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

import logging
import os
import shutil
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any

os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

warnings.filterwarnings("ignore", message=".*torchao.*")
warnings.filterwarnings("ignore", message=".*xformers.*")
logging.getLogger("xformers").setLevel(logging.ERROR)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

from diffusers.utils import logging as _diffusers_logging  # isort: skip  # noqa: E402

_diffusers_logging.set_verbosity(50)

from gamedev_shared.gpu import clear_cuda_memory  # noqa: E402
from gamedev_shared.logging import Logger  # noqa: E402
from gamedev_shared.sdnq import is_available as _sdnq_available  # noqa: E402

from . import defaults as _defaults  # noqa: E402
from .hy3d21_paths import (  # noqa: E402
    default_cfg_yaml,
    ensure_hy3dpaint_on_path,
    ensure_realesrgan_ckpt,
    resolve_hy3dpaint_root,
)
from .utils.mesh_io import load_mesh_trimesh, save_glb  # noqa: E402

_logger = Logger()


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


def _apply_paint_multi_gpu(
    pipe: Any,
    gpu_ids: list[int],
    verbose: bool = False,
) -> None:
    primary_dev = f"cuda:{gpu_ids[0]}"
    secondary_dev = f"cuda:{gpu_ids[1]}"

    inner_mv = pipe.models.get("multiview_model")
    if inner_mv is None or not hasattr(inner_mv, "pipeline"):
        raise RuntimeError("Multiview model not loaded — cannot apply multi-GPU")

    diff_pipe = inner_mv.pipeline

    diff_pipe.unet.to(primary_dev)
    diff_pipe.vae.to(secondary_dev)
    if hasattr(diff_pipe, "text_encoder") and diff_pipe.text_encoder is not None:
        diff_pipe.text_encoder.to(secondary_dev)

    diff_pipe._multi_gpu_primary = primary_dev
    _orig_exec_device_prop = type(diff_pipe)._execution_device

    def _patched_exec_device(self: Any) -> torch.device:
        if hasattr(self, "_multi_gpu_primary"):
            return torch.device(self._multi_gpu_primary)
        return _orig_exec_device_prop.fget(self)

    type(diff_pipe)._execution_device = property(_patched_exec_device)

    inner_mv.device = primary_dev
    if hasattr(pipe, "config") and hasattr(pipe.config, "device"):
        pipe.config.device = primary_dev

    if verbose:
        gpu0 = torch.cuda.get_device_name(gpu_ids[0])
        gpu1 = torch.cuda.get_device_name(gpu_ids[1])
        unet_mem = sum(p.numel() * p.element_size() for p in diff_pipe.unet.parameters()) / (1024**3)
        vae_mem = sum(p.numel() * p.element_size() for p in diff_pipe.vae.parameters()) / (1024**3)
        _logger.info(
            f"Multi-GPU:\n"
            f"  {primary_dev} ({gpu0}): UNet ({unet_mem:.2f} GB)\n"
            f"  {secondary_dev} ({gpu1}): VAE ({vae_mem:.2f} GB)"
        )


def _get_combined_bounds(objects: list) -> tuple[np.ndarray, np.ndarray]:
    """AABB combinado ``(min_corner, max_corner)`` de uma lista de objectos bpy."""
    from gamedev_shared.bpy_mesh import get_bounds

    all_mins = [np.array(get_bounds(o)[0]) for o in objects]
    all_maxs = [np.array(get_bounds(o)[1]) for o in objects]
    if not all_mins:
        return np.zeros(3), np.zeros(3)
    return np.min(all_mins, axis=0), np.max(all_maxs, axis=0)


def _apply_translation(objects: list, offset: np.ndarray) -> None:
    """Desloca vértices de todos os objectos (in-place)."""
    for obj in objects:
        for v in obj.data.vertices:
            v.co[0] += float(offset[0])
            v.co[1] += float(offset[1])
            v.co[2] += float(offset[2])
        obj.data.update()


