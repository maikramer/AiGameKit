"""Centralized SDNQ (SD.Next Quantization) module for the AiGameKit monorepo.

Single source of truth for SDNQ quantization across all packages:
- Tested preset configurations (based on benchmarks across Text2D, Paint3D)
- Pre-quantization of models at install time
- Runtime quantization application (post-load)
- Quantized matmul acceleration for pipelines

Default preset: ``sdnq-uint8`` — best tested across all models and GPUs.

Backend coverage (SDNQ >=0.2.9):
    SDNQ is pure PyTorch. On CUDA/ROCm/XPU it uses the Inductor backend via Triton
    (quantized matmul is auto-enabled when ``use_torch_compile`` is truthy). On CPU
    and MPS (Apple Mac) it falls back to PyTorch eager mode — quantization works but
    quantized matmul is disabled (no Triton). SDNQ can also compile to OpenVINO via
    ``torch.compile → Inductor → OpenVINO`` for Intel CPU/GPU inference.

References:
    - SDNQ library: https://github.com/Disty0/sdnq
    - Pre-quantized models: https://huggingface.co/collections/Disty0/sdnq
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def _auto_quantized_matmul_default() -> bool:
    """Default de ``use_quantized_matmul`` quando o caller não especifica.

    O default upstream (``sdnq.common.use_torch_compile``) liga torch.compile/
    Triton na primeira matmul — compile frio de muitos minutos por processo,
    o que não compensa fora de batches longos (e arde em GPUs ~6 GB partilhadas).
    Opt-in explícito via env ``AIGAMEKIT_SDNQ_AUTO_MATMUL=1``.
    """
    return os.environ.get("AIGAMEKIT_SDNQ_AUTO_MATMUL", "0").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Presets — validated by benchmarks in AiGameKitLab
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SDNQPreset:
    """Tested SDNQ quantization preset with all parameters for ``SDNQConfig``.

    These presets encode the best-tested configurations from benchmark sweeps
    across Text2D (FLUX Klein), Text3D (Hunyuan3D-Omni), Paint3D
    (Hunyuan3D-Paint UNet) e SD1.5 - reproduzivel com
    ``aigamekit-lab bench sdnq`` (ver ``docs/findings/SDNQ_029_FINDINGS.md``).

    SDNQ >=0.2.8 features: ``scale_dtype``/``zero_point_dtype`` fazem
    *double quantization* das escalas. Com group_size=32, uint4 com escalas
    fp32 custa ~6 bits/peso; com escalas uint8 custa ~5 bits — mesmo erro,
    1 bit menos. ``use_codebook`` (Lloyd-Max) continua disponível, mas **não é usado
    nos presets**: guarda ``2**bits`` níveis fp32 *por grupo*, o que a group
    size 32 inflaciona o preset para 11-20 bits/peso.
    """

    name: str
    weights_dtype: str
    group_size: int
    use_svd: bool
    svd_rank: int
    svd_steps: int
    dequantize_fp32: bool
    description: str
    use_hadamard: bool = False
    hadamard_group_size: int | None = None  # None = group_size (compat N2)
    use_codebook: bool = False
    scale_dtype: str | None = None
    zero_point_dtype: str | None = None
    use_codebook_scale: bool = False
    use_dynamic_quantization: bool = False
    dynamic_loss_threshold: float | None = None
    weights_dtype_order: list[str] | None = None
    # Fator de VRAM (peso_quant / peso_fp16) medido; None = usa o dtype.
    vram_factor: float | None = None


PRESETS: dict[str, SDNQPreset] = {
    "sdnq-uint8": SDNQPreset(
        name="sdnq-uint8",
        weights_dtype="uint8",
        group_size=0,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        description=(
            "SDNQ UINT8 — default; group auto (1024 em lineares) e sem codebook: "
            "8.1 b/peso e ~100x mais rápido no load que o codebook (que gastava 10-18 b/peso)"
        ),
        vram_factor=0.55,
    ),
    "sdnq-int8": SDNQPreset(
        name="sdnq-int8",
        weights_dtype="int8",
        group_size=0,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        description="SDNQ INT8 — signed, alternative to uint8",
        vram_factor=0.55,
    ),
    "sdnq-int4": SDNQPreset(
        name="sdnq-int4",
        weights_dtype="uint4",
        group_size=32,
        use_svd=True,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        use_hadamard=True,
        hadamard_group_size=256,
        scale_dtype="uint8",
        description=(
            "SDNQ 4-bit — uint4 + SVD + Hadamard N4 + escalas double-quant (uint8); "
            "~5.1 b/peso com -23% de erro vs int4+SVD (5.6 b/peso)"
        ),
        vram_factor=0.32,
    ),
    "sdnq-uint4": SDNQPreset(
        name="sdnq-uint4",
        weights_dtype="uint4",
        group_size=32,
        use_svd=True,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        use_hadamard=True,
        hadamard_group_size=256,
        scale_dtype="uint8",
        description=(
            "SDNQ UINT4 — alias de qualidade do sdnq-int4 (mesma receita 0.2.9, sem o codebook que gastava 20 b/peso)"
        ),
        vram_factor=0.32,
    ),
    "sdnq-uint4-hq": SDNQPreset(
        name="sdnq-uint4-hq",
        weights_dtype="uint4",
        group_size=16,
        use_svd=True,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        use_hadamard=True,
        hadamard_group_size=256,
        scale_dtype="uint8",
        description=(
            "SDNQ 4-bit HQ — group 16 + SVD + Hadamard + double-quant scales; "
            "mesma VRAM do int4 antigo com -35% de erro (tier opt-in)"
        ),
        vram_factor=0.38,
    ),
    "sdnq-fp8": SDNQPreset(
        name="sdnq-fp8",
        weights_dtype="fp8",
        group_size=0,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        description="SDNQ FP8 — RTX 40 series (Ada Lovelace+)",
        vram_factor=0.55,
    ),
    # Degraus abaixo de 4 bits: exclusivos dos modos de offload do planner
    # (lowvram) — quando nem int4 + group offload dá folga. Sem codebook: a
    # 3/2 bits o codebook (2**bits níveis fp32 por grupo) gastava 11/6 b/peso,
    # mais do que o int4; com escalas double-quant ficam em 3.5/2.5 b/peso e
    # são degraus reais de memória. A chave ``sdnq-intN`` mantém-se por compat
    # com o ladder do planner/vramd.
    "sdnq-int3": SDNQPreset(
        name="sdnq-int3",
        weights_dtype="uint3",
        group_size=32,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        use_hadamard=True,
        hadamard_group_size=256,
        scale_dtype="uint8",
        description="SDNQ 3-bit — uint3 + Hadamard N4 + double-quant scales (3.5 b/peso)",
        vram_factor=0.24,
    ),
    "sdnq-int2": SDNQPreset(
        name="sdnq-int2",
        weights_dtype="uint2",
        group_size=32,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        use_hadamard=True,
        hadamard_group_size=256,
        scale_dtype="uint8",
        description="SDNQ 2-bit — uint2 + Hadamard N4 + double-quant scales (2.5 b/peso)",
        vram_factor=0.18,
    ),
}

DEFAULT_PRESET: str = "sdnq-uint8"


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Check if the ``sdnq`` package is installed."""
    try:
        from sdnq import SDNQConfig  # noqa: F401

        return True
    except ImportError:
        return False


