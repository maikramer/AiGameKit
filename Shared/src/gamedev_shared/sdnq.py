"""Centralized SDNQ (SD.Next Quantization) module for the GameDev monorepo.

Single source of truth for SDNQ quantization across all packages:
- Tested preset configurations (based on benchmarks across Text2D, Part3D, Paint3D)
- Pre-quantization of models at install time
- Runtime quantization application (post-load)
- Quantized matmul acceleration for pipelines

Default preset: ``sdnq-uint8`` — best tested across all models and GPUs.

References:
    - SDNQ library: https://github.com/Disty0/sdnq
    - Pre-quantized models: https://huggingface.co/collections/Disty0/sdnq
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Presets — validated by benchmarks in GameDevLab
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SDNQPreset:
    """Tested SDNQ quantization preset with all parameters for ``SDNQConfig``.

    These presets encode the best-tested configurations from benchmark sweeps
    across Text2D (FLUX Klein), Part3D (Hunyuan3D-Part DiT), and Paint3D
    (Hunyuan3D-Paint UNet).
    """

    name: str
    weights_dtype: str
    group_size: int
    use_svd: bool
    svd_rank: int
    svd_steps: int
    dequantize_fp32: bool
    description: str


PRESETS: dict[str, SDNQPreset] = {
    "sdnq-uint8": SDNQPreset(
        name="sdnq-uint8",
        weights_dtype="uint8",
        group_size=0,
        use_svd=False,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        description="SDNQ UINT8 — best tested, default for all models",
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
    ),
    "sdnq-int4": SDNQPreset(
        name="sdnq-int4",
        weights_dtype="int4",
        group_size=32,
        use_svd=True,
        svd_rank=32,
        svd_steps=8,
        dequantize_fp32=True,
        description="SDNQ INT4 — maximum compression for low VRAM GPUs",
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


def _check_cuda() -> bool:
    """Check if CUDA is available (lazy import)."""
    try:
        import torch

        return torch.cuda.is_available()  # type: ignore[no-any-return]
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# SDNQConfig factory
# ---------------------------------------------------------------------------


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
        preset: Preset name (``"sdnq-uint8"``, ``"sdnq-int8"``, ``"sdnq-int4"``, ``"sdnq-fp8"``).
        quantization_device: Device for quantization (default: ``"cuda"`` if available).
        return_device: Device after quantization (default: same as ``quantization_device``).
        use_quantized_matmul: Enable Triton/CUDA quantized matmul (default: auto-detect).
        modules_to_not_convert: Module names to skip during quantization.
        **overrides: Additional keyword args forwarded to ``SDNQConfig``.

    Returns:
        ``SDNQConfig`` instance.

    Raises:
        ImportError: If ``sdnq`` is not installed.
        KeyError: If preset name is unknown.
    """
    if preset not in PRESETS:
        raise KeyError(f"Unknown SDNQ preset: {preset!r}. Available: {', '.join(PRESETS)}")

    from sdnq import SDNQConfig
    from sdnq.common import use_torch_compile

    p = PRESETS[preset]

    device = quantization_device or ("cuda" if _check_cuda() else "cpu")
    ret_device = return_device or device
    matmul = use_quantized_matmul if use_quantized_matmul is not None else bool(use_torch_compile)

    kwargs: dict[str, Any] = {
        "weights_dtype": p.weights_dtype,
        "group_size": p.group_size,
        "use_svd": p.use_svd,
        "svd_rank": p.svd_rank,
        "svd_steps": p.svd_steps,
        "use_quantized_matmul": matmul,
        "quantization_device": device,
        "return_device": ret_device,
        "dequantize_fp32": p.dequantize_fp32,
    }
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
    import inspect

    supported = set(inspect.signature(sdnq_post_load_quant).parameters.keys()) - {"model"}
    config_vars = {k: v for k, v in vars(config).items() if k in supported}
    return sdnq_post_load_quant(model, **config_vars)


def apply_quantized_matmul(pipe: Any, *, enabled: bool = True) -> None:
    """Apply quantized matmul acceleration to pipeline sub-modules in-place.

    For diffusers pipelines (e.g., FLUX), enables Triton/CUDA quantized
    matmul on transformer, text_encoder, and text_encoder_2.

    Args:
        pipe: Diffusion pipeline with sub-modules to accelerate.
        enabled: Whether to enable (default: ``True``).
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
        for name in ("transformer", "text_encoder", "text_encoder_2"):
            mod = getattr(pipe, name, None)
            if mod is not None:
                setattr(pipe, name, apply_sdnq_options_to_model(mod, use_quantized_matmul=True))
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

    quant_meta = {
        "quantization": f"sdnq-{PRESETS[preset].weights_dtype}",
        "preset": preset,
        "weights_dtype": PRESETS[preset].weights_dtype,
        "group_size": PRESETS[preset].group_size,
        "use_svd": PRESETS[preset].use_svd,
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
# VRAM estimation
# ---------------------------------------------------------------------------

_COMPRESSION_FACTORS: dict[str, float] = {
    "fp16": 1.0,
    "fp8": 0.5,
    "uint8": 0.5,
    "int8": 0.5,
    "int4": 0.25,
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

    factor = _COMPRESSION_FACTORS.get(PRESETS[preset].weights_dtype, 1.0)
    model_mb = model_size_mb * factor
    activation_overhead = 1.5
    return model_mb * activation_overhead


def suggest_preset_for_vram(vram_gb: float) -> str:
    """Suggest the best SDNQ preset for available VRAM.

    Args:
        vram_gb: Available VRAM in GB.

    Returns:
        Preset name.
    """
    if vram_gb >= 8:
        return "sdnq-uint8"
    if vram_gb >= 6:
        return "sdnq-uint8"
    return "sdnq-int4"
