"""Geração multiview 3D via Tencent Hunyuan3D-2mv (FP16 sequencial em ~2.8 GiB VRAM)
e ponte de fusão ``2mv → bbox exato → Hunyuan3D-Omni --control-type point``.

Em GPUs de 6 GB (ex. RTX 4050 5.72 GiB), cada etapa entra e sai da VRAM em sequência:
``BiRefNet`` → ``DinoImageEncoderMV`` (~2.1 GiB) → ``Hunyuan3DDiT`` (~2.1 GiB) →
``ShapeVAE`` v2.0 (~0.4 GiB). Pico medido: ~2.76 GiB alocados (~3.26 GiB reservados)
em ~98 s a ``octree_resolution=384`` sem qualquer quantização.
"""

from __future__ import annotations

import gc
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import trimesh
import yaml
from PIL import Image

from aigamekit_shared.logging import Logger

from .turnaround import prepare_multiview_images
from .utils.bg_removal import BiRefNetBGRemover, crop_to_content, has_meaningful_alpha
from .utils.memory import clear_cuda_memory as _clear_cuda_cache

_logger = Logger()

DEFAULT_2MV_HF_REPO = "tencent/Hunyuan3D-2mv"
DEFAULT_2MV_SUBFOLDER = "hunyuan3d-dit-v2-mv"
DEFAULT_2MV_STEPS = 50
DEFAULT_2MV_GUIDANCE = 5.5
DEFAULT_2MV_OCTREE = 384
DEFAULT_2MV_CHUNKS = 20000

_VENDOR_ROOT = Path(__file__).resolve().parent / "hy3dshape" / "hy2mv_vendor"


def _ensure_hy2mv_vendor_on_path() -> None:
    root_s = str(_VENDOR_ROOT)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


def remap_dinov2_v5_state_dict(
    state_dict: dict[str, torch.Tensor],
    expected_keys: set[str] | list[str] | tuple[str, ...],
) -> dict[str, torch.Tensor]:
    """Remapeia chaves DINOv2 (transformers 4.x) para ``Dinov2Model`` do transformers 5.x.

    Converte ``attention.attention.{query,key,value}`` → ``attention.{q,k,v}_proj``,
    ``attention.output.dense`` → ``attention.o_proj``, ``mlp.weights_in`` → split
    ``mlp.gate_proj`` + ``mlp.up_proj`` (ordem SwiGLU ``silu(x1) * x2``), e
    ``mlp.weights_out`` → ``mlp.down_proj``. No-op se as chaves já coincidirem.
    """
    exp = set(expected_keys)
    needs_remap = any(".attention.q_proj." in k for k in exp) and any(
        ".attention.attention.query." in k for k in state_dict
    )
    if not needs_remap:
        return state_dict

    out: dict[str, torch.Tensor] = {}
    for k, v in state_dict.items():
        if ".attention.attention.query." in k:
            out[k.replace(".attention.attention.query.", ".attention.q_proj.")] = v
        elif ".attention.attention.key." in k:
            out[k.replace(".attention.attention.key.", ".attention.k_proj.")] = v
        elif ".attention.attention.value." in k:
            out[k.replace(".attention.attention.value.", ".attention.v_proj.")] = v
        elif ".attention.output.dense." in k:
            out[k.replace(".attention.output.dense.", ".attention.o_proj.")] = v
        elif ".mlp.weights_in." in k:
            gate, up = v.chunk(2, dim=0)
            out[k.replace(".mlp.weights_in.", ".mlp.gate_proj.")] = gate.contiguous()
            out[k.replace(".mlp.weights_in.", ".mlp.up_proj.")] = up.contiguous()
        elif ".mlp.weights_out." in k:
            out[k.replace(".mlp.weights_out.", ".mlp.down_proj.")] = v
        else:
            out[k] = v
    return out