def detect_compute_backend() -> str:
    """Detect the active compute backend for SDNQ.

    SDNQ is pure PyTorch: on CUDA/ROCm/XPU it uses the Inductor backend (via Triton,
    enabling quantized matmul); on CPU and MPS it falls back to PyTorch eager mode
    (quantization works but quantized matmul is disabled). SDNQ can also compile to
    OpenVINO via ``torch.compile → Inductor → OpenVINO`` for Intel devices.

    Returns:
        One of ``"cuda"``, ``"xpu"``, ``"mps"``, ``"cpu"``.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if bool(getattr(torch, "xpu", None)) and torch.xpu.is_available():
            return "xpu"
        if bool(getattr(torch, "mps", None)) and torch.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def register_sdnq(*, patch_lora: bool = False) -> bool:
    """Import ``sdnq`` to register it with diffusers/transformers.

    Importing ``SDNQConfig`` registers SDNQ as a backend so ``from_pretrained`` can
    load pre-quantized checkpoints. Consolidates the duplicated ``_register_sdnq``
    pattern that existed across the 2D tools (Text2D, Skymap2D, Texture2D).

    Args:
        patch_lora: When ``True``, also apply :func:`patch_lora_shape_calculation`
            (needed by pipelines that load LoRA weights on top of SDNQ checkpoints,
            e.g. Skymap2D/Texture2D with the equirect LoRA).

    Returns:
        Whether Triton (and thus quantized matmul) is available on this backend.

    Raises:
        ImportError: If ``sdnq`` is not installed.
    """
    try:
        from sdnq import SDNQConfig  # noqa: F401 — registration side-effect
        from sdnq.common import use_torch_compile

        if patch_lora:
            patch_lora_shape_calculation()
        return bool(use_torch_compile)
    except ImportError as e:
        raise ImportError("O pacote 'sdnq' é necessário. Instale com: pip install sdnq") from e


def patch_lora_shape_calculation() -> None:
    """Monkey-patch diffusers' ``_calculate_module_shape`` to handle SDNQ weights.

    SDNQ stores quantized weights as 1D uint8 tensors with a ``scale`` attribute
    on the parent module. Without this patch, ``load_lora_weights`` fails with
    ``ValueError: not enough values to unpack (expected 2, got 1)``.

    Idempotent — safe to call multiple times.
    """
    try:
        from diffusers.loaders.lora_pipeline import FluxLoraLoaderMixin, get_submodule_by_name
    except ImportError:
        return

    if getattr(FluxLoraLoaderMixin, "_sdnq_patched", False):
        return

    import torch

    def _sdnq_weight_shape(weight: Any, module: Any) -> Any:
        if hasattr(module, "scale") and weight.ndim == 1 and weight.dtype == torch.uint8:
            out_features = module.scale.shape[0]
            in_features = weight.shape[0] * 2 // out_features
            return (out_features, in_features)
        if weight.__class__.__name__ == "Params4bit":
            return weight.quant_state.shape
        if weight.__class__.__name__ == "GGUFParameter":
            return weight.quant_shape
        return weight.shape

    @staticmethod  # type: ignore[misc]
    def _patched(model: Any, base_module: Any = None, base_weight_param_name: str | None = None) -> Any:
        if base_module is not None:
            return _sdnq_weight_shape(base_module.weight, base_module)
        if base_weight_param_name is not None:
            if not base_weight_param_name.endswith(".weight"):
                raise ValueError(f"Invalid {base_weight_param_name=}")
            module_path = base_weight_param_name.rsplit(".weight", 1)[0]
            submod = get_submodule_by_name(model, module_path)
            return _sdnq_weight_shape(submod.weight, submod)
        raise ValueError("Either base_module or base_weight_param_name must be provided.")

    # Monkeypatching intencional do SDNQ — setattr com nome em variável
    # (ruff B010) aceito pelo mypy com e sem os tipos do diffusers instalados.
    for _attr, _value in (("_calculate_module_shape", _patched), ("_sdnq_patched", True)):
        setattr(FluxLoraLoaderMixin, _attr, _value)


def _check_cuda() -> bool:
    """Check if CUDA is available (lazy import)."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# SDNQConfig factory
