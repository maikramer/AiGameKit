"""
Tencent is pleased to support the open source community by making Tencent Hunyuan 3D Omni available.

Copyright (C) 2025 Tencent.  All rights reserved. The below software and/or models in this
distribution may have been modified by Tencent ("Tencent Modifications"). All Tencent Modifications
are Copyright (C) Tencent.

Tencent Hunyuan 3D Omni is licensed under the TENCENT HUNYUAN 3D OMNI COMMUNITY LICENSE AGREEMENT
except for the third-party components listed below, which is licensed under different terms.
Tencent Hunyuan 3D Omni does not impose any additional limitations beyond what is outlined in the
respective licenses of these third-party components. Users must comply with all terms and conditions
of original licenses of these third-party components and must ensure that the usage of the third party
components adheres to all relevant laws and regulations.

For avoidance of doubts, Tencent Hunyuan 3D Omni means training code, inference-enabling code, parameters,
and/or weights of this Model, which are made publicly available by Tencent in accordance with TENCENT
HUNYUAN 3D OMNI COMMUNITY LICENSE AGREEMENT.
"""

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from hy3dshape.models.utils.misc import instantiate_from_config
from torch import Tensor
from torchvision import transforms
from transformers import Dinov2Model


def fps(
    src: torch.Tensor,
    batch: Tensor | None = None,
    ratio: Tensor | float | None = None,
    random_start: bool = True,
    batch_size: int | None = None,
    ptr: Tensor | list[int] | None = None,
):
    src = src.float()
    from torch_cluster import fps as fps_fn

    output = fps_fn(src, batch, ratio, random_start, batch_size, ptr)
    return output