def fit_mesh_to_bbox_aspect(
    mesh: trimesh.Trimesh,
    target_lhw: list[float] | tuple[float, float, float],
) -> trimesh.Trimesh:
    """Ajusta o aspect ratio da malha Y-up para ``[L(X), H(Y), W(Z)]`` preservando centro/pés.

    Usado na ponte ``Hunyuan3D-2mv → Hunyuan3D-Omni --control-type point``: como
    ``point_cloud_tensor`` normaliza isotropicamente pelo eixo maior, ajustar o aspect
    da malha guia codifica topologia multiview + dimensões exatas num único sinal.
    """
    if len(target_lhw) != 3:
        raise ValueError(f"target_lhw espera 3 floats [L, H, W], recebeu {len(target_lhw)}")
    tgt = np.asarray([float(x) for x in target_lhw], dtype=np.float64)
    if float(tgt.min()) <= 0.0:
        raise ValueError(f"target_lhw deve ter valores positivos, recebeu {target_lhw!r}")
    tgt = tgt / float(tgt.max())

    m = mesh.copy()
    v = np.asarray(m.vertices, dtype=np.float64)
    bmin, bmax = v.min(axis=0), v.max(axis=0)
    cur = np.maximum(bmax - bmin, 1e-6)
    cur_norm = cur / float(cur.max())
    center = 0.5 * (bmin + bmax)
    scale_xyz = tgt / cur_norm
    v = (v - center) * scale_xyz + center
    m.vertices = v
    return m