# ---------------------------------------------------------------------------

_CONFIG_KEYS_CACHE: set[str] | None = None


def _supported_config_keys() -> set[str]:
    """Return the keyword names accepted by the installed ``SDNQConfig``.

    A SDNQ evolui rápido (0.2.8 adicionou ``scale_dtype``, 0.2.9
    ``weights_dtype_order``); filtrar por assinatura evita ``TypeError`` em
    venvs que ainda não foram atualizados.
    """
    global _CONFIG_KEYS_CACHE
    if _CONFIG_KEYS_CACHE is None:
        from sdnq import SDNQConfig

        try:
            parameters = inspect.signature(SDNQConfig.__init__).parameters
        except (TypeError, ValueError):
            # Mocks/test doubles sem assinatura — assume a versão moderna.
            return {
                "scale_dtype",
                "zero_point_dtype",
                "use_codebook_scale",
                "use_dynamic_quantization",
                "dynamic_loss_threshold",
                "weights_dtype_order",
            }
        keys = {
            name
            for name, parameter in parameters.items()
            if name != "self"
            and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        }
        # Só cacheia assinaturas reais. Um mock devolve ``(*args, **kw)``
        # (conjunto vazio) e envenenaria o cache para o resto do processo.
        if keys:
            _CONFIG_KEYS_CACHE = keys
        return keys
    return _CONFIG_KEYS_CACHE


