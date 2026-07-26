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


def _env_flag(name: str, default: bool) -> bool:
    """Override booleano via env: "0/false/no/off" desliga, "1/true/yes/on" liga."""
    v = os.environ.get(name, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def _auto_dino_device(memory_efficient: bool, gpu_ids: list[int] | None) -> str:
    """DINO-giant (~2.2 GB fp16): GPU só quando há folga de VRAM.

    Em CPU corre fp32 (fp16 em CPU não tem kernels nativos). Multi-GPU →
    device secundário (junto do VAE). Single-GPU precisa de >= DINO_GPU_MIN_GIB.
    """
    if memory_efficient or not torch.cuda.is_available():
        return "cpu"
    if gpu_ids and len(gpu_ids) >= 2:
        return f"cuda:{gpu_ids[1]}"
    from gamedev_shared.hardware import GIB, cuda_gpu_specs

    largest = max((mem for _, mem in cuda_gpu_specs()), default=0)
    return "cuda" if largest / GIB >= _defaults.DINO_GPU_MIN_GIB else "cpu"


def _auto_esrgan_device(memory_efficient: bool, gpu_ids: list[int] | None) -> str:
    """Real-ESRGAN (~64 MB): GPU sempre que houver CUDA — tiling limita o pico
    de VRAM e o imageSuperNet cai para CPU automaticamente em OOM."""
    if not torch.cuda.is_available():
        return "cpu"
    if gpu_ids and len(gpu_ids) >= 2 and not memory_efficient:
        return f"cuda:{gpu_ids[1]}"
    return "cuda"


def _preflight_paint_model(model_repo: str, subfolder: str, *, verbose: bool = False) -> None:
    """Garante os pesos do Hunyuan3D-Paint em disco antes de construir o pipeline.

    Download com resume/progresso, restrito ao subfolder de paint (não baixa shape/dit).
    Best-effort: se falhar (offline mas em cache, ou hub indisponível), o pipeline trata
    do download on-demand como antes.
    """
    try:
        from gamedev_shared.model_download import ensure_model

        ensure_model(
            model_repo,
            allow_patterns=[f"{subfolder}/*", "*.json", "*.yaml"],
            on_status=(lambda m: _logger.info(f"preflight: {m}")) if verbose else None,
        )
    except Exception as exc:
        if verbose:
            _logger.info(f"preflight paint falhou ({exc}); pipeline baixa on-demand")


def _apply_optimization_config(config: Any, *, memory_efficient: bool, gpu_ids: list[int] | None) -> None:
    """Anexa knobs de otimização ao Hunyuan3DPaintConfig.

    - cfg_batch_chunking: CFG uncond/ref/full em 3 forwards sequenciais B=1
      (pico de ativações ÷3, matemática idêntica). Default: ligado em modo memory-efficient.
      Env: PAINT3D_CFG_CHUNKING.
    - offload_ref_unet: ref-UNet (dual stream) → CPU após o 1º step de cada
      pintura (liberta ~1.7 GB fp16). Default: ligado em modo memory-efficient.
      Env: PAINT3D_OFFLOAD_REF_UNET.
    - dino_device / realesrgan_device: colocação automática por VRAM.
      Env: PAINT3D_DINO_DEVICE / PAINT3D_ESRGAN_DEVICE.
    """
    config.cfg_batch_chunking = _env_flag("PAINT3D_CFG_CHUNKING", memory_efficient)
    config.offload_ref_unet = _env_flag("PAINT3D_OFFLOAD_REF_UNET", memory_efficient)
    config.dino_device = os.environ.get("PAINT3D_DINO_DEVICE", "").strip() or _auto_dino_device(
        memory_efficient, gpu_ids
    )
    config.realesrgan_device = os.environ.get("PAINT3D_ESRGAN_DEVICE", "").strip() or _auto_esrgan_device(
        memory_efficient, gpu_ids
    )
    config.realesrgan_tile = _defaults.ESRGAN_TILE_MEMORY_EFFICIENT if memory_efficient else _defaults.ESRGAN_TILE


def _log_optimization_config(config: Any, prefix: str = "") -> None:
    _logger.info(
        f"{prefix}Otimizações: cfg_chunking={config.cfg_batch_chunking} "
        f"offload_ref_unet={config.offload_ref_unet} dino={config.dino_device} "
        f"esrgan={config.realesrgan_device} (tile={config.realesrgan_tile}) "
        f"sage_attn={os.environ.get('PAINT3D_USE_SAGEATTN', '0') == '1'}"
    )


def _park_ref_unet_on_cpu(pipe: Any, *, verbose: bool = False) -> bool:
    """Move ``unet_dual`` (ref stream) para CPU e liberta cache CUDA.

    ``pipeline.to(cuda)`` carrega os dois UNets; o offload_ref_unet só actuava
    *após* o 1º step de denoise — tarde demais para MeshRender (cudaMalloc
    ~50-200 MiB no load_mesh). Com dual estacionado, ~1.7 GiB ficam livres.
    O forward já faz ``unet_dual.to(device)`` sob demanda.
    """
    mv = getattr(pipe, "models", {}).get("multiview_model") if pipe is not None else None
    p = getattr(mv, "pipeline", None) if mv is not None else None
    unet = getattr(p, "unet", None) if p is not None else None
    if unet is None:
        return False
    dual = getattr(unet, "unet_dual", None)
    if dual is None:
        return False
    try:
        unet.offload_ref_unet = True
        dual.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if verbose:
            _logger.info("runtime budget: unet_dual → CPU (MeshRender headroom)")
        return True
    except Exception as exc:
        if verbose:
            _logger.warn(f"runtime budget: park unet_dual falhou: {exc}")
        return False


def ensure_meshrender_vram_headroom(pipe: Any, *, verbose: bool = False) -> None:
    """Garante margem livre para MeshRender; levanta RuntimeError se impossível.

    Nunca deixar cair em cudaMalloc OOM silencioso — falha clara + tip UMS.
    Em GPUs folgadas (já ≥ mínimo) não mexe no dual-UNet.
    """
    from gamedev_shared.vram_budget import PAINT_MESHRENDER_MIN_FREE_BYTES, free_vram_bytes

    if not torch.cuda.is_available():
        return
    free_b = free_vram_bytes()
    if free_b is None:
        return
    if free_b >= PAINT_MESHRENDER_MIN_FREE_BYTES:
        return
    _park_ref_unet_on_cpu(pipe, verbose=verbose)
    torch.cuda.empty_cache()
    free_b = free_vram_bytes() or 0
    if free_b >= PAINT_MESHRENDER_MIN_FREE_BYTES:
        return
    free_mib = free_b / (1024 * 1024)
    need_mib = PAINT_MESHRENDER_MIN_FREE_BYTES / (1024 * 1024)
    raise RuntimeError(
        f"VRAM insuficiente para MeshRender: {free_mib:.0f} MiB livres "
        f"(mínimo {need_mib:.0f} MiB). Evicta backends (ums evict) ou reduz "
        f"views/texture; sem fallback que OOMa a GPU."
    )


def apply_runtime_vram_budget(
    config: Any,
    pipe: Any,
    *,
    requested_views: int,
    requested_resolution: int,
    memory_efficient: bool,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Orça views/tiles/DINO pela VRAM livre **após** load/offload.

    Canónico: :func:`gamedev_shared.vram_budget.paint_runtime_budget` (também
    exposto no UMS via ``modelserver.runtime_budget``). Desligar:
    ``PAINT3D_AUTO_VRAM_BUDGET=0``.

    Com ``offload_ref_unet`` (mem_eff / VRAM apertada), estaciona ``unet_dual``
    em CPU **antes** do MeshRender — evita OOM com ~34 MiB livres.
    """
    if not _env_flag("PAINT3D_AUTO_VRAM_BUDGET", True):
        return None
    if not torch.cuda.is_available() or str(getattr(config, "device", "")) == "cpu":
        return None

    from gamedev_shared.quantization import enable_vae_optimizations
    from gamedev_shared.vram_budget import free_vram_bytes, paint_runtime_budget

    force_dino = os.environ.get("PAINT3D_DINO_DEVICE", "").strip() or None
    parked = False

    # Park dual cedo em low-VRAM: senão free_vram_bytes() vê placa cheia e
    # corta views a 2 sem libertar o que MeshRender precisa.
    if memory_efficient or _env_flag("PAINT3D_OFFLOAD_REF_UNET", False):
        _park_ref_unet_on_cpu(pipe, verbose=verbose)
        parked = True

    free_b = free_vram_bytes()
    budget = paint_runtime_budget(
        free_b,
        requested_views=int(requested_views),
        requested_resolution=int(requested_resolution),
        memory_efficient=bool(memory_efficient),
        force_dino_device=force_dino,
    )

    # Se o budget pediu ref offload e ainda não parkámos, park + re-medir:
    # free sobe e as vistas podem recuperar (antes ficavam sub-orçadas).
    if budget.offload_ref_unet and not parked:
        _park_ref_unet_on_cpu(pipe, verbose=verbose)
        parked = True
        free_b2 = free_vram_bytes()
        if free_b2 is not None and (free_b is None or free_b2 > free_b):
            budget = paint_runtime_budget(
                free_b2,
                requested_views=int(requested_views),
                requested_resolution=int(requested_resolution),
                memory_efficient=bool(memory_efficient),
                force_dino_device=force_dino,
            )
            free_b = free_b2

    config.max_selected_view_num = budget.max_views
    config.cfg_batch_chunking = budget.cfg_batch_chunking
    config.offload_ref_unet = budget.offload_ref_unet
    config.realesrgan_tile = budget.esrgan_tile
    config.dino_device = budget.dino_device

    mv = getattr(pipe, "models", {}).get("multiview_model") if pipe is not None else None
    if mv is not None:
        p = getattr(mv, "pipeline", None)
        if p is not None:
            p.cfg_batch_chunking = budget.cfg_batch_chunking
            unet = getattr(p, "unet", None)
            if unet is not None and hasattr(unet, "offload_ref_unet"):
                unet.offload_ref_unet = bool(budget.offload_ref_unet)
        dino = getattr(mv, "dino_v2", None)
        if dino is not None:
            try:
                dino.to(budget.dino_device)
                mv._dino_device = budget.dino_device
            except Exception as exc:
                if verbose:
                    _logger.warn(f"runtime budget: DINO->{budget.dino_device} falhou: {exc}")

    if budget.offload_ref_unet and not parked:
        _park_ref_unet_on_cpu(pipe, verbose=verbose)

    super_m = getattr(pipe, "models", {}).get("super_model") if pipe is not None else None
    if super_m is not None:
        super_m._tile = int(budget.esrgan_tile)

    vae = getattr(pipe, "vae", None) if pipe is not None else None
    if vae is not None:
        try:
            enable_vae_optimizations(
                vae,
                enable_slicing=True,
                enable_tiling=True,
                tile_sample_min_size=int(budget.vae_tile_size),
            )
        except Exception as exc:
            if verbose:
                _logger.warn(f"runtime budget: VAE tile falhou: {exc}")

    # Gate duro: não arrancar paint se MeshRender não cabe.
    ensure_meshrender_vram_headroom(pipe, verbose=verbose)
    free_b = free_vram_bytes()

    if verbose:
        free_gib = (free_b or 0) / (1024**3)
        notes = ", ".join(budget.notes) if budget.notes else "ok"
        _logger.info(
            f"VRAM runtime budget: free={free_gib:.1f} GiB views={budget.max_views} "
            f"@{budget.view_resolution} vae_tile={budget.vae_tile_size} "
            f"esrgan_tile={budget.esrgan_tile} dino={budget.dino_device} ({notes})"
        )
    out = budget.as_dict()
    out["meshrender_headroom_ok"] = True
    return out


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


def _ensure_trust_remote_code_compat() -> None:
    """Wrap ``DiffusionPipeline.from_pretrained`` para injetar ``trust_remote_code=True``.

    O Hunyuan3D-Paint (código vendored em ``hy3dpaint/``) carrega um pipeline custom
    via ``DiffusionPipeline.from_pretrained(..., custom_pipeline=...)``. A partir do
    diffusers 0.38, este caminho exige ``trust_remote_code=True`` explícito (antes era
    implícito). Em vez de alterar o código vendored, fazemos wrap do método uma única
    vez para que o default seja ``True`` quando o caller não o especifica — preservando
    o comportamento que o vendored assumia em versões anteriores do diffusers.
    """
    try:
        from diffusers import DiffusionPipeline as _DP
    except ImportError:
        return

    # Idempotência: marca guardada no atributo de classe (não no método bound).
    if getattr(_DP, "_paint3d_trust_remote_wrap", False):
        return

    # from_pretrained é um classmethod; acedemos à função subjacente via __func__.
    _orig_func = _DP.from_pretrained.__func__

    def _patched_from_pretrained(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        kwargs.setdefault("trust_remote_code", True)
        return _orig_func(cls, *args, **kwargs)

    _DP.from_pretrained = classmethod(_patched_from_pretrained)  # type: ignore[assignment]
    _DP._paint3d_trust_remote_wrap = True  # type: ignore[attr-defined]


def check_paint_rasterizer_available() -> None:
    """Garante que ``custom_rasterizer`` está disponível (shim nvdiffrast ou extensão nativa)."""
    _ensure_custom_rasterizer_shim()
    _ensure_trust_remote_code_compat()
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


def _apply_paint_kernel_opts(
    pipe: Any,
    *,
    torch_compile: bool = False,
    torch_compile_mode: str = "default",
    channels_last: bool = False,
    allow_group_offload: bool = False,
    memory_efficient: bool = False,
    verbose: bool = False,
) -> None:
    """Aplica channels_last / torch.compile / group-offload opt-in ao pipeline Paint.

    Compile no UNet wrapper 2.5D é frágil — compila VAE + inner ``unet``/``unet_dual``.
    Com group offload activo, força ``mode=default`` (sem CUDA graphs).
    """
    import os

    from gamedev_shared.quantization import (
        apply_channels_last,
        apply_torch_compile,
        resolve_torch_compile_mode,
    )

    if allow_group_offload:
        os.environ["PAINT3D_GROUP_OFFLOAD"] = "1"
        if verbose:
            _logger.info("Group offload experimental ligado (PAINT3D_GROUP_OFFLOAD=1)")

    if channels_last:
        if pipe.vae is not None:
            apply_channels_last(pipe.vae, log_fn=_logger.info if verbose else None)
        unet = pipe.unet
        if unet is not None:
            # Wrapper UNet2p5D — NHWC nos ModelMixin internos se existirem.
            applied = False
            for attr in ("unet", "unet_dual"):
                sub = getattr(unet, attr, None)
                if sub is not None:
                    apply_channels_last(sub, log_fn=_logger.info if verbose else None)
                    applied = True
            if not applied:
                apply_channels_last(unet, log_fn=_logger.info if verbose else None)

    if torch_compile:
        os.environ.pop("TORCHDYNAMO_DISABLE", None)
        # Paint com SDNQ mantém pesos na GPU; só group_offload activa streams.
        offload = "group_stream" if allow_group_offload else "none"
        mode = resolve_torch_compile_mode(
            torch_compile_mode,
            offload=offload,
            group_offload_active=allow_group_offload,
        )
        if mode != torch_compile_mode and verbose:
            _logger.info(f"torch.compile mode={torch_compile_mode} → {mode} (offload={offload})")

        # SDNQ QConv2d (mem-eff) rebenta em torch.compile (`Couldn't swap QConv2d.weight`).
        # Compilar só VAE nesse caso; UNet FP16 (sem mem-eff) ainda tenta compile.
        if memory_efficient:
            if verbose:
                _logger.info("torch.compile nos UNets skip (SDNQ QConv2d incompatível); tenta só VAE")
            unet_targets: list[tuple[Any, str]] = []
        else:
            unet_targets = []
            unet = pipe.unet
            if unet is not None:
                for attr in ("unet", "unet_dual"):
                    sub = getattr(unet, attr, None)
                    if sub is not None:
                        unet_targets.append((unet, attr))

        if pipe.vae is not None:
            try:
                compiled = apply_torch_compile(
                    pipe.vae, mode=mode, offload=offload, group_offload_active=allow_group_offload
                )
                mv = pipe.multiview_pipeline
                if mv is not None and compiled is not pipe.vae:
                    mv.vae = compiled
                    if verbose:
                        _logger.info(f"torch.compile ({mode}) aplicado ao VAE")
            except Exception as exc:
                if verbose:
                    _logger.warn(f"torch.compile VAE skip: {exc}")

        for parent, attr in unet_targets:
            sub = getattr(parent, attr, None)
            if sub is None:
                continue
            try:
                compiled = apply_torch_compile(
                    sub, mode=mode, offload=offload, group_offload_active=allow_group_offload
                )
                if compiled is not sub:
                    setattr(parent, attr, compiled)
                    if verbose:
                        _logger.info(f"torch.compile ({mode}) aplicado a unet.{attr}")
            except Exception as exc:
                if verbose:
                    _logger.warn(f"torch.compile unet.{attr} skip: {exc}")


def _try_paint_group_offload(pipe: Any, *, verbose: bool = False) -> bool:
    """Aplica group offload com CUDA streams ao pipeline Hunyuan-Paint (best-effort).

    O pipeline Hunyuan-Paint é custom (não diffusers ModelMixin standard): os módulos
    pesados vivem em ``pipe.models["multiview_model"].pipeline`` (unet, vae, text_encoder).
    Aplicamos group offload a esses inner modules via ``try_group_offloading`` com
    ``modules=`` custom. Resolve o setup (leaf_level vs block_level) via fórmula
    ``plan_group_offload`` baseada na VRAM livre.

    **Desactivado por defeito**: o fluxo dual-stream do wrapper UNet2p5DConditionModel
    (reference attention: ``unet`` popula ``condition_embed_dict`` que ``unet_dual``
    consome) conflitua com o group offload — os hooks do diffusers interferem com essa
    partilha de estado, causando ``KeyError`` no forward. Para activar experimentalmente:
    ``PAINT3D_GROUP_OFFLOAD=1``. O pipeline já tem SDNQ uint8 + offload_ref_unet +
    VAE tiling como mecanismos de poupança de VRAM.

    Best-effort: se falhar (attrs inesperados, diffusers sem suporte, conflito com
    o offload_ref_unet custom), retorna False silenciosamente — o pipeline segue com
    a colocação já feita pelo constructor (pipeline.to(device)).
    """
    # OPT-IN: o group offload conflitua com o fluxo dual-stream do UNet2p5D (reference
    # attention). Só activar se PAINT3D_GROUP_OFFLOAD=1 explicitamente.
    import os

    from gamedev_shared.group_offload import (
        plan_group_offload,
        try_group_offloading,
    )
    from gamedev_shared.hardware import cuda_gpu_specs
    from gamedev_shared.lowvram import GIB, get_footprint

    if os.environ.get("PAINT3D_GROUP_OFFLOAD", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False

    # Aceder ao inner diffusers pipeline (HunyuanPaintPipeline).
    inner_mv = getattr(pipe, "models", {}).get("multiview_model") if hasattr(pipe, "models") else None
    if inner_mv is None or not hasattr(inner_mv, "pipeline"):
        return False
    diff_pipe = inner_mv.pipeline

    # Footprint do Hunyuan3D-Paint — registry centralizado.
    specs = cuda_gpu_specs()
    if not specs:
        return False
    usable_gib = (max(m for _, m in specs) / GIB) * 0.9
    footprint = get_footprint("hunyuan-paint")
    cfg = plan_group_offload(usable_gib, footprint, quant_mode="none")
    if cfg is None:
        return False  # modelo cabe na GPU — sem offload

    # Aplicar aos inner modules do pipeline Hunyuan-Paint.
    # NOTA: o wrapper UNet2p5DConditionModel tem forward dual-stream com offload_ref_unet
    # custom; aplicamos group offload aos inner UNets (ModelMixin) e VAE, evitando o
    # wrapper para não interferir com essa lógica.
    applied_unet = False
    inner_unet = getattr(diff_pipe, "unet", None)
    if inner_unet is not None:
        # O wrapper expõe .unet (UNet2DConditionModel real) e opcionalmente .unet_dual.
        for attr in ("unet", "unet_dual"):
            sub = getattr(inner_unet, attr, None)
            if sub is not None and hasattr(sub, "enable_group_offload"):
                try:
                    sub.enable_group_offload(
                        onload_device=torch.device("cuda"),
                        offload_device=torch.device("cpu"),
                        offload_type=cfg.offload_type,
                        use_stream=cfg.use_stream,
                        num_blocks_per_group=cfg.num_blocks_per_group,
                        record_stream=cfg.record_stream,
                        non_blocking=cfg.non_blocking,
                    )
                    applied_unet = True
                    if verbose:
                        _logger.info(f"Group offload ({cfg.summary()}) aplicado a unet.{attr}")
                except Exception as e:
                    if verbose:
                        _logger.warn(f"Group offload falhou em unet.{attr}: {e}")

    # VAE + text_encoder via try_group_offloading (modules= custom no diff_pipe).
    applied_rest = try_group_offloading(
        diff_pipe,
        config=cfg,
        modules=("vae", "text_encoder"),
        log=verbose,
        log_fn=_logger.info,
    )

    if applied_unet or applied_rest:
        if verbose:
            _logger.info(f"Paint3D group offload ativo ({cfg.summary()})")
        return True
    return False


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


def _fit_glb_aabb_to_reference(output_path: str | Path, reference_path: str | Path, *, verbose: bool = False) -> None:
    """Encaixa o AABB do GLB de saída no AABB do GLB de referência (input), em
    espaço glTF (Y-up), preservando texturas/materiais/normais.

    Determinístico, **sem heurística**: lê rotação do node-transform do input,
    calcula escala+translação por AABB (trimesh só para métricas), aplica a
    matriz 4×4 em bpy e re-exporta com NORMAL+TANGENT via ``save_glb``.
    """
    import trimesh

    from paint3d.utils.mesh_io import load_mesh_bpy, save_glb

    ref = trimesh.load(str(reference_path), force="scene")
    out_scene = trimesh.load(str(output_path), force="scene")

    rot = np.eye(3)
    nodes = list(ref.graph.nodes_geometry)
    if nodes:
        node_t, _ = ref.graph[nodes[0]]
        r = np.asarray(node_t, float)[:3, :3]
        norms = np.linalg.norm(r, axis=0)
        norms[norms < 1e-9] = 1.0
        rot = r / norms

    mesh = out_scene.dump(concatenate=True)
    center0 = np.asarray(mesh.bounds.mean(axis=0), float)
    rot4 = np.eye(4)
    rot4[:3, :3] = rot
    t_center = np.eye(4)
    t_center[:3, 3] = center0
    t_neg = np.eye(4)
    t_neg[:3, 3] = -center0
    pre = t_center @ rot4 @ t_neg

    # Bounds após rotação (sem mutar o mesh de export — só métrica).
    corners = np.asarray(mesh.bounds, float)
    # 8 cantos do AABB
    mins, maxs = corners[0], corners[1]
    box = np.array([[x, y, z] for x in (mins[0], maxs[0]) for y in (mins[1], maxs[1]) for z in (mins[2], maxs[2])])
    ones = np.ones((len(box), 1))
    rotated = (pre @ np.hstack([box, ones]).T).T[:, :3]
    omin, omax = rotated.min(axis=0), rotated.max(axis=0)

    rmin, rmax = np.asarray(ref.bounds[0], float), np.asarray(ref.bounds[1], float)
    rext, oext = rmax - rmin, omax - omin
    safe = np.where(oext > 1e-9, oext, 1.0)
    scale = float(np.median(rext / safe))
    ref_center = (rmin + rmax) * 0.5
    out_center = (omin + omax) * 0.5
    fit = np.eye(4)
    fit[:3, :3] *= scale
    fit[:3, 3] = ref_center - scale * out_center
    mat = fit @ pre

    objs = load_mesh_bpy(output_path)
    for obj in objs:
        if getattr(obj, "type", None) != "MESH":
            continue
        co = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
        obj.data.vertices.foreach_get("co", co)
        pts = co.reshape(-1, 3)
        h = np.hstack([pts, np.ones((len(pts), 1))])
        pts2 = (mat @ h.T).T[:, :3]
        obj.data.vertices.foreach_set("co", pts2.ravel())
        obj.data.update()

    save_glb(objs, output_path)

    if verbose:
        ang = float(np.degrees(np.arccos(np.clip((np.trace(rot) - 1.0) / 2.0, -1.0, 1.0))))
        _logger.info(f"placement preservado (glTF/bpy): rot_input={ang:.1f}° scale={scale:.4f}")


def _apply_uniform_scale(objects: list, center: np.ndarray, scale: float) -> None:
    """Escala uniforme dos vértices em torno de ``center`` (in-place)."""
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    for obj in objects:
        for v in obj.data.vertices:
            v.co[0] = cx + (float(v.co[0]) - cx) * scale
            v.co[1] = cy + (float(v.co[1]) - cy) * scale
            v.co[2] = cz + (float(v.co[2]) - cz) * scale
        obj.data.update()


def _preserve_placement(
    objects: list,
    bounds_min_before: np.ndarray,
    bounds_max_before: np.ndarray,
    *,
    verbose: bool = False,
) -> None:
    """Repõe a colocação do input no output do paint, **sem heurística**.

    O pipeline de paint preserva a orientação do mesh (apenas re-normaliza
    posição/escala), por isso encaixamos deterministicamente o AABB do output no
    AABB do input: escala uniforme para o mesmo tamanho + translação para o mesmo
    centro. O resultado ocupa exatamente o bounding box do input → rotação, origem
    e escala preservadas. Não roda o mesh (rodar com base em rácios de AABB era
    frágil e introduzia erros em assets não-altos).
    """
    bmin_before = np.asarray(bounds_min_before, dtype=float)
    bmax_before = np.asarray(bounds_max_before, dtype=float)
    bmin_after, bmax_after = _get_combined_bounds(objects)

    ext_before = bmax_before - bmin_before
    ext_after = bmax_after - bmin_after
    center_before = (bmin_before + bmax_before) * 0.5
    center_after = (bmin_after + bmax_after) * 0.5

    # Escala uniforme = mediana dos rácios por eixo (robusta a pequenas variações
    # de remesh; ~1.0 quando o paint preserva a escala).
    safe_after = np.where(ext_after > 1e-9, ext_after, 1.0)
    scale = float(np.median(ext_before / safe_after))
    if abs(scale - 1.0) > 1e-4:
        _apply_uniform_scale(objects, center_after, scale)
        bmin_after, bmax_after = _get_combined_bounds(objects)
        center_after = (bmin_after + bmax_after) * 0.5

    offset = center_before - center_after
    if float(np.dot(offset, offset)) > 1e-12:
        _apply_translation(objects, offset)

    if verbose:
        _logger.info(f"placement preservado (sem rotação): scale={scale:.4f} offset={offset.tolist()}")


def apply_hunyuan_paint(
    mesh: Any,
    image: str | Path | Image.Image,
    *,
    model_repo: str = _defaults.DEFAULT_PAINT_HF_REPO,
    subfolder: str = _defaults.DEFAULT_PAINT_SUBFOLDER,
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
    memory_efficient: bool = _defaults.DEFAULT_MEMORY_EFFICIENT,
    gpu_ids: list[int] | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str = "default",
    channels_last: bool = False,
    allow_group_offload: bool = False,
) -> Any:
    """
    Aplica Hunyuan3D-Paint 2.1: mesh + imagem de referência → mesh com UV e textura/PBR (GLB).

    Por defeito corre em alta precisão (FP16, sem quantização, render 2048, texture 4096).
    Com ``memory_efficient=True`` ativa quantização SDNQ uint8 e resoluções reduzidas (1024/2048).

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
            f"  repo={model_repo} weights_subfolder={subfolder} "
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
            # Cascas internas: topology-fix (shape→clean) antes do paint.
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
            elif memory_efficient:
                config.render_size = _defaults.MEMORY_EFFICIENT_RENDER_SIZE
            else:
                config.render_size = _defaults.DEFAULT_PAINT_RENDER_SIZE

            if texture_size is not None:
                config.texture_size = texture_size
            elif memory_efficient:
                config.texture_size = _defaults.MEMORY_EFFICIENT_TEXTURE_SIZE
            else:
                config.texture_size = _defaults.DEFAULT_PAINT_TEXTURE_SIZE

            if not torch.cuda.is_available():
                config.render_size = min(config.render_size, 1024)
                config.texture_size = min(config.texture_size, 2048)

            config.bake_exp = bake_exp

            if not memory_efficient:
                config.quantization_config = {"type": "none"}

            _apply_optimization_config(config, memory_efficient=memory_efficient, gpu_ids=gpu_ids)
            if verbose:
                _log_optimization_config(config)

        with profile_span("paint_load_pipeline"):
            _preflight_paint_model(model_repo, subfolder, verbose=verbose)
            pipe = Hunyuan3DPaintPipeline(config)
            # Skip inpaint em ilhas UV nunca baked (cascas internas / occlusas).
            from .paint_prep import install_bake_supersampling, install_restricted_inpaint

            install_restricted_inpaint(pipe.view_processor)
            # Bake supersampled: subdiv SIMPLE só no bake para precisão por-texel.
            install_bake_supersampling(pipe.render, logger=_logger)

        with profile_span("paint_optimize_pipeline"):
            try:
                if memory_efficient and _sdnq_available() and pipe.unet is not None:
                    from gamedev_shared.sdnq import quantize_model

                    if verbose:
                        _logger.info("Modo memory-efficient: aplicando SDNQ uint8 ao UNet (dequantize_fp32=False)...")
                    pipe.unet = quantize_model(pipe.unet, preset="sdnq-uint8", dequantize_fp32=False)
                elif verbose:
                    if memory_efficient:
                        _logger.warn("Modo memory-efficient: SDNQ indisponível — UNet em FP16/qint8")
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

                if gpu_ids and len(gpu_ids) >= 2 and not memory_efficient:
                    _apply_paint_multi_gpu(pipe, gpu_ids, verbose=verbose)
                elif torch.cuda.device_count() >= 2 and not memory_efficient and verbose:
                    gpu0_name = torch.cuda.get_device_name(0)
                    gpu1_name = torch.cuda.get_device_name(1)
                    _logger.info(
                        f"Multi-GPU disponível: cuda:0 ({gpu0_name}), "
                        f"cuda:1 ({gpu1_name}). Usar --gpu-ids 0,1 para activar."
                    )
            except Exception as e:
                if verbose:
                    _logger.warn(f"Aviso: otimizações opcionais falharam: {e}")

        # --- Kernel opts (channels_last / compile) antes do group offload ---
        _apply_paint_kernel_opts(
            pipe,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            channels_last=channels_last,
            allow_group_offload=allow_group_offload,
            memory_efficient=memory_efficient,
            verbose=verbose,
        )

        # --- Group offload com CUDA streams (memory_efficient + GPUs pequenas) ---
        # Aplicado após SDNQ/VAE opts e antes da inferência. O pipeline Hunyuan-Paint
        # é custom (não diffusers ModelMixin standard), pelo que usamos try_group_offloading
        # com modules= custom. Best-effort: se falhar, segue com a colocação atual.
        if memory_efficient:
            _try_paint_group_offload(pipe, verbose=verbose)

        apply_runtime_vram_budget(
            config,
            pipe,
            requested_views=max_num_view,
            requested_resolution=view_resolution,
            memory_efficient=memory_efficient,
            verbose=verbose,
        )

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
        _preserve_placement(textured, bounds_min_before, bounds_max_before, verbose=verbose)

    return textured


def paint_file_to_file(
    mesh_path: str | Path,
    image_path: str | Path,
    output_path: str | Path,
    *,
    model_repo: str | None = None,
    subfolder: str | None = None,
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
    memory_efficient: bool = _defaults.DEFAULT_MEMORY_EFFICIENT,
    gpu_ids: list[int] | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str = "default",
    channels_last: bool = False,
    allow_group_offload: bool = False,
) -> Path:
    """Atalho: carrega mesh, pinta com Hunyuan3D-Paint 2.1 (PBR baked), exporta GLB."""
    repo = model_repo or _defaults.DEFAULT_PAINT_HF_REPO
    sub = subfolder or _defaults.DEFAULT_PAINT_SUBFOLDER
    if max_num_view is None:
        nviews = _defaults.MEMORY_EFFICIENT_MAX_VIEWS if memory_efficient else _defaults.DEFAULT_PAINT_MAX_VIEWS
    else:
        nviews = max_num_view
    if view_resolution is None:
        vres = (
            _defaults.MEMORY_EFFICIENT_VIEW_RESOLUTION if memory_efficient else _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
        )
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
        # Preservação feita em glTF sobre o ficheiro final (frame correto), não em bpy.
        preserve_origin=False,
        memory_efficient=memory_efficient,
        gpu_ids=gpu_ids,
        torch_compile=torch_compile,
        torch_compile_mode=torch_compile_mode,
        channels_last=channels_last,
        allow_group_offload=allow_group_offload,
    )

    output_path = Path(output_path)
    with profile_span("paint_save_glb"):
        save_glb(out, output_path)
    if preserve_origin:
        with profile_span("paint_preserve_placement"):
            _fit_glb_aabb_to_reference(output_path, mesh_path, verbose=verbose)
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
        memory_efficient: bool = _defaults.DEFAULT_MEMORY_EFFICIENT,
        gpu_ids: list[int] | None = None,
        torch_compile: bool = False,
        torch_compile_mode: str = "default",
        channels_last: bool = False,
        allow_group_offload: bool = False,
    ):
        self._model_repo = model_repo
        self._subfolder = subfolder
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
        self._memory_efficient = memory_efficient
        self._gpu_ids = gpu_ids
        self._torch_compile = torch_compile
        self._torch_compile_mode = torch_compile_mode
        self._channels_last = channels_last
        self._allow_group_offload = allow_group_offload
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
                f"max_views={self._max_num_view} "
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
            elif self._memory_efficient:
                config.render_size = _defaults.MEMORY_EFFICIENT_RENDER_SIZE
            else:
                config.render_size = _defaults.DEFAULT_PAINT_RENDER_SIZE

            if self._texture_size is not None:
                config.texture_size = self._texture_size
            elif self._memory_efficient:
                config.texture_size = _defaults.MEMORY_EFFICIENT_TEXTURE_SIZE
            else:
                config.texture_size = _defaults.DEFAULT_PAINT_TEXTURE_SIZE

            if not torch.cuda.is_available():
                config.render_size = min(config.render_size, 1024)
                config.texture_size = min(config.texture_size, 2048)

            config.bake_exp = self._bake_exp

            if not self._memory_efficient:
                config.quantization_config = {"type": "none"}

            _apply_optimization_config(config, memory_efficient=self._memory_efficient, gpu_ids=self._gpu_ids)
            if self._verbose:
                _log_optimization_config(config, prefix="[batch] ")

        with profile_span("paint_load_pipeline"):
            _preflight_paint_model(self._model_repo, self._subfolder, verbose=self._verbose)
            pipe = Hunyuan3DPaintPipeline(config)
            from .paint_prep import install_bake_supersampling, install_restricted_inpaint

            install_restricted_inpaint(pipe.view_processor)
            install_bake_supersampling(pipe.render, logger=_logger)

        with profile_span("paint_optimize_pipeline"):
            try:
                if self._memory_efficient and _sdnq_available() and pipe.unet is not None:
                    from gamedev_shared.sdnq import quantize_model

                    if self._verbose:
                        _logger.info(
                            "[batch] Modo memory-efficient: aplicando SDNQ uint8 ao UNet (dequantize_fp32=False)..."
                        )
                    pipe.unet = quantize_model(pipe.unet, preset="sdnq-uint8", dequantize_fp32=False)
                elif self._verbose:
                    if self._memory_efficient:
                        _logger.warn("[batch] Modo memory-efficient: SDNQ indisponível — UNet em FP16/qint8")
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

                if self._gpu_ids and len(self._gpu_ids) >= 2 and not self._memory_efficient:
                    _apply_paint_multi_gpu(pipe, self._gpu_ids, verbose=self._verbose)
                elif torch.cuda.device_count() >= 2 and not self._memory_efficient and self._verbose:
                    gpu0_name = torch.cuda.get_device_name(0)
                    gpu1_name = torch.cuda.get_device_name(1)
                    _logger.info(
                        f"[batch] Multi-GPU disponível: cuda:0 ({gpu0_name}), "
                        f"cuda:1 ({gpu1_name}). Usar --gpu-ids 0,1 para activar."
                    )
            except Exception as e:
                if self._verbose:
                    _logger.warn(f"[batch] Aviso: otimizações opcionais falharam: {e}")

        _apply_paint_kernel_opts(
            pipe,
            torch_compile=self._torch_compile,
            torch_compile_mode=self._torch_compile_mode,
            channels_last=self._channels_last,
            allow_group_offload=self._allow_group_offload,
            memory_efficient=self._memory_efficient,
            verbose=self._verbose,
        )
        if self._memory_efficient:
            _try_paint_group_offload(pipe, verbose=self._verbose)

        apply_runtime_vram_budget(
            config,
            pipe,
            requested_views=self._max_num_view,
            requested_resolution=self._view_resolution,
            memory_efficient=self._memory_efficient,
            verbose=self._verbose,
        )

        self._pipe = pipe
        self._config = config
        return self

    def refresh_runtime_budget(
        self,
        *,
        requested_views: int | None = None,
        requested_resolution: int | None = None,
    ) -> dict[str, Any] | None:
        """Reaplica orçamento VRAM (UMS / batch entre itens).

        Args:
            requested_views: override por-request (UMS). Clamped ao
                ``max_num_view`` com que o pipeline foi carregado — as câmaras
                candidatas são fixadas no load; só se pode reduzir.
            requested_resolution: override por-request. Clamped à resolução do
                load pelo mesmo motivo (buffers de render são fixos).

        Returns:
            Dict do budget aplicado (:func:`apply_runtime_vram_budget`).
        """
        if self._pipe is None or self._config is None:
            return None
        views = self._max_num_view
        if requested_views is not None:
            views = max(1, min(int(requested_views), self._max_num_view))
        res = self._view_resolution
        if requested_resolution is not None:
            res = max(256, min(int(requested_resolution), self._view_resolution))
        return apply_runtime_vram_budget(
            self._config,
            self._pipe,
            requested_views=views,
            requested_resolution=res,
            memory_efficient=self._memory_efficient,
            verbose=self._verbose,
        )

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
            _preserve_placement(textured, bounds_min_before, bounds_max_before, verbose=self._verbose)

        return textured