def _canonicalize_yup(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Garante convenção Y-up (+Z frente) idêntica à saída crua do Hunyuan3D-Omni."""
    m = mesh.copy()
    ext = m.extents
    if float(ext[2]) > float(ext[1]) * 1.15:
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2.0, [1, 0, 0]))
    return m


class Hunyuan2MVGenerator:
    """Gerador multiview ``Hunyuan3D-2mv`` com ciclo de vida sequencial na GPU (~2.8 GiB VRAM)."""

    def __init__(
        self,
        *,
        device: str | None = None,
        repo_id: str = DEFAULT_2MV_HF_REPO,
        subfolder: str = DEFAULT_2MV_SUBFOLDER,
        cache_dir: str | None = None,
        verbose: bool = False,
    ) -> None:
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.repo_id = repo_id
        self.subfolder = subfolder
        self.cache_dir = cache_dir
        self.verbose = verbose
        self.last_stats: dict[str, Any] = {}

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    def _resolve_weights(self) -> tuple[Path, Path]:
        from huggingface_hub import hf_hub_download

        cfg = Path(
            hf_hub_download(
                self.repo_id,
                f"{self.subfolder}/config.yaml",
                cache_dir=self.cache_dir,
            )
        )
        ckpt = Path(
            hf_hub_download(
                self.repo_id,
                f"{self.subfolder}/model.fp16.safetensors",
                cache_dir=self.cache_dir,
            )
        )
        return cfg, ckpt

    def generate_from_views(
        self,
        views: dict[str, str | Path | Image.Image],
        *,
        num_inference_steps: int = DEFAULT_2MV_STEPS,
        guidance_scale: float = DEFAULT_2MV_GUIDANCE,
        octree_resolution: int = DEFAULT_2MV_OCTREE,
        num_chunks: int = DEFAULT_2MV_CHUNKS,
        seed: int | None = 42,
        remove_bg: bool = True,
        align_back: bool = True,
        lock_outer_hands: bool = True,
        hierarchical_decode: bool = True,
    ) -> trimesh.Trimesh:
        """Gera mesh 3D Y-up a partir de 2-4 vistas ortográficas (``front``, ``back``, ``left``, ``right``).

        Convenção oficial Tencent Hunyuan3D-2mv:
        - ``front``: frente (0°);
        - ``left``: lado esquerdo anatómico visível, frente apontando para ESQUERDA (``<-``);
        - ``back``: costas (180°);
        - ``right``: lado direito anatómico visível, frente apontando para DIREITA (``->``).
        """
        if "front" not in views:
            raise ValueError("Hunyuan3D-2mv exige pelo menos a vista 'front'")
        valid_keys = {"front", "left", "back", "right"}
        bad = set(views) - valid_keys
        if bad:
            raise ValueError(f"Vistas desconhecidas para Hunyuan3D-2mv: {sorted(bad)} (válidas: {sorted(valid_keys)})")

        _ensure_hy2mv_vendor_on_path()
        import safetensors.torch
        from hy3dgen.shapegen.pipelines import export_to_trimesh, instantiate_from_config, retrieve_timesteps

        t0 = time.perf_counter()
        use_cuda = self.device.startswith("cuda") and torch.cuda.is_available()
        exec_dev = self.device if use_cuda else "cpu"
        exec_dtype = torch.float16 if use_cuda else torch.float32

        loaded_views, align_meta = prepare_multiview_images(
            views,
            align_back=align_back,
            lock_outer_hands=lock_outer_hands,
        )
        if "back_align" in align_meta:
            ba = align_meta["back_align"]
            self._log(
                f"2mv turnaround align (front↔back): full_iou={ba['full_iou']:.3f}, hands_iou={ba['hands_iou']:.3f}"
            )

        # 1. Remoção de fundo (BiRefNet) → unload antes do conditioner
        clean_views: dict[str, Image.Image] = {}
        if remove_bg:
            remover: BiRefNetBGRemover | None = None
            try:
                for k, im in loaded_views.items():
                    if has_meaningful_alpha(im):
                        clean_views[k] = crop_to_content(im)
                    else:
                        if remover is None:
                            remover = BiRefNetBGRemover(device=exec_dev)
                        clean_views[k] = crop_to_content(remover.remove_background(im))
            finally:
                if remover is not None:
                    remover.unload()
                    del remover
                gc.collect()
                _clear_cuda_cache()
        else:
            clean_views = loaded_views

        # 2. Carregar checkpoint 2mv em CPU (4.59 GiB → particionado por submódulo)
        cfg_path, ckpt_path = self._resolve_weights()
        with open(cfg_path, encoding="utf-8") as fh:
            config = yaml.safe_load(fh)

        self._log(f"A carregar Hunyuan3D-2mv ({ckpt_path.name})...")
        st = safetensors.torch.load_file(str(ckpt_path), device="cpu")
        parts: dict[str, dict[str, torch.Tensor]] = {}
        for k, v in st.items():
            head, tail = k.split(".", 1)
            parts.setdefault(head, {})[tail] = v
        del st

        image_processor = instantiate_from_config(config["image_processor"])
        scheduler = instantiate_from_config(config["scheduler"])

        # 3. Fase 1 GPU: DinoImageEncoderMV (~2.1 GiB FP16)
        conditioner = instantiate_from_config(config["conditioner"])
        cond_sd = remap_dinov2_v5_state_dict(parts.pop("conditioner"), conditioner.state_dict().keys())
        conditioner.load_state_dict(cond_sd, strict=True)
        del cond_sd
        conditioner.to(device=exec_dev, dtype=exec_dtype).eval()

        cond_inputs = image_processor(clean_views)
        img_tensor = cond_inputs.pop("image").to(device=exec_dev, dtype=exec_dtype)
        view_idxs = [cond_inputs["view_idxs"]]
        with torch.inference_mode():
            cond = conditioner(image=img_tensor, view_idxs=view_idxs)
            uncond = conditioner.unconditional_embedding(batch_size=1, view_idxs=view_idxs)
            cond_all = {"main": torch.cat([cond["main"], uncond["main"]], dim=0)}

        conditioner.to("cpu")
        del conditioner, img_tensor, cond, uncond
        gc.collect()
        _clear_cuda_cache()

        # 4. Fase 2 GPU: Hunyuan3DDiT (~2.1 GiB FP16 totalmente residente)
        model = instantiate_from_config(config["model"])
        model.load_state_dict(parts.pop("model"), strict=True)
        model.to(device=exec_dev, dtype=exec_dtype).eval()

        num_latents = int(config["vae"]["params"]["num_latents"])
        embed_dim = int(config["vae"]["params"]["embed_dim"])
        scale_factor = float(config["vae"]["params"]["scale_factor"])

        gen = torch.Generator(device=exec_dev)
        if seed is not None:
            gen.manual_seed(int(seed))
        latents = torch.randn((1, num_latents, embed_dim), generator=gen, device=exec_dev, dtype=exec_dtype)
        sigmas = np.linspace(0.0, 1.0, int(num_inference_steps))
        timesteps, _ = retrieve_timesteps(scheduler, int(num_inference_steps), exec_dev, sigmas=sigmas)

        t_diff = time.perf_counter()
        with torch.inference_mode():
            for t in timesteps:
                latent_in = torch.cat([latents, latents], dim=0)
                t_tensor = t.expand(2).to(latents.dtype) / scheduler.config.num_train_timesteps
                noise_pred = model(latent_in, t_tensor, cond_all)
                pred_c, pred_u = noise_pred.chunk(2)
                noise_pred = pred_u + float(guidance_scale) * (pred_c - pred_u)
                latents = scheduler.step(noise_pred, t, latents).prev_sample
        dt_diff = time.perf_counter() - t_diff

        model.to("cpu")
        del model, cond_all
        gc.collect()
        _clear_cuda_cache()

        # 5. Fase 3 GPU: ShapeVAE v2.0 (~0.4 GiB FP16)
        vae = instantiate_from_config(config["vae"])
        vae.load_state_dict(parts.pop("vae"), strict=False)
        vae.to(device=exec_dev, dtype=exec_dtype).eval()
        if hierarchical_decode:
            vae.enable_flashvdm_decoder(enabled=True, adaptive_kv_selection=False, mc_algo="mc")

        t_dec = time.perf_counter()
        with torch.inference_mode():
            latents = (1.0 / scale_factor) * latents
            latents = vae(latents)
            outputs = vae.latents2mesh(
                latents,
                bounds=1.01,
                mc_level=0.0,
                num_chunks=int(num_chunks),
                octree_resolution=int(octree_resolution),
                mc_algo="mc",
                enable_pbar=self.verbose,
            )
        dt_dec = time.perf_counter() - t_dec

        raw_mesh = export_to_trimesh(outputs)[0]
        vae.to("cpu")
        del vae, latents, parts
        gc.collect()
        _clear_cuda_cache()

        mesh = _canonicalize_yup(raw_mesh)
        dt_total = time.perf_counter() - t0
        self.last_stats = {
            "seconds": round(dt_total, 2),
            "diff_seconds": round(dt_diff, 2),
            "decode_seconds": round(dt_dec, 2),
            "verts": len(mesh.vertices),
            "faces": len(mesh.faces),
            "views": sorted(clean_views.keys()),
            **align_meta,
        }
        self._log(
            f"Hunyuan3D-2mv concluído em {dt_total:.1f}s "
            f"(diff={dt_diff:.1f}s, decode={dt_dec:.1f}s, F={len(mesh.faces):,})"
        )
        return mesh


def fuse_2mv_with_omni(
    mesh_2mv: trimesh.Trimesh,
    front_image: str | Path | Image.Image,
    *,
    omni_generator: Any,
    target_bbox_lhw: list[float] | tuple[float, float, float] | None = None,
    point_count: int = 2048,
    num_inference_steps: int = 50,
    guidance_scale: float = 4.5,
    octree_resolution: int = 384,
    num_chunks: int = 20000,
    hy_seed: int | None = 42,
    mc_level: float | str = "auto",
    remove_bg: bool = True,
) -> trimesh.Trimesh:
    """Fusão sequencial ``Hunyuan3D-2mv → bbox fit → Hunyuan3D-Omni --control-type point``.

    Ideal para edifícios, portões, mobiliário e props com ``bbox`` anisotrópico rígido:
    1. Opcionalmente ajusta o aspect ratio da malha 2mv para ``target_bbox_lhw`` ``[L, H, W]``.
    2. Amostra ``point_count`` (default 2048, in-distribution no ``OmniEncoder``) pontos de
       superfície num ``.ply`` temporário.
    3. Executa ``omni_generator.generate_from_image(..., control_type="point")`` obtendo os
       chanfros ``sharpedge`` de 4096 latentes do Omni preservando a topologia multiview do 2mv.
    """
    guide_mesh = fit_mesh_to_bbox_aspect(mesh_2mv, target_bbox_lhw) if target_bbox_lhw is not None else mesh_2mv
    pts, _ = trimesh.sample.sample_surface(guide_mesh, int(point_count), seed=int(hy_seed or 0))

    with tempfile.TemporaryDirectory(prefix="hy2mv_omni_") as tmp_dir:
        ply_path = Path(tmp_dir) / "guide_2mv.ply"
        trimesh.points.PointCloud(np.asarray(pts, dtype=np.float64)).export(str(ply_path))
        return omni_generator.generate_from_image(
            front_image,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            octree_resolution=octree_resolution,
            num_chunks=num_chunks,
            hy_seed=hy_seed,
            mc_level=mc_level,
            remove_bg=remove_bg,
            control_type="point",
            point_cloud=ply_path,
        )