def create_config(
    preset: str = DEFAULT_PRESET,
    *,
    quantization_device: str | None = None,
    return_device: str | None = None,
    use_quantized_matmul: bool | None = None,
    modules_to_not_convert: list[str] | None = None,
    **overrides: Any,
) -> Any:
    """Create an ``SDNQConfig`` from a named preset with optional overrides.

    Args:
        preset: Preset name (``"sdnq-uint8"``, ``"sdnq-int8"``, ``"sdnq-int4"``,
            ``"sdnq-uint4"``, ``"sdnq-fp8"``).
        quantization_device: Device for quantization (default: ``"cuda"`` if available,
            else ``"cpu"``). On CPU, quantization runs in PyTorch eager mode.
        return_device: Device after quantization (default: same as ``quantization_device``).
        use_quantized_matmul: Enable Triton/CUDA quantized matmul (default:
            ``AIGAMEKIT_SDNQ_AUTO_MATMUL`` env, off por defeito — evita compile
            frio torch.compile/Triton). Disabled on CPU (no Triton).
        modules_to_not_convert: Module names to skip during quantization.
        **overrides: Additional keyword args forwarded to ``SDNQConfig``.
            Notable SDNQ >=0.2.0 options:

            - ``use_dynamic_quantization`` (bool): dynamically select a per-layer
              quantization type based on ``dynamic_loss_threshold``;
              ``weights_dtype`` becomes the minimum allowed type.
            - ``use_hadamard`` (bool) + ``hadamard_group_size``: Hadamard rotations
              on quantized weights (improves accuracy at low bit-widths).
            - ``scale_dtype`` / ``zero_point_dtype`` (SDNQ >=0.2.8): double
              quantization of scales (e.g. ``"uint8"``) — cuts ~1 bit/peso a
              ``group_size=32`` sem perda mensurável.
            - ``offload_buffers`` (bool): basic CPU offload of optimizer/weight
              buffers — useful for the 6 GB VRAM target.

    Returns:
        ``SDNQConfig`` instance.

    Raises:
        ImportError: If ``sdnq`` is not installed.
        KeyError: If preset name is unknown.
    """
    if preset not in PRESETS:
        raise KeyError(f"Unknown SDNQ preset: {preset!r}. Available: {', '.join(PRESETS)}")

    from sdnq import SDNQConfig

    p = PRESETS[preset]

    device = quantization_device or ("cuda" if _check_cuda() else "cpu")
    ret_device = return_device or device
    matmul = use_quantized_matmul if use_quantized_matmul is not None else _auto_quantized_matmul_default()

    kwargs: dict[str, Any] = {
        "weights_dtype": p.weights_dtype,
        "group_size": p.group_size,
        "use_svd": p.use_svd,
        "svd_rank": p.svd_rank,
        "svd_steps": p.svd_steps,
        "use_hadamard": p.use_hadamard,
        "use_codebook": p.use_codebook,
        "use_quantized_matmul": matmul,
        "quantization_device": device,
        "return_device": ret_device,
        "dequantize_fp32": p.dequantize_fp32,
    }
    if p.use_hadamard:
        # Hadamard N4 (ConvRot) quando o grupo é potência de 4 (ex.: 256) —
        # melhor qualidade que N2; None = group_size do preset (compat).
        kwargs["hadamard_group_size"] = p.hadamard_group_size or p.group_size

    # Campos introduzidos na SDNQ 0.2.8/0.2.9. Só os passamos quando o preset os
    # define (passar None/False é no-op) e quando a versão instalada os conhece
    # — venvs antigos continuam a carregar os presets antigos sem TypeError.
    optional: dict[str, Any] = {
        "scale_dtype": p.scale_dtype,
        "zero_point_dtype": p.zero_point_dtype,
        "use_codebook_scale": p.use_codebook_scale or None,
        "use_dynamic_quantization": p.use_dynamic_quantization or None,
        "dynamic_loss_threshold": p.dynamic_loss_threshold,
        "weights_dtype_order": p.weights_dtype_order,
    }
    supported = _supported_config_keys()
    for key, value in optional.items():
        if value is None or value is False:
            continue
        if key in supported:
            kwargs[key] = value
        else:
            _logger.warning(
                "SDNQ instalado não conhece %s (preset %s) — campo ignorado; atualiza `sdnq>=0.2.9`.",
                key,
                preset,
            )

    if modules_to_not_convert is not None:
        kwargs["modules_to_not_convert"] = modules_to_not_convert
    kwargs.update(overrides)

    return SDNQConfig(**kwargs)


# ---------------------------------------------------------------------------
# Runtime quantization (post-load)
# ---------------------------------------------------------------------------


def quantize_model(
    model: Any,
    preset: str = DEFAULT_PRESET,
    *,
    modules_to_not_convert: list[str] | None = None,
    **overrides: Any,
) -> Any:
    """Apply SDNQ post-load quantization to a model.

    Wraps ``sdnq_post_load_quant`` with preset-based configuration and
    error handling. Returns the quantized model.

    Args:
        model: PyTorch model to quantize.
        preset: Preset name (default: ``"sdnq-uint8"``).
        modules_to_not_convert: Module names to skip.
        **overrides: Additional ``SDNQConfig`` overrides.

    Returns:
        Quantized model.

    Raises:
        ImportError: If ``sdnq`` is not installed.
    """
    from sdnq import sdnq_post_load_quant

    config = create_config(preset, modules_to_not_convert=modules_to_not_convert, **overrides)
    params = inspect.signature(sdnq_post_load_quant).parameters
    if "quantization_config" in params:
        # SDNQ >=0.2.5 aceita o config completo. É o caminho correto: o filtro
        # por assinatura usado antes descartava em silêncio ``scale_dtype`` e
        # ``zero_point_dtype``, que não são parâmetros explícitos da função.
        return sdnq_post_load_quant(model, quantization_config=config)

    supported = set(params) - {"model"}
    config_vars = {k: v for k, v in vars(config).items() if k in supported}
    return sdnq_post_load_quant(model, **config_vars)


