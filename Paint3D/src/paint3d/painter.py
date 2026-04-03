"""
Textura com Hunyuan3D-Paint 2.1 (``hy3dpaint.textureGenPipeline.Hunyuan3DPaintPipeline``).

Código vendored em ``paint3d.hy3dpaint`` (de Tencent-Hunyuan/Hunyuan3D-2.1).
Pesos em Hugging Face (``tencent/Hunyuan3D-2.1``, pasta ``hunyuan3d-paintpbr-v2-1``),
descarregados sob demanda via ``huggingface_hub.snapshot_download``.
Checkpoint Real-ESRGAN em ``hy3dpaint/ckpt/RealESRGAN_x4plus.pth``.

O rasterizador CUDA é fornecido por **nvdiffrast** (NVIDIA), registado como
``custom_rasterizer`` em ``sys.modules`` antes de importar o renderer 2.1.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import torch
import trimesh
from PIL import Image

from gamedev_shared.gpu import clear_cuda_memory
from gamedev_shared.sdnq import is_available as _sdnq_available

from . import defaults as _defaults
from .hy3d21_paths import (
    default_cfg_yaml,
    ensure_hy3dpaint_on_path,
    ensure_realesrgan_ckpt,
    resolve_hy3dpaint_root,
)
from .utils.mesh_io import load_mesh_trimesh, save_glb


def _ensure_custom_rasterizer_shim() -> None:
    """Regista o shim nvdiffrast como ``custom_rasterizer`` se o módulo nativo não existir."""
    if "custom_rasterizer" in sys.modules:
        return
    try:
        import custom_rasterizer  # noqa: F401 - extensão nativa já instalada

        return
    except (ImportError, ModuleNotFoundError, OSError):
        pass

    from paint3d import custom_rasterizer_shim

    sys.modules["custom_rasterizer"] = custom_rasterizer_shim  # type: ignore[assignment]


def check_paint_rasterizer_available() -> None:
    """Garante que ``custom_rasterizer`` está disponível (shim nvdiffrast ou extensão nativa)."""
    _ensure_custom_rasterizer_shim()
    try:
        import custom_rasterizer  # noqa: F401
    except (ImportError, ModuleNotFoundError, OSError) as e:
        raise RuntimeError(
            "Rasterizador indisponível: nem nvdiffrast nem custom_rasterizer foram encontrados.\n"
            "Instala nvdiffrast: pip install git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation\n"
            "Ou compila custom_rasterizer (ver PAINT_SETUP.md)."
        ) from e


def check_hunyuan3d21_environment() -> tuple[bool, str]:
    """Verifica código vendored e peso Real-ESRGAN. Devolve (ok, mensagem)."""
    root = resolve_hy3dpaint_root()
    if not (root / "textureGenPipeline.py").is_file():
        return False, f"Código hy3dpaint em falta: {root / 'textureGenPipeline.py'}"
    cfg = default_cfg_yaml()
    if not cfg.is_file():
        return False, f"Config em falta: {cfg}"
    return True, str(root)


def apply_hunyuan_paint(
    mesh: trimesh.Trimesh,
    image: str | Path | Image.Image,
    *,
    model_repo: str = _defaults.DEFAULT_PAINT_HF_REPO,
    subfolder: str = _defaults.DEFAULT_PAINT_SUBFOLDER,
    paint_cpu_offload: bool = _defaults.DEFAULT_PAINT_CPU_OFFLOAD,
    max_num_view: int = _defaults.DEFAULT_PAINT_MAX_VIEWS,
    view_resolution: int = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION,
    use_remesh: bool = True,
    verbose: bool = False,
    enable_vae_slicing: bool = _defaults.DEFAULT_ENABLE_VAE_SLICING,
    enable_vae_tiling: bool = _defaults.DEFAULT_ENABLE_VAE_TILING,
    vae_tile_size: int = _defaults.DEFAULT_VAE_TILE_SIZE,
) -> trimesh.Trimesh:
    """
    Aplica Hunyuan3D-Paint 2.1: mesh + imagem de referência → mesh com UV e textura/PBR (GLB).

    SDNQ uint8 é aplicado automaticamente ao UNet quando disponível (``gamedev_shared.sdnq``).
    Sem SDNQ, o pipeline usa qint8 pré-quantizado do upstream ou FP16.
    """
    from gamedev_shared.profiler import profile_span
    from gamedev_shared.quantization import enable_vae_optimizations

    with profile_span("paint_check_env"):
        check_paint_rasterizer_available()

        ok, msg = check_hunyuan3d21_environment()
        if not ok:
            raise RuntimeError(msg)

        hy3dpaint_root = ensure_hy3dpaint_on_path()
        cfg_yaml = default_cfg_yaml()
        ckpt_path = ensure_realesrgan_ckpt()

    from .hy3dpaint.textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline

    if verbose:
        print(
            f"[Paint 2.1] hy3dpaint={hy3dpaint_root}\n"
            f"  repo={model_repo} weights_subfolder={subfolder} offload={paint_cpu_offload} "
            f"max_views={max_num_view} res={view_resolution}"
        )

    clear_cuda_memory()

    with tempfile.TemporaryDirectory(prefix="paint3d_h21_") as td_raw:
        tdir = Path(td_raw)
        mesh_in = tdir / "input_mesh.glb"
        ref_path = tdir / "ref.png"
        out_obj = tdir / "textured_mesh.obj"
        out_glb = tdir / "textured_mesh.glb"

        with profile_span("paint_prepare_io"):
            save_glb(mesh, mesh_in)

            if isinstance(image, (str, Path)):
                shutil.copy2(image, ref_path)
            else:
                im = image.convert("RGB") if image.mode != "RGB" else image
                im.save(ref_path)

        with profile_span("paint_configure"):
            config = Hunyuan3DPaintConfig(max_num_view, view_resolution)
            config.multiview_pretrained_path = model_repo
            config.multiview_weights_subfolder = subfolder
            config.multiview_cfg_path = str(cfg_yaml)
            config.realesrgan_ckpt_path = str(ckpt_path)

            if torch.cuda.is_available():
                config.device = "cuda"
            else:
                config.device = "cpu"

            if paint_cpu_offload and torch.cuda.is_available():
                config.render_size = 1024
                config.texture_size = 2048
            elif not torch.cuda.is_available():
                config.render_size = min(config.render_size, 1024)
                config.texture_size = min(config.texture_size, 2048)

        with profile_span("paint_load_pipeline"):
            pipe = Hunyuan3DPaintPipeline(config)

        with profile_span("paint_optimize_pipeline"):
            try:
                if _sdnq_available() and pipe.unet is not None:
                    from gamedev_shared.sdnq import quantize_model

                    if verbose:
                        print("[Paint 2.1] Aplicando SDNQ uint8 ao UNet (dequantize_fp32=False)...")
                    pipe.unet = quantize_model(pipe.unet, preset="sdnq-uint8", dequantize_fp32=False)
                elif verbose:
                    print("[Paint 2.1] SDNQ indisponivel — UNet em FP16/qint8")
                if pipe.vae is not None:
                    enable_vae_optimizations(
                        pipe.vae,
                        enable_slicing=enable_vae_slicing,
                        enable_tiling=enable_vae_tiling,
                        tile_sample_min_size=vae_tile_size,
                    )
                    if verbose and enable_vae_tiling:
                        print(f"[Paint 2.1] VAE tiling ativo (tile_size={vae_tile_size})")
            except Exception as e:
                if verbose:
                    print(f"[Paint 2.1] Aviso: otimizações opcionais falharam: {e}")

        with profile_span("paint_inference", sync_cuda=True):
            try:
                with torch.no_grad():
                    pipe(
                        mesh_path=str(mesh_in),
                        image_path=str(ref_path),
                        output_mesh_path=str(out_obj),
                        use_remesh=use_remesh,
                        save_glb=True,
                    )
            finally:
                del pipe
                clear_cuda_memory()

        if not out_glb.is_file():
            raise FileNotFoundError(f"Paint 2.1 não gerou GLB esperado: {out_glb}")

        textured = load_mesh_trimesh(out_glb)

    if not isinstance(textured, trimesh.Trimesh):
        raise TypeError(f"Paint devolveu {type(textured)}, esperado Trimesh")

    return textured


def paint_file_to_file(
    mesh_path: str | Path,
    image_path: str | Path,
    output_path: str | Path,
    *,
    model_repo: str | None = None,
    subfolder: str | None = None,
    paint_cpu_offload: bool | None = None,
    max_num_view: int | None = None,
    view_resolution: int | None = None,
    use_remesh: bool = True,
    verbose: bool = False,
    enable_vae_slicing: bool = _defaults.DEFAULT_ENABLE_VAE_SLICING,
    enable_vae_tiling: bool = _defaults.DEFAULT_ENABLE_VAE_TILING,
    vae_tile_size: int = _defaults.DEFAULT_VAE_TILE_SIZE,
) -> Path:
    """Atalho: carrega mesh, pinta com Hunyuan3D-Paint 2.1 (PBR baked), exporta GLB."""
    repo = model_repo or _defaults.DEFAULT_PAINT_HF_REPO
    sub = subfolder or _defaults.DEFAULT_PAINT_SUBFOLDER
    offload = _defaults.DEFAULT_PAINT_CPU_OFFLOAD if paint_cpu_offload is None else paint_cpu_offload
    nviews = _defaults.DEFAULT_PAINT_MAX_VIEWS if max_num_view is None else max_num_view
    vres = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION if view_resolution is None else view_resolution

    from gamedev_shared.profiler import profile_span

    with profile_span("paint_load_mesh"):
        mesh = load_mesh_trimesh(mesh_path)
    out = apply_hunyuan_paint(
        mesh,
        image_path,
        model_repo=repo,
        subfolder=sub,
        paint_cpu_offload=offload,
        max_num_view=nviews,
        view_resolution=vres,
        use_remesh=use_remesh,
        verbose=verbose,
        enable_vae_slicing=enable_vae_slicing,
        enable_vae_tiling=enable_vae_tiling,
        vae_tile_size=vae_tile_size,
    )

    output_path = Path(output_path)
    with profile_span("paint_save_glb"):
        save_glb(out, output_path)
    return output_path