class PositionEmbeddingSine(nn.Module):
    """
    This is a more standard version of the position embedding, very similar to the one
    used by the Attention is all you need paper, generalized to work on images.
    """

    def __init__(self, num_pos_feats=64, temperature=10000, normalize=False, scale=None):
        super().__init__()
        self.num_pos_feats = num_pos_feats
        self.temperature = temperature
        self.normalize = normalize
        if scale is not None and normalize is False:
            raise ValueError("normalize should be True if scale is passed")
        if scale is None:
            scale = 2 * math.pi
        self.scale = scale

    def forward(self, x: torch.Tensor):
        mask = torch.zeros_like(x).bool()
        assert mask is not None
        not_mask = ~mask
        y_embed = not_mask.cumsum(1, dtype=torch.float32)
        x_embed = not_mask.cumsum(2, dtype=torch.float32)
        if self.normalize:
            eps = 1e-6
            y_embed = y_embed / (y_embed[:, -1:, :] + eps) * self.scale
            x_embed = x_embed / (x_embed[:, :, -1:] + eps) * self.scale

        dim_t = torch.arange(self.num_pos_feats, dtype=torch.float32, device=x.device)
        dim_t = self.temperature ** (2 * (dim_t // 2) / self.num_pos_feats)

        pos_x = x_embed[:, :, :, None] / dim_t
        pos_y = y_embed[:, :, :, None] / dim_t
        pos_x = torch.stack((pos_x[:, :, :, 0::2].sin(), pos_x[:, :, :, 1::2].cos()), dim=4).flatten(3)
        pos_y = torch.stack((pos_y[:, :, :, 0::2].sin(), pos_y[:, :, :, 1::2].cos()), dim=4).flatten(3)
        pos = torch.cat((pos_y, pos_x), dim=3)
        return pos


class DinoImageEncoder(nn.Module):
    def __init__(
        self,
        version="facebook/dinov2-large",
        trainable=False,
        image_size=224,
        use_cls_token=True,
        use_pos_embed=False,
        zero_out_background=False,
        mask_resize_mode="bicubic",
        **kwargs,
    ):
        super().__init__()
        self.model = Dinov2Model.from_pretrained(version)
        self.use_cls_token = use_cls_token
        self.use_pos_embed = use_pos_embed
        self.zero_out_background = zero_out_background
        self.mask_resize_mode = mask_resize_mode
        self.image_size = image_size
        self.setup_transform(image_size)
        if not trainable:
            self.model.eval()
            self.model.requires_grad_(False)

        if self.use_pos_embed:
            self.pos_embed = PositionEmbeddingSine(self.model.config.hidden_size // 2)

        if self.zero_out_background and self.use_pos_embed:
            raise ValueError("Cannot use zero_out_background and use_pos_embed at the same time")

    def setup_transform(self, image_size):
        print(f"Image size: {image_size}")
        self.transform = transforms.Compose(
            [
                transforms.Resize(image_size, transforms.InterpolationMode.BILINEAR, antialias=True),
                transforms.CenterCrop(image_size),  # crop a (224, 224) square
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ]
        )
        self.size = image_size // 14
        self.patch_nums = (image_size // 14) ** 2
        if self.use_cls_token:
            self.patch_nums += 1

    def expand_mask_to_bbox(self, masks):
        bs = masks.shape[0]
        expanded_masks = torch.zeros_like(masks)
        for i in range(bs):
            mask = masks[i, 0]
            non_zero_indices = torch.nonzero(mask, as_tuple=False)
            if non_zero_indices.numel() > 0:
                y_min, x_min = torch.min(non_zero_indices, dim=0)[0]
                y_max, x_max = torch.max(non_zero_indices, dim=0)[0]
                expanded_masks[i, 0, y_min : y_max + 1, x_min : x_max + 1] = 1.0
        return expanded_masks

    def forward(self, image, dropout_mask=None, value_range=(-1, 1), mask=None):
        if value_range is not None:
            low, high = value_range
            image = (image - low) / (high - low)

        inputs = self.transform(image)
        outputs = self.model(inputs)

        last_hidden_state = outputs.last_hidden_state
        if not self.use_cls_token:
            last_hidden_state = last_hidden_state[:, 1:, :]

        if self.use_pos_embed:
            if self.use_cls_token:
                raise NotImplementedError
            B, N, C = last_hidden_state.shape
            pos_embed = self.pos_embed(last_hidden_state[:, :, 0].reshape(B, self.size, self.size))
            pos_embed = pos_embed.reshape(B, N, C)
            last_hidden_state = last_hidden_state + pos_embed.to(
                last_hidden_state.device, dtype=last_hidden_state.dtype
            )

        if self.zero_out_background:
            if mask is None:
                assert self.training is False, "mask should be provided in training mode"
                print("Warning: mask is not provided, use mask compute from image")
                image_np = image.detach().cpu().numpy()
                image_np = np.transpose(image_np, (0, 2, 3, 1))[0]
                image_np = (image_np * 255).astype(np.uint8)
                mask = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
                mask = (mask < 250).astype(np.uint8)
                mask = cv2.resize(mask, (self.size, self.size), interpolation=cv2.INTER_NEAREST)
                mask = torch.from_numpy(mask).to(self.model.device, dtype=torch.bool)
            else:
                mask = (mask > 0) * 1.0
                if self.mask_resize_mode == "maxpooling":
                    mask = F.interpolate(mask, size=(self.image_size, self.image_size), mode="nearest")
                    mask = F.max_pool2d(mask, kernel_size=14, stride=14)
                elif self.mask_resize_mode == "bbox":
                    mask = self.expand_mask_to_bbox(mask)
                    mask = F.interpolate(mask, size=(self.image_size, self.image_size), mode="nearest")
                    mask = F.max_pool2d(mask, kernel_size=14, stride=14)
                else:
                    mask = F.interpolate(mask, size=(self.size, self.size), mode=self.mask_resize_mode)

            mask = mask.to(dtype=last_hidden_state.dtype)
            mask_flatten = mask.reshape(mask.shape[0], -1, 1)
            if not self.use_cls_token:
                last_hidden_state = last_hidden_state * mask_flatten
            else:
                new_hidden_state = last_hidden_state[:, 1:, :]
                new_hidden_state = new_hidden_state * mask_flatten
                last_hidden_state = torch.cat([last_hidden_state[:, 0:1, :], new_hidden_state], dim=1)

        outputs = {"dino": {"last_hidden_state": last_hidden_state}}

        if dropout_mask is not None:
            outputs = self.maskout(outputs, dropout_mask)

        return outputs

    def maskout(self, outputs, mask):
        bsz = mask.shape[0]
        mask = mask.reshape(bsz, 1, 1).to(self.model.device)
        mask = torch.logical_not(mask)
        last_hidden_state = outputs["dino"]["last_hidden_state"] * mask
        return {
            "dino": {
                "last_hidden_state": last_hidden_state,
            }
        }

    def unconditional_embedding(self, batch_size):
        device = next(self.model.parameters()).device
        dtype = next(self.model.parameters()).dtype
        zero = torch.zeros(
            batch_size,
            self.patch_nums,
            self.model.config.hidden_size,
            device=device,
            dtype=dtype,
        )
        return {
            "dino": {
                "last_hidden_state": zero,
            }
        }


class ModLN(nn.Module):
    def __init__(self, inner_dim: int, mod_dim: int = 1024):
        super().__init__()
        self.mlp = nn.Sequential(
            nn.SiLU(),
            nn.Linear(mod_dim, inner_dim * 2),
        )

        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.zeros_(m.weight)
                nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor, condition: torch.Tensor):
        """
        x: [N, M, C_in], M: num of tokens
        condition: [N, C_mod]
        """
        shift, scale = self.mlp(condition).unsqueeze(1).chunk(2, dim=-1)
        return x * (1 + scale) + shift


class DinoEncoder(nn.Module):
    def __init__(
        self,
        dino_image_encoder_version,
        drop_image_dino_rate=0.0,
    ):
        super().__init__()
        self.dino_image_encoder = DinoImageEncoder(version=dino_image_encoder_version)
        self.drop_image_dino_rate = drop_image_dino_rate
        self.disable_drop = False

    def forward(self, image, text):
        outputs = {}

        if self.disable_drop:
            dino_mask = None
        else:
            random_p = torch.rand(len(image), device="cuda")
            dino_mask = random_p < self.drop_image_dino_rate

        dino_outputs = self.dino_image_encoder(image, dropout_mask=dino_mask)
        outputs.update(dino_outputs)

        return outputs

    def unconditional_embedding(self, batch_size):
        outputs = {}

        dino_outputs = self.dino_image_encoder.unconditional_embedding(batch_size)
        outputs.update(dino_outputs)

        return outputs


class SingleImageEncoder(nn.Module):
    def __init__(
        self,
        image_encoder,
        drop_ratio=0.0,
    ):
        super().__init__()
        self.image_encoder = instantiate_from_config(image_encoder)
        self.drop_ratio = drop_ratio
        self.disable_drop = False

    def setup(self, image_size=224):
        if hasattr(self.image_encoder, "setup_transform"):
            self.image_encoder.setup_transform(image_size=image_size)

    def forward(self, image=None, text=None, mask=None):
        if self.disable_drop:
            dropout_mask = None
        else:
            random_p = torch.rand(len(image), device="cuda")
            dropout_mask = random_p < self.drop_ratio

        outputs = self.image_encoder(image, dropout_mask=dropout_mask, mask=mask)
        return outputs

    def unconditional_embedding(self, batch_size):
        outputs = self.image_encoder.unconditional_embedding(batch_size)
        return outputs


class OmniEncoder(nn.Module):
    def __init__(
        self,
        image_encoder,
        image_size=224,
        resolutions=[512, 1024, 2048],
        voxel_resolution=16,
        width=1024,
        re_sample=True,
        random_noise=False,
        noise_ratio=0.0,
        noise_scales=[0.0],
        drop_point=False,
        drop_ratio=0.0,
        num_freqs=8,
        include_pi=True,
    ):
        super().__init__()
        self.drop_ratio = drop_ratio
        self.disable_drop = False
        self.image_encoder = instantiate_from_config(image_encoder)
        self.image_encoder.eval()
        self.image_encoder.requires_grad_(False)

        self.cond_signal_embedding = nn.Embedding(4, 8)
        self.cond_signal_linear = nn.Linear(8, width)

        # parameter for point
        self.voxel_resolution = voxel_resolution
        self.resolutions = resolutions
        self.random_noise = random_noise
        self.noise_scales = noise_scales
        self.noise_ratio = noise_ratio
        self.re_sample = re_sample
        self.drop_point = drop_point
        self.drop_ratio = drop_ratio
        from ..modules.embedder import FourierEmbedder

        self.pe = FourierEmbedder(num_freqs=num_freqs, include_pi=include_pi)
        self.linear = nn.Sequential(nn.Linear(self.pe.get_dims(6), width), nn.RMSNorm(width), nn.GELU())

    def setup(self, image_size=224):
        if hasattr(self.image_encoder, "setup_transform"):
            self.image_encoder.setup_transform(image_size=image_size)

    def generate_voxel(self, pc):
        """
        量化点云到 16*16*16 grid 上
        """
        device, dtype = pc.device, pc.dtype
        B, N, D = pc.shape
        assert D == 3, "点云维度应为3"  # pc: -1 ~ 1

        resolution = self.voxel_resolution
        points_norm = (pc + 1) / 2  # (B, N, 3) 0 ~ 1
        voxels = (points_norm * resolution).floor().long()  # (B, N, 3)
        voxels = torch.clamp(voxels, 0, resolution - 1)

        sampled_voxels_batch = []
        for b in range(B):
            vox_b = voxels[b]  # (N, 3)
            linear_idx = vox_b[:, 0] + vox_b[:, 1] * resolution + vox_b[:, 2] * resolution * resolution
            unique_idx = torch.unique(linear_idx)

            # 转回三维 voxel idx
            z = unique_idx // (resolution * resolution)
            y = (unique_idx % (resolution * resolution)) // resolution
            x = unique_idx % resolution
            unique_voxels = torch.stack([x, y, z], dim=1).float()  # (M, 3)

            # mapping back to [-1,1]，get center of voxel
            # range of voxel idx is [0, resolution-1]
            # voxel size = 2 / resolution
            voxel_size = 2.0 / resolution
            voxel_centers = unique_voxels * voxel_size + voxel_size / 2 - 1  # (M, 3)
            sampled_voxels_batch.append(voxel_centers)

        # padding to same length for batch process
        max_voxels = max([v.shape[0] for v in sampled_voxels_batch])
        padded_voxels = []
        for v in sampled_voxels_batch:
            pad_len = max_voxels - v.shape[0]
            if pad_len > 0:
                pad = torch.zeros(pad_len, 3, device=pc.device, dtype=pc.dtype)
                v = torch.cat([v, pad], dim=0)
            padded_voxels.append(v.unsqueeze(0))  # (1, max_voxels, 3)

        sampled_voxels = torch.cat(padded_voxels, dim=0)  # (B, max_voxels, 3)

        return sampled_voxels.to(device=device, dtype=dtype)

    def bbox_to_corners(self, bbox):
        """
        PyTorch 版本：将 bbox (B,1,3) 转换为 8 个角点坐标（范围[-1,1])

        参数:
            bbox: torch.Tensor, shape (B,1,3), 分别表示 [length, height, width] (范围 0~1)

        返回:
            corners: torch.Tensor, shape (B,8,3), 每个bbox的8个角点xyz坐标
        """
        B = bbox.shape[0]
        half_dims = bbox / 2  # (B,1,3)

        signs = torch.tensor(
            [[1, 1, 1], [1, 1, -1], [1, -1, 1], [1, -1, -1], [-1, 1, 1], [-1, 1, -1], [-1, -1, 1], [-1, -1, -1]],
            dtype=torch.float32,
            device=bbox.device,
        )  # (8,3)

        corners = half_dims * signs.unsqueeze(0)  # (B,8,3)

        return corners

    def forward(self, image, surface=None, mask=None, pose=None, bbox=None, point=None, voxel=None, **kwargs):
        # Alinhar device ao input (cpu_offload / group_offload pode deixar pipe.device=cpu
        # enquanto image_encoder corre em CUDA).
        if isinstance(image, torch.Tensor):
            drop_device = image.device
        elif isinstance(image, (list, tuple)) and image and isinstance(image[0], torch.Tensor):
            drop_device = image[0].device
        else:
            drop_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        if self.disable_drop:
            dropout_mask = None
        else:
            random_p = torch.rand(len(image), device=drop_device)
            dropout_mask = random_p < self.drop_ratio

        image_cond = self.image_encoder(image, dropout_mask=dropout_mask, mask=mask)["dino"]["last_hidden_state"]
        # cpu_offload / group_offload: image_encoder pode estar em CUDA enquanto
        # self.linear / pe / cond_signal_* ficam em CPU. Correr o ramo de controlo
        # no device dos pesos locais; alinhar ao image_cond só no cat final.
        try:
            mod_device = next(self.linear.parameters()).device
            mod_dtype = next(self.linear.parameters()).dtype
        except StopIteration:
            mod_device = image_cond.device
            mod_dtype = image_cond.dtype

        def _cat_cond(geom_tokens, signal_id: int, sampled):
            cond_signal = self.cond_signal_embedding(torch.tensor([signal_id], device=mod_device))
            cond_signal = self.cond_signal_linear(cond_signal)
            cond_signal = cond_signal.unsqueeze(0).repeat(len(image), 10, 1)
            geom_tokens = geom_tokens.to(device=image_cond.device, dtype=image_cond.dtype)
            cond_signal = cond_signal.to(device=image_cond.device, dtype=image_cond.dtype)
            return torch.cat([image_cond, geom_tokens, cond_signal], dim=1), sampled.to(
                device=image_cond.device, dtype=image_cond.dtype
            )

        if pose is not None:
            pose = pose.to(device=mod_device, dtype=mod_dtype)
            geom = self.linear(self.pe(pose))
            cond, sampled_point = _cat_cond(geom, 0, pose[..., :3])

        elif bbox is not None:
            bbox = bbox.to(device=mod_device, dtype=mod_dtype)
            geom = self.linear(self.pe(bbox.repeat(1, 1, 2)))
            corners = self.bbox_to_corners(bbox)
            cond, sampled_point = _cat_cond(geom, 1, corners)

        elif voxel is not None:
            voxel = voxel.to(device=mod_device, dtype=mod_dtype)
            voxel = self.generate_voxel(voxel[..., :3])
            geom = self.linear(self.pe(voxel.repeat(1, 1, 2)))
            cond, sampled_point = _cat_cond(geom, 2, voxel[..., :3])

        elif point is not None:
            point = point.to(device=mod_device, dtype=mod_dtype)
            geom = self.linear(self.pe(point.repeat(1, 1, 2)))
            cond, sampled_point = _cat_cond(geom, 3, point[..., :3])
        else:
            raise ValueError("pose, bbox, voxel, point must be one of them")

        outputs = {"cond": cond, "cond_point": sampled_point}
        return outputs

    def unconditional_embedding(self, batch_size):
        outputs = self.image_encoder.unconditional_embedding(batch_size)
        return outputs