def _apply_rotation_x(objects: list, angle_rad: float) -> None:
    """Roda objectos em torno do eixo X (in-place, sobre os vértices).

    Usado para reaplicar a convenção Y-up quando o pipeline de pintura
    devolve a mesh em Z-up (Hunyuan default). Operamos sobre os vértices
    (não na transform do objecto) para que o GLB exportado já chegue na
    orientação certa, sem depender de matrizes de nó.
    """
    if abs(angle_rad) < 1e-9:
        return
    c = float(np.cos(angle_rad))
    s = float(np.sin(angle_rad))
    for obj in objects:
        for v in obj.data.vertices:
            y = float(v.co[1])
            z = float(v.co[2])
            v.co[1] = c * y - s * z
            v.co[2] = s * y + c * z
        obj.data.update()


def _bounds_axis_swap_x_rad(
    before_min: np.ndarray,
    before_max: np.ndarray,
    after_min: np.ndarray,
    after_max: np.ndarray,
    *,
    tol: float = 0.05,
) -> float:
    """Detecta swap Y↔Z entre input e output, comparando AABBs.

    Devolve o ângulo (em radianos) a aplicar em torno de X para repor a
    convenção do input. ``+π/2`` se o output está em Z-up vs input Y-up,
    ``-π/2`` no caso contrário, ``0.0`` se as dimensões coincidem.

    Heurística: para um asset de personagem, a dimensão "altura" é a
    maior das três (ou pelo menos não a menor). Se a altura está em Y no
    input mas em Z no output (ou vice-versa), houve rotação.
    """
    extent_before = np.asarray(before_max) - np.asarray(before_min)
    extent_after = np.asarray(after_max) - np.asarray(after_min)
    if (
        np.any(extent_before <= tol) or np.any(extent_after <= tol)
    ):
        return 0.0
    # Razão entre eixos Y/Z em ambos os AABBs. Se inverteu, houve swap.
    yz_before = extent_before[1] / extent_before[2]
    yz_after = extent_after[1] / extent_after[2]
    # Toleramos ±10% (mesh de saída pode ter pequenas variações por remesh).
    if yz_before > 1.0 and yz_after < 1.0 and abs(yz_before - 1.0 / yz_after) < max(yz_before, 1.0 / yz_after) * 0.30:
        # Input era Y-tall, output é Z-tall → Hunyuan rodou +90° em X
        # ((0,1,0)→(0,0,1)); para repor a convenção Y-up rodamos -90° em X.
        return float(-np.pi / 2.0)
    if yz_before < 1.0 and yz_after > 1.0 and abs(1.0 / yz_before - yz_after) < max(1.0 / yz_before, yz_after) * 0.30:
        return float(np.pi / 2.0)
    return 0.0