def apply_quantized_matmul(
    pipe: Any,
    *,
    enabled: bool = True,
    module_names: tuple[str, ...] = ("transformer", "text_encoder", "text_encoder_2"),
) -> None:
    """Apply quantized matmul acceleration to pipeline sub-modules in-place.

    For diffusers pipelines (e.g., FLUX), enables Triton/CUDA quantized
    matmul on the requested sub-modules. Sub-modules that are missing or lack a
    ``quantization_config`` are silently skipped.

    Args:
        pipe: Diffusion pipeline with sub-modules to accelerate.
        enabled: Whether to enable (default: ``True``).
        module_names: Sub-module attribute names to target. Defaults to the FLUX
            pipeline layout (``transformer``, ``text_encoder``, ``text_encoder_2``);
            pass e.g. ``("unet",)`` for UNet-based pipelines.
    """
    if not enabled:
        return
    try:
        import torch
        from sdnq.loader import apply_sdnq_options_to_model

        cuda_ok = torch.cuda.is_available()
        xpu_ok = bool(getattr(torch, "xpu", None)) and torch.xpu.is_available()
        if not (cuda_ok or xpu_ok):
            return
        for name in module_names:
            mod = getattr(pipe, name, None)
            if mod is None:
                continue
            if not hasattr(mod, "quantization_config"):
                continue
            try:
                setattr(pipe, name, apply_sdnq_options_to_model(mod, use_quantized_matmul=True))
            except RuntimeError as e:
                if "triton" not in str(e).lower():
                    raise
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Pre-quantization (install time)
# ---------------------------------------------------------------------------


def pre_quantize_model(
    model: Any,
    output_dir: Path,
    preset: str = DEFAULT_PRESET,
    *,
    meta: dict[str, Any] | None = None,
    **overrides: Any,
) -> Path:
    """Pre-quantize a model and save to disk for later fast-loading.

    This is intended to run once at install time. The saved model can be
    loaded directly with ``from_pretrained(...)`` with SDNQ registered.

    Args:
        model: PyTorch model to quantize (already loaded in FP16/FP32).
        output_dir: Directory to save the quantized model.
        preset: SDNQ preset name.
        meta: Additional metadata to save alongside.
        **overrides: Additional ``SDNQConfig`` overrides.

    Returns:
        Path to the output directory.
    """
    quantized = quantize_model(model, preset, **overrides)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from safetensors.torch import save_file

        save_file(quantized.state_dict(), str(output_dir / "model.safetensors"))
    except ImportError:
        import torch

        torch.save(quantized.state_dict(), output_dir / "model.pt")

    p = PRESETS[preset]
    quant_meta = {
        "quantization": f"sdnq-{p.weights_dtype}",
        "preset": preset,
        "weights_dtype": p.weights_dtype,
        "group_size": p.group_size,
        "use_svd": p.use_svd,
        "use_hadamard": p.use_hadamard,
        "use_codebook": p.use_codebook,
        "scale_dtype": p.scale_dtype,
        "zero_point_dtype": p.zero_point_dtype,
        "use_codebook_scale": p.use_codebook_scale,
        "use_dynamic_quantization": p.use_dynamic_quantization,
        "weights_dtype_order": p.weights_dtype_order,
        **(meta or {}),
    }
    (output_dir / "quantization_meta.json").write_text(json.dumps(quant_meta, indent=2), encoding="utf-8")

    del quantized
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    return output_dir


# ---------------------------------------------------------------------------
# SDNQ Disk Cache (evita reler pesos bf16 + repetir SVD/Hadamard a cada cold load)
# ---------------------------------------------------------------------------

SDNQ_CACHE_SCHEMA_VERSION = "sdnq-cache-v1"
SDNQ_CACHE_ENV = "AIGAMEKIT_SDNQ_CACHE"
SDNQ_CACHE_DIR_ENV = "AIGAMEKIT_SDNQ_CACHE_DIR"


