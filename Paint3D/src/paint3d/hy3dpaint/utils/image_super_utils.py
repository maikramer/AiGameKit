# Hunyuan 3D is licensed under the TENCENT HUNYUAN NON-COMMERCIAL LICENSE AGREEMENT
# except for the third-party components listed below.
# Hunyuan 3D does not impose any additional limitations beyond what is outlined
# in the repsective licenses of these third-party components.
# Users must comply with all terms and conditions of original licenses of these third-party
# components and must ensure that the usage of the third party components adheres to
# all relevant laws and regulations.

# For avoidance of doubts, Hunyuan 3D means the large language models and
# their software and algorithms, including trained model weights, parameters (including
# optimizer states), machine-learning model code, inference-enabling code, training-enabling code,
# fine-tuning enabling code and other elements of the foregoing made publicly available
# by Tencent in accordance with TENCENT HUNYUAN COMMUNITY LICENSE AGREEMENT.

import numpy as np
import torch
from PIL import Image


class imageSuperNet:
    """Real-ESRGAN x4 com device configurável.

    ``config.realesrgan_device`` ("cpu" | "cuda" | "cuda:N"): em GPU usa fp16 +
    tiling (``config.realesrgan_tile``) para limitar o pico de VRAM — ordens de
    grandeza mais rápido que CPU para as 2×N vistas do enhance. Em OOM cai de
    volta para CPU fp32 automaticamente e mantém-se lá.
    """

    def __init__(self, config) -> None:
        self._ckpt_path = config.realesrgan_ckpt_path
        self._device = str(getattr(config, "realesrgan_device", "cpu"))
        self._tile = int(getattr(config, "realesrgan_tile", 512))
        self.upsampler = self._build(self._device)

    def _build(self, device_str: str):
        from paint3d.hy3dpaint.utils.realesrgan_infer import RealESRGANer
        from paint3d.hy3dpaint.utils.rrdbnet_arch_standalone import RRDBNet

        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        use_cuda = device_str.startswith("cuda")
        return RealESRGANer(
            scale=4,
            model_path=self._ckpt_path,
            dni_weight=None,
            model=model,
            tile=self._tile if use_cuda else 0,
            tile_pad=10,
            pre_pad=0,
            half=use_cuda,
            device=torch.device(device_str),
            gpu_id=None,
        )

    def __call__(self, image):
        try:
            output, _ = self.upsampler.enhance(np.array(image))
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            self._device = "cpu"
            self.upsampler = self._build("cpu")
            output, _ = self.upsampler.enhance(np.array(image))
        output = Image.fromarray(output)
        return output