def apply_hunyuan_paint(
    mesh: Any,
    image: str | Path | Image.Image,
    *,
    model_repo: str = _defaults.DEFAULT_PAINT_HF_REPO,
    subfolder: str = _defaults.DEFAULT_PAINT_SUBFOLDER,
    paint_cpu_offload: bool = _defaults.DEFAULT_PAINT_CPU_OFFLOAD,
    max_num_view: int = _defaults.DEFAULT_PAINT_MAX_VIEWS,
    view_resolution: int = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION,
    render_size: int | None = None,
    texture_size: int | None = None,
    bake_exp: int = _defaults.DEFAULT_PAINT_BAKE_EXP,
    use_remesh: bool = False,
    verbose: bool = False,
    enable_vae_slicing: bool = _defaults.DEFAULT_ENABLE_VAE_SLICING,
    enable_vae_tiling: bool = _defaults.DEFAULT_ENABLE_VAE_TILING,
    vae_tile_size: int = _defaults.DEFAULT_VAE_TILE_SIZE,
    preserve_origin: bool = True,
    low_vram: bool = _defaults.DEFAULT_LOW_VRAM,
    gpu_ids: list[int] | None = None,
) -> Any:
    """
    Aplica Hunyuan3D-Paint 2.1: mesh + imagem de referência → mesh com UV e textura/PBR (GLB).

    Por defeito corre em alta precisão (FP16, sem quantização, render 2048, texture 4096).
    Com ``low_vram=True`` ativa quantização SDNQ uint8 e resoluções reduzidas (1024/2048).

    Com ``preserve_origin=True`` (padrão), a mesh texturizada preserva a posição
    original: o centroide do AABB da saída é alinhado ao do input, corrigindo qualquer
    renormalização interna do pipeline de pintura.
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
        _logger.info(
            f"hy3dpaint={hy3dpaint_root}\n"
            f"  repo={model_repo} weights_subfolder={subfolder} offload={paint_cpu_offload} "
            f"max_views={max_num_view} res={view_resolution}"
        )

    clear_cuda_memory()

    with tempfile.TemporaryDirectory(prefix="paint3d_h21_") as td_raw:
        tdir = Path(td_raw)
        mesh_in = tdir / "input_mesh.glb"
        ref_path = tdir / "ref.png"
        out_obj = tdir / "textured_mesh.glb"
        out_glb = tdir / "textured_mesh.glb"

        with profile_span("paint_prepare_io"):
            bounds_min_before, bounds_max_before = _get_combined_bounds(mesh)
            if verbose:
                _logger.info(
                    f"input AABB (antes do pipeline): min={bounds_min_before.tolist()} max={bounds_max_before.tolist()}"
                )
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

            if render_size is not None:
                config.render_size = render_size
            elif low_vram:
                config.render_size = _defaults.LOW_VRAM_RENDER_SIZE
            else:
                config.render_size = _defaults.DEFAULT_PAINT_RENDER_SIZE

            if texture_size is not None:
                config.texture_size = texture_size
            elif low_vram:
                config.texture_size = _defaults.LOW_VRAM_TEXTURE_SIZE
            else:
                config.texture_size = _defaults.DEFAULT_PAINT_TEXTURE_SIZE

            if not torch.cuda.is_available():
                config.render_size = min(config.render_size, 1024)
                config.texture_size = min(config.texture_size, 2048)

            config.bake_exp = bake_exp

            if not low_vram:
                config.quantization_config = {"type": "none"}

        with profile_span("paint_load_pipeline"):
            pipe = Hunyuan3DPaintPipeline(config)

        with profile_span("paint_optimize_pipeline"):
            try:
                if low_vram and _sdnq_available() and pipe.unet is not None:
                    from gamedev_shared.sdnq import quantize_model

                    if verbose:
                        _logger.info("Modo low-VRAM: aplicando SDNQ uint8 ao UNet (dequantize_fp32=False)...")
                    pipe.unet = quantize_model(pipe.unet, preset="sdnq-uint8", dequantize_fp32=False)
                elif verbose:
                    if low_vram:
                        _logger.warn("Modo low-VRAM: SDNQ indisponível — UNet em FP16/qint8")
                    else:
                        _logger.info("Modo alta VRAM — UNet em FP16 (sem quantização)")
                if pipe.vae is not None:
                    enable_vae_optimizations(
                        pipe.vae,
                        enable_slicing=enable_vae_slicing,
                        enable_tiling=enable_vae_tiling,
                        tile_sample_min_size=vae_tile_size,
                    )
                    if verbose and enable_vae_tiling:
                        _logger.info(f"VAE tiling ativo (tile_size={vae_tile_size})")

                # --- Multi-GPU component placement (see _apply_paint_multi_gpu) ---
                multi_gpu_env = os.environ.get("PAINT3D_MULTI_GPU", "").strip()
                if multi_gpu_env in ("1", "true", "yes"):
                    import warnings

                    warnings.warn(
                        "PAINT3D_MULTI_GPU está obsoleto — use --gpu-ids (ex: --gpu-ids 0,1).",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    if gpu_ids is None and torch.cuda.device_count() >= 2:
                        gpu_ids = [0, 1]

                if gpu_ids and len(gpu_ids) >= 2 and not low_vram:
                    _apply_paint_multi_gpu(pipe, gpu_ids, verbose=verbose)
                elif torch.cuda.device_count() >= 2 and not low_vram and verbose:
                    gpu0_name = torch.cuda.get_device_name(0)
                    gpu1_name = torch.cuda.get_device_name(1)
                    _logger.info(
                        f"Multi-GPU disponível: cuda:0 ({gpu0_name}), "
                        f"cuda:1 ({gpu1_name}). Usar --gpu-ids 0,1 para activar."
                    )
            except Exception as e:
                if verbose:
                    _logger.warn(f"Aviso: otimizações opcionais falharam: {e}")

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

    if not textured or not all(hasattr(o, "data") and getattr(o, "type", "") == "MESH" for o in textured):
        raise TypeError(f"Paint devolveu tipos inesperados: {[type(o) for o in textured]}")

    if preserve_origin:
        bounds_min_after, bounds_max_after = _get_combined_bounds(textured)

        # 1) Detectar swap Y↔Z (Hunyuan paint exporta em Z-up; nós queremos
        # Y-up — convenção aplicada no shape pelo text3d generate).
        angle_x = _bounds_axis_swap_x_rad(
            bounds_min_before, bounds_max_before,
            bounds_min_after, bounds_max_after,
        )
        if abs(angle_x) > 1e-9:
            _apply_rotation_x(textured, angle_x)
            if verbose:
                _logger.info(
                    f"orientação reposta: rot_x={angle_x:.4f} rad "
                    f"(extent_before_yz={(bounds_max_before-bounds_min_before)[1:].tolist()}, "
                    f"extent_after_yz={(bounds_max_after-bounds_min_after)[1:].tolist()})"
                )
            # Recalcula bounds após rotação para que o offset translacional
            # use os números corretos.
            bounds_min_after, bounds_max_after = _get_combined_bounds(textured)

        # 2) Realinhar centroide ao input.
        centroid_before = (bounds_min_before + bounds_max_before) * 0.5
        centroid_after = (bounds_min_after + bounds_max_after) * 0.5
        offset = centroid_before - centroid_after
        if np.dot(offset, offset) > 1e-12:
            _apply_translation(textured, offset)
            if verbose:
                _logger.info(
                    f"origin corrigido: offset={offset.tolist()} "
                    f"(antes={centroid_before.tolist()}, depois_pintura={centroid_after.tolist()})"
                )

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
    render_size: int | None = None,
    texture_size: int | None = None,
    bake_exp: int | None = None,
    use_remesh: bool = False,
    verbose: bool = False,
    enable_vae_slicing: bool = _defaults.DEFAULT_ENABLE_VAE_SLICING,
    enable_vae_tiling: bool = _defaults.DEFAULT_ENABLE_VAE_TILING,
    vae_tile_size: int = _defaults.DEFAULT_VAE_TILE_SIZE,
    preserve_origin: bool = True,
    low_vram: bool = _defaults.DEFAULT_LOW_VRAM,
    gpu_ids: list[int] | None = None,
) -> Path:
    """Atalho: carrega mesh, pinta com Hunyuan3D-Paint 2.1 (PBR baked), exporta GLB."""
    repo = model_repo or _defaults.DEFAULT_PAINT_HF_REPO
    sub = subfolder or _defaults.DEFAULT_PAINT_SUBFOLDER
    offload = _defaults.DEFAULT_PAINT_CPU_OFFLOAD if paint_cpu_offload is None else paint_cpu_offload
    if max_num_view is None:
        nviews = _defaults.LOW_VRAM_MAX_VIEWS if low_vram else _defaults.DEFAULT_PAINT_MAX_VIEWS
    else:
        nviews = max_num_view
    if view_resolution is None:
        vres = _defaults.LOW_VRAM_VIEW_RESOLUTION if low_vram else _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
    else:
        vres = view_resolution
    bexp = _defaults.DEFAULT_PAINT_BAKE_EXP if bake_exp is None else bake_exp

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
        render_size=render_size,
        texture_size=texture_size,
        bake_exp=bexp,
        use_remesh=use_remesh,
        verbose=verbose,
        enable_vae_slicing=enable_vae_slicing,
        enable_vae_tiling=enable_vae_tiling,
        vae_tile_size=vae_tile_size,
        preserve_origin=preserve_origin,
        low_vram=low_vram,
        gpu_ids=gpu_ids,
    )

    output_path = Path(output_path)
    with profile_span("paint_save_glb"):
        save_glb(out, output_path)
    return output_path


class PaintBatchProcessor:
    """Context manager: carrega Hunyuan3D-Paint uma vez, pinta múltiplas meshes.

    Mantém o pipeline carregado entre chamadas para evitar o custo de
    inicialização (~30-60s) em cada item de um batch.

    Uso::

        with PaintBatchProcessor(verbose=True) as proc:
            for mesh, img in items:
                textured = proc.paint_mesh(mesh, img)
    """

    def __init__(
        self,
        *,
        model_repo: str = _defaults.DEFAULT_PAINT_HF_REPO,
        subfolder: str = _defaults.DEFAULT_PAINT_SUBFOLDER,
        paint_cpu_offload: bool = _defaults.DEFAULT_PAINT_CPU_OFFLOAD,
        max_num_view: int = _defaults.DEFAULT_PAINT_MAX_VIEWS,
        view_resolution: int = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION,
        render_size: int | None = None,
        texture_size: int | None = None,
        bake_exp: int = _defaults.DEFAULT_PAINT_BAKE_EXP,
        use_remesh: bool = False,
        verbose: bool = False,
        enable_vae_slicing: bool = _defaults.DEFAULT_ENABLE_VAE_SLICING,
        enable_vae_tiling: bool = _defaults.DEFAULT_ENABLE_VAE_TILING,
        vae_tile_size: int = _defaults.DEFAULT_VAE_TILE_SIZE,
        preserve_origin: bool = True,
        low_vram: bool = _defaults.DEFAULT_LOW_VRAM,
        gpu_ids: list[int] | None = None,
    ):
        self._model_repo = model_repo
        self._subfolder = subfolder
        self._paint_cpu_offload = paint_cpu_offload
        self._max_num_view = max_num_view
        self._view_resolution = view_resolution
        self._render_size = render_size
        self._texture_size = texture_size
        self._bake_exp = bake_exp
        self._use_remesh = use_remesh
        self._verbose = verbose
        self._enable_vae_slicing = enable_vae_slicing
        self._enable_vae_tiling = enable_vae_tiling
        self._vae_tile_size = vae_tile_size
        self._preserve_origin = preserve_origin
        self._low_vram = low_vram
        self._gpu_ids = gpu_ids
        self._pipe: Any = None
        self._config: Any = None

    def __enter__(self) -> PaintBatchProcessor:
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

        if self._verbose:
            _logger.info(
                f"[batch] hy3dpaint={hy3dpaint_root}\n"
                f"  repo={self._model_repo} weights_subfolder={self._subfolder} "
                f"offload={self._paint_cpu_offload} max_views={self._max_num_view} "
                f"res={self._view_resolution}"
            )

        clear_cuda_memory()

        with profile_span("paint_configure"):
            config = Hunyuan3DPaintConfig(self._max_num_view, self._view_resolution)
            config.multiview_pretrained_path = self._model_repo
            config.multiview_weights_subfolder = self._subfolder
            config.multiview_cfg_path = str(cfg_yaml)
            config.realesrgan_ckpt_path = str(ckpt_path)

            if torch.cuda.is_available():
                config.device = "cuda"
            else:
                config.device = "cpu"

            if self._render_size is not None:
                config.render_size = self._render_size
            elif self._low_vram:
                config.render_size = _defaults.LOW_VRAM_RENDER_SIZE
            else:
                config.render_size = _defaults.DEFAULT_PAINT_RENDER_SIZE

            if self._texture_size is not None:
                config.texture_size = self._texture_size
            elif self._low_vram:
                config.texture_size = _defaults.LOW_VRAM_TEXTURE_SIZE
            else:
                config.texture_size = _defaults.DEFAULT_PAINT_TEXTURE_SIZE

            if not torch.cuda.is_available():
                config.render_size = min(config.render_size, 1024)
                config.texture_size = min(config.texture_size, 2048)

            config.bake_exp = self._bake_exp

            if not self._low_vram:
                config.quantization_config = {"type": "none"}

        with profile_span("paint_load_pipeline"):
            pipe = Hunyuan3DPaintPipeline(config)

        with profile_span("paint_optimize_pipeline"):
            try:
                if self._low_vram and _sdnq_available() and pipe.unet is not None:
                    from gamedev_shared.sdnq import quantize_model

                    if self._verbose:
                        _logger.info("[batch] Modo low-VRAM: aplicando SDNQ uint8 ao UNet (dequantize_fp32=False)...")
                    pipe.unet = quantize_model(pipe.unet, preset="sdnq-uint8", dequantize_fp32=False)
                elif self._verbose:
                    if self._low_vram:
                        _logger.warn("[batch] Modo low-VRAM: SDNQ indisponível — UNet em FP16/qint8")
                    else:
                        _logger.info("[batch] Modo alta VRAM — UNet em FP16 (sem quantização)")
                if pipe.vae is not None:
                    enable_vae_optimizations(
                        pipe.vae,
                        enable_slicing=self._enable_vae_slicing,
                        enable_tiling=self._enable_vae_tiling,
                        tile_sample_min_size=self._vae_tile_size,
                    )
                    if self._verbose and self._enable_vae_tiling:
                        _logger.info(f"[batch] VAE tiling ativo (tile_size={self._vae_tile_size})")

                if self._gpu_ids and len(self._gpu_ids) >= 2 and not self._low_vram:
                    _apply_paint_multi_gpu(pipe, self._gpu_ids, verbose=self._verbose)
                elif torch.cuda.device_count() >= 2 and not self._low_vram and self._verbose:
                    gpu0_name = torch.cuda.get_device_name(0)
                    gpu1_name = torch.cuda.get_device_name(1)
                    _logger.info(
                        f"[batch] Multi-GPU disponível: cuda:0 ({gpu0_name}), "
                        f"cuda:1 ({gpu1_name}). Usar --gpu-ids 0,1 para activar."
                    )
            except Exception as e:
                if self._verbose:
                    _logger.warn(f"[batch] Aviso: otimizações opcionais falharam: {e}")

        self._pipe = pipe
        self._config = config
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._pipe is not None:
            del self._pipe
            self._pipe = None
        self._config = None
        clear_cuda_memory()

    def paint_mesh(self, mesh: Any, image: str | Path | Image.Image, *, step_callback=None) -> Any:
        """Pinta uma mesh usando o pipeline carregado. Mesh + imagem → objectos bpy texturizados."""
        from gamedev_shared.profiler import profile_span

        if self._pipe is None:
            raise RuntimeError("Pipeline não inicializado — use `with PaintBatchProcessor(...) as p:`")

        with tempfile.TemporaryDirectory(prefix="paint3d_batch_") as td_raw:
            tdir = Path(td_raw)
            mesh_in = tdir / "input_mesh.glb"
            ref_path = tdir / "ref.png"
            out_obj = tdir / "textured_mesh.glb"
            out_glb = tdir / "textured_mesh.glb"

            with profile_span("paint_batch_prepare_io"):
                bounds_min_before, bounds_max_before = _get_combined_bounds(mesh)
                save_glb(mesh, mesh_in)
                if isinstance(image, (str, Path)):
                    shutil.copy2(image, ref_path)
                else:
                    im = image.convert("RGB") if image.mode != "RGB" else image
                    im.save(ref_path)

            with profile_span("paint_batch_inference", sync_cuda=True), torch.no_grad():
                self._pipe(
                    mesh_path=str(mesh_in),
                    image_path=str(ref_path),
                    output_mesh_path=str(out_obj),
                    use_remesh=self._use_remesh,
                    save_glb=True,
                    step_callback=step_callback,
                )

            if not out_glb.is_file():
                raise FileNotFoundError(f"Paint 2.1 não gerou GLB esperado: {out_glb}")

            textured = load_mesh_trimesh(out_glb)

        if not textured or not all(hasattr(o, "data") and getattr(o, "type", "") == "MESH" for o in textured):
            raise TypeError(f"Paint devolveu tipos inesperados: {[type(o) for o in textured]}")

        if self._preserve_origin:
            bounds_min_after, bounds_max_after = _get_combined_bounds(textured)
            # 1) Repor convenção Y-up se Hunyuan paint devolveu Z-up.
            angle_x = _bounds_axis_swap_x_rad(
                bounds_min_before, bounds_max_before,
                bounds_min_after, bounds_max_after,
            )
            if abs(angle_x) > 1e-9:
                _apply_rotation_x(textured, angle_x)
                bounds_min_after, bounds_max_after = _get_combined_bounds(textured)
            # 2) Realinhar centroide.
            centroid_before = (bounds_min_before + bounds_max_before) * 0.5
            centroid_after = (bounds_min_after + bounds_max_after) * 0.5
            offset = centroid_before - centroid_after
            if np.dot(offset, offset) > 1e-12:
                _apply_translation(textured, offset)

        return textured