def sdnq_cache_enabled() -> bool:
    """Whether the on-disk SDNQ quantized-component cache is enabled.

    Enabled by default; set ``AIGAMEKIT_SDNQ_CACHE=0`` (or ``false/no/off``)
    to disable (useful for A/B benchmarks or ephemeral containers).
    """
    raw = os.environ.get(SDNQ_CACHE_ENV, "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def sdnq_cache_dir() -> Path:
    """Root directory for cached SDNQ-quantized components."""
    override = os.environ.get(SDNQ_CACHE_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "aigamekit" / "sdnq-cache"


def _sdnq_installed_version() -> str:
    """Return installed ``sdnq`` package version string (best-effort)."""
    try:
        import importlib.metadata

        return importlib.metadata.version("sdnq")
    except Exception:
        pass
    try:
        import sdnq  # type: ignore[import-untyped]

        return str(getattr(sdnq, "__version__", "unknown"))
    except Exception:
        return "unknown"


def resolve_hf_snapshot_revision(model_id: str, *, cache_dir: str | Path | None = None) -> str:
    """Resolve a local revision fingerprint for ``model_id`` without network calls.

    For local directories, uses the resolved path + config/index mtime. For HF
    repo ids (``org/repo``), reads ``refs/main`` or the newest ``snapshots/*``
    entry from the local HuggingFace cache so model updates automatically
    invalidate the SDNQ disk cache.
    """
    local_path = Path(model_id).expanduser()
    if local_path.exists():
        mtime = 0
        for probe in ("modular_model_index.json", "model_index.json", "config.json"):
            candidate = local_path / probe
            if candidate.is_file():
                with contextlib.suppress(OSError):
                    mtime = max(mtime, int(candidate.stat().st_mtime))
        return f"local:{local_path.resolve()}:{mtime}"

    hub_root_raw = (
        str(cache_dir)
        if cache_dir
        else (
            os.environ.get("HF_HUB_CACHE")
            or os.environ.get("HUGGINGFACE_HUB_CACHE")
            or str(Path.home() / ".cache" / "huggingface" / "hub")
        )
    )
    repo_dir = Path(hub_root_raw).expanduser() / f"models--{model_id.replace('/', '--')}"
    ref_main = repo_dir / "refs" / "main"
    if ref_main.is_file():
        try:
            rev = ref_main.read_text(encoding="utf-8").strip()
            if rev:
                return rev
        except OSError:
            pass
    snaps_dir = repo_dir / "snapshots"
    if snaps_dir.is_dir():
        try:
            snaps = [p for p in snaps_dir.iterdir() if p.is_dir()]
            if snaps:
                snaps.sort(key=lambda p: p.stat().st_mtime)
                return snaps[-1].name
        except OSError:
            pass
    return "unknown"


def build_sdnq_cache_key(
    model_id: str,
    component_name: str,
    preset: str,
    *,
    revision: str | None = None,
    cache_dir: str | Path | None = None,
    modules_to_not_convert: list[str] | tuple[str, ...] | None = None,
    dequantize_fp32: bool | None = None,
    torch_dtype: str = "bfloat16",
    extra: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic filesystem-safe cache directory name for a component."""
    p = PRESETS.get(preset)
    preset_payload: dict[str, Any]
    if p is not None:
        preset_payload = {
            "weights_dtype": p.weights_dtype,
            "group_size": p.group_size,
            "use_svd": p.use_svd,
            "svd_rank": p.svd_rank,
            "svd_steps": p.svd_steps,
            "use_hadamard": p.use_hadamard,
            "hadamard_group_size": p.hadamard_group_size,
            "use_codebook": p.use_codebook,
            "scale_dtype": p.scale_dtype,
            "zero_point_dtype": p.zero_point_dtype,
            "use_codebook_scale": p.use_codebook_scale,
            "use_dynamic_quantization": p.use_dynamic_quantization,
            "dynamic_loss_threshold": p.dynamic_loss_threshold,
            "weights_dtype_order": list(p.weights_dtype_order) if p.weights_dtype_order else None,
        }
    else:
        preset_payload = {"preset": preset}

    payload = {
        "schema": SDNQ_CACHE_SCHEMA_VERSION,
        "sdnq_version": _sdnq_installed_version(),
        "model_id": model_id,
        "revision": revision or resolve_hf_snapshot_revision(model_id, cache_dir=cache_dir),
        "component": component_name,
        "preset": preset,
        "preset_params": preset_payload,
        "dequantize_fp32": dequantize_fp32 if dequantize_fp32 is not None else (p.dequantize_fp32 if p else True),
        "modules_to_not_convert": sorted(str(x) for x in (modules_to_not_convert or ())),
        "torch_dtype": str(torch_dtype),
        "extra": extra or {},
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    safe_comp = re.sub(r"[^a-zA-Z0-9_.-]+", "_", component_name).strip("_") or "component"
    safe_preset = re.sub(r"[^a-zA-Z0-9_.-]+", "_", preset).strip("_") or "preset"
    return f"{safe_comp}-{safe_preset}-{digest}"


def _resolve_model_class_from_config(model_config: dict[str, Any]) -> Any:
    """Resolve diffusers/transformers model class from a saved ``config.json``."""
    class_name = model_config.get("_class_name") or model_config.get("architectures")
    if isinstance(class_name, list):
        class_name = class_name[0] if class_name else None
    if not class_name or not isinstance(class_name, str):
        return None
    for pkg_name in ("diffusers", "transformers"):
        try:
            pkg = __import__(pkg_name)
            cls = getattr(pkg, class_name, None)
            if cls is not None:
                return cls
        except ImportError:
            continue
    return None


def save_sdnq_cached_model(
    model: Any,
    target_dir: Path,
    *,
    meta: dict[str, Any] | None = None,
    max_shard_size: str = "5GB",
) -> Path:
    """Atomically save an SDNQ-quantized component into ``target_dir``.

    Writes into a sibling temporary directory first and swaps into place only
    after ``cache_meta.json`` is written, so an interrupted run never leaves a
    corrupt cache entry behind.
    """
    from sdnq.loader import save_sdnq_model

    target_dir = Path(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target_dir.parent / f".{target_dir.name}.tmp.{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        save_sdnq_model(model, str(tmp_dir), max_shard_size=max_shard_size)
        cache_meta = {
            "schema": SDNQ_CACHE_SCHEMA_VERSION,
            "complete": True,
            "sdnq_version": _sdnq_installed_version(),
            **(meta or {}),
        }
        (tmp_dir / "cache_meta.json").write_text(json.dumps(cache_meta, indent=2), encoding="utf-8")
        if target_dir.exists():
            # Outro worker gravou a mesma chave em paralelo — mantém a existente.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return target_dir
        os.replace(tmp_dir, target_dir)
        return target_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def load_sdnq_cached_model(
    target_dir: Path,
    *,
    model_cls: Any = None,
    torch_dtype: Any = None,
    device: str = "cpu",
    dequantize_fp32: bool | None = None,
    use_quantized_matmul: bool | None = None,
) -> Any | None:
    """Load a previously cached SDNQ-quantized component with bit-exact parity.

    Contorna um bug upstream do ``sdnq 0.2.9``: ``sdnq.loader.load_sdnq_model``
    chama ``sdnq_post_load_quant(**get_quant_args_from_config(...))``, o que
    descarta silenciosamente ``scale_dtype``/``zero_point_dtype`` (double quant)
    e faz o ``load_state_dict`` rebentar com ``Unexpected key: *.scale_2``.
    Passar o ``SDNQConfig`` completo directamente a ``sdnq_post_load_quant``
    (com ``quantization_device=None``/``return_device=None``) reconstrói o
    esqueleto SDNQ exacto sobre ``init_empty_weights()`` e carrega os pesos em
    ``strict=True`` sem perda nem re-quantização.

    Returns:
        Loaded quantized ``nn.Module``, or ``None`` on cache miss.
    """
    target_dir = Path(target_dir)
    meta_path = target_dir / "cache_meta.json"
    qcfg_path = target_dir / "quantization_config.json"
    cfg_path = target_dir / "config.json"
    if not (meta_path.is_file() and qcfg_path.is_file() and cfg_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta.get("complete") or meta.get("schema") != SDNQ_CACHE_SCHEMA_VERSION:
            return None
    except Exception:
        return None

    shard_files = sorted(target_dir.glob("*.safetensors"))
    if not shard_files:
        return None

    from accelerate import init_empty_weights
    from safetensors.torch import load_file
    from sdnq import SDNQConfig, sdnq_post_load_quant
    from sdnq.loader import apply_sdnq_options_to_model, post_process_model
    from sdnq.quantizer import QuantizationMethod, SDNQQuantizer

    model_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    qcfg_dict = json.loads(qcfg_path.read_text(encoding="utf-8"))
    qcfg_dict["is_training"] = False
    # Em init_empty_weights(), passar quantization_device/return_device tenta
    # mover meta tensors e levanta NotImplementedError — zerar aqui.
    qcfg_dict["quantization_device"] = None
    qcfg_dict["return_device"] = None
    qcfg_dict["add_skip_keys"] = False
    qcfg_dict["use_dynamic_quantization"] = False
    qcfg = SDNQConfig.from_dict(qcfg_dict)

    resolved_cls = model_cls or _resolve_model_class_from_config(model_config)
    if resolved_cls is None:
        raise ValueError(f"Não foi possível resolver a classe do modelo em {target_dir}")

    with init_empty_weights():
        if hasattr(resolved_cls, "load_config") and hasattr(resolved_cls, "from_config"):
            config = resolved_cls.load_config(str(target_dir))
            if hasattr(config, "quantization_config"):
                delattr(config, "quantization_config")
            if hasattr(config, "pop"):
                config.pop("quantization_config", None)
            model = resolved_cls.from_config(config)
        elif hasattr(resolved_cls, "_from_config"):
            from transformers import AutoConfig

            config = AutoConfig.from_pretrained(str(target_dir))
            if hasattr(config, "quantization_config"):
                delattr(config, "quantization_config")
            model = resolved_cls(config)
        else:
            clean_cfg = dict(model_config)
            clean_cfg.pop("quantization_config", None)
            model = resolved_cls(**clean_cfg)

        model = sdnq_post_load_quant(
            model,
            quantization_config=qcfg,
            torch_dtype=torch_dtype,
            pre_quantized=True,
        )

    state_dict: dict[str, Any] = {}
    for shard in shard_files:
        state_dict.update(load_file(str(shard), device=str(device)))

    tied = getattr(model, "_tied_weights_keys", None)
    if isinstance(tied, dict):
        for dst_key, src_key in tied.items():
            if src_key in state_dict and dst_key not in state_dict:
                state_dict[dst_key] = state_dict[src_key]
    elif (
        model.__class__.__name__ == "Qwen3ForCausalLM"
        and "lm_head.weight" not in state_dict
        and "model.embed_tokens.weight" in state_dict
    ):
        state_dict["lm_head.weight"] = state_dict["model.embed_tokens.weight"]

    model.load_state_dict(state_dict, strict=True, assign=True)
    del state_dict

    meta_left = [n for n, t in list(model.named_parameters()) + list(model.named_buffers()) if t.device.type == "meta"]
    if meta_left:
        raise RuntimeError(f"SDNQ cache deixou tensores em meta ({len(meta_left)}): {meta_left[:3]}")

    model = post_process_model(model)
    model.quantization_config = qcfg
    model.quantization_method = QuantizationMethod.SDNQ
    if hasattr(model, "config"):
        with contextlib.suppress(Exception):
            model.config.quantization_config = qcfg
        with contextlib.suppress(Exception):
            model.config["quantization_config"] = qcfg.to_dict()
    if hasattr(model, "hf_quantizer"):
        model.hf_quantizer.quantization_config = qcfg
    else:
        model.hf_quantizer = SDNQQuantizer(qcfg)

    need_opts = (dequantize_fp32 is not None and bool(dequantize_fp32) != bool(qcfg.dequantize_fp32)) or (
        use_quantized_matmul is not None and bool(use_quantized_matmul) != bool(qcfg.use_quantized_matmul)
    )
    if need_opts:
        model = apply_sdnq_options_to_model(
            model,
            dequantize_fp32=dequantize_fp32,
            use_quantized_matmul=use_quantized_matmul,
        )

    return model


# ---------------------------------------------------------------------------
# VRAM estimation
# ---------------------------------------------------------------------------

# Fatores medidos com `aigamekit-lab bench sdnq` (bits/peso / 16), com margem
# para camadas pequenas onde SVD/escalas pesam mais. Incluem escalas e SVD.
_COMPRESSION_FACTORS: dict[str, float] = {
    "fp16": 1.0,
    "fp8": 0.55,
    "uint8": 0.55,
    "int8": 0.55,
    "int4": 0.32,
    "uint4": 0.32,
    "int3": 0.24,
    "uint3": 0.24,
    "int2": 0.18,
    "uint2": 0.18,
}


def estimate_vram_mb(
    model_size_mb: float,
    preset: str = DEFAULT_PRESET,
) -> float:
    """Estimate peak VRAM (MB) after quantization for a given model size.

    Args:
        model_size_mb: Model size in MB (FP16).
        preset: SDNQ preset name.

    Returns:
        Estimated peak VRAM in MB.
    """
    if preset not in PRESETS:
        return model_size_mb * 1.5

    p = PRESETS[preset]
    factor = p.vram_factor if p.vram_factor is not None else _COMPRESSION_FACTORS.get(p.weights_dtype, 1.0)
    model_mb = model_size_mb * factor
    activation_overhead = 1.5
    return model_mb * activation_overhead


def suggest_preset_for_vram(vram_gb: float) -> str:
    """Suggest the best SDNQ preset for available VRAM.

    int4 é o piso para GPUs >=4 GB (o planner lowvram usa-o como quant de
    offload); int3/int2 são os degraus finos para GPUs ainda menores.

    Args:
        vram_gb: Available VRAM in GB.

    Returns:
        Preset name.
    """
    if vram_gb >= 8:
        return "sdnq-uint8"
    if vram_gb >= 4:
        return "sdnq-int4"
    if vram_gb >= 3:
        return "sdnq-int3"
    return "sdnq-int2"
