"""
Módulo de quantização compartilhado para otimização de VRAM.

Suporta:
- SDNQ (SD.Next Quantization) — centralized in ``gamedev_shared.sdnq``
- bitsandbytes (4-bit NF4/FP4, 8-bit)
- torchao (quantização nativa PyTorch)
- optimum-quanto (quanto)
- FP8 (para GPUs NVIDIA RTX 40 series)
"""

from __future__ import annotations

import os
from typing import Any


def is_bitsandbytes_available() -> bool:
    """Verifica se bitsandbytes está instalado."""
    try:
        import bitsandbytes  # noqa: F401

        return True
    except ImportError:
        return False


def is_torchao_available() -> bool:
    """Verifica se torchao está instalado."""
    try:
        import torchao  # noqa: F401

        return True
    except ImportError:
        return False


def is_quanto_available() -> bool:
    """Verifica se optimum-quanto está instalado."""
    try:
        from optimum import quanto  # noqa: F401

        return True
    except ImportError:
        return False


def is_sdnq_available() -> bool:
    """Verifica se SDNQ (SD.Next Quantization) está instalado."""
    from gamedev_shared.sdnq import is_available as _sdnq_available

    return _sdnq_available()


def get_gpu_compute_capability() -> tuple[int, int] | None:
    """Retorna a compute capability da GPU (major, minor) ou None."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        props = torch.cuda.get_device_properties(0)
        return (props.major, props.minor)
    except Exception:
        return None


def supports_fp8() -> bool:
    """Verifica se a GPU suporta FP8 (RTX 40 series ou superior)."""
    cc = get_gpu_compute_capability()
    if cc is None:
        return False
    # FP8 suportado a partir de compute capability 8.9 (Ada Lovelace)
    return cc >= (8, 9)


def get_quantization_config(
    quantization_mode: str = "auto",
    compute_dtype: str = "float16",
) -> dict[str, Any] | None:
    """
    Retorna configuração de quantização baseada no modo.

    Args:
        quantization_mode: "auto", "none", "fp8", "int8", "int4", "quanto-int8", "quanto-int4"
        compute_dtype: dtype para computação ("float16", "bfloat16", "float32")

    Returns:
        Dict com configuração ou None se sem quantização
    """
    mode = quantization_mode.lower().strip()

    if mode == "none" or (mode == "auto" and not is_bitsandbytes_available()):
        return None

    # FP8 para GPUs RTX 40 series
    if (mode == "fp8" or (mode == "auto" and supports_fp8())) and supports_fp8():
        return {
            "type": "fp8",
            "compute_dtype": compute_dtype,
        }

    # bitsandbytes 4-bit
    if (mode == "int4" or mode == "4bit") and is_bitsandbytes_available():
        from transformers import BitsAndBytesConfig

        return {
            "type": "bitsandbytes-4bit",
            "config": BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=getattr(__import__("torch"), compute_dtype),
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            ),
        }

    # bitsandbytes 8-bit
    if (mode == "int8" or mode == "8bit" or mode == "auto") and is_bitsandbytes_available():
        from transformers import BitsAndBytesConfig

        return {
            "type": "bitsandbytes-8bit",
            "config": BitsAndBytesConfig(load_in_8bit=True),
        }

    # quanto int8
    if mode == "quanto-int8" and is_quanto_available():
        from optimum import quanto

        return {
            "type": "quanto-int8",
            "config": quanto.qfloat8,
        }

    # quanto int4
    if mode == "quanto-int4" and is_quanto_available():
        from optimum import quanto

        return {
            "type": "quanto-int4",
            "config": quanto.qint4,
        }

    # SDNQ — delegate to centralized module
    if mode.startswith("sdnq"):
        from gamedev_shared.sdnq import PRESETS, create_config

        if is_sdnq_available() and mode in PRESETS:
            config = create_config(mode)
            return {
                "type": mode,
                "config": config,
            }

    return None


def apply_torchao_quantization(
    model: Any,
    mode: str = "int4_weight_only",
) -> Any:
    """
    Aplica quantização torchao a um modelo.

    Args:
        model: Modelo PyTorch
        mode: "int4_weight_only", "int8_weight_only", "int8_dynamic"

    Returns:
        Modelo quantizado
    """
    if not is_torchao_available():
        return model

    try:
        from torchao.quantization import (
            int4_weight_only,
            int8_dynamic_activation_int8_weight,
            int8_weight_only,
            quantize,
        )

        quantizers = {
            "int4_weight_only": int4_weight_only,
            "int8_weight_only": int8_weight_only,
            "int8_dynamic": int8_dynamic_activation_int8_weight,
        }

        if mode in quantizers:
            quantize(model, quantizers[mode]())
            return model
    except Exception:
        pass

    return model


def get_suggested_quantization_for_vram(
    vram_gb: float,
    model_size_gb: float = 4.0,
) -> str:
    """
    Sugere modo de quantização baseado na VRAM disponível.

    Prefers SDNQ uint8 for most cases (best tested), falls back to int4
    for low VRAM.

    Args:
        vram_gb: VRAM disponível em GB
        model_size_gb: Tamanho do modelo em GB (FP32)

    Returns:
        Modo de quantização sugerido
    """
    from gamedev_shared.sdnq import suggest_preset_for_vram

    fp16_size = model_size_gb / 2
    required_vram = fp16_size * 1.5

    if vram_gb >= required_vram:
        return "none"
    if is_sdnq_available():
        return suggest_preset_for_vram(vram_gb)
    if vram_gb >= required_vram * 0.6 and supports_fp8():
        return "fp8"
    if vram_gb >= required_vram * 0.5:
        return "int8"
    return "int4"


def format_quantization_info(quant_config: dict[str, Any] | None) -> str:
    """Formata informações de quantização para exibição."""
    if quant_config is None:
        return "sem quantização (FP16/FP32)"

    qtype = quant_config.get("type", "unknown")

    descriptions = {
        "fp8": "FP8 (8-bit floating point)",
        "bitsandbytes-4bit": "BitsAndBytes 4-bit (NF4)",
        "bitsandbytes-8bit": "BitsAndBytes 8-bit",
        "quanto-int8": "Quanto INT8",
        "quanto-int4": "Quanto INT4",
        "sdnq-int8": "SDNQ INT8 (modern)",
        "sdnq-uint8": "SDNQ UINT8 (modern unsigned)",
        "sdnq-int4": "SDNQ INT4 (modern aggressive)",
    }

    return descriptions.get(qtype, f"Quantização: {qtype}")


# ---------------------------------------------------------------------------
# Helpers para diffusers
# ---------------------------------------------------------------------------


def enable_vae_optimizations(
    vae: Any,
    enable_slicing: bool = True,
    enable_tiling: bool = True,
    tile_sample_min_size: int = 256,
) -> None:
    """
    Habilita otimizações de VAE para imagens grandes.

    Args:
        vae: VAE model
        enable_slicing: Habilita slicing para batch processing
        enable_tiling: Habilita tiling para imagens grandes
        tile_sample_min_size: Tamanho mínimo do tile
    """
    if enable_slicing and hasattr(vae, "enable_slicing"):
        vae.enable_slicing()

    if enable_tiling and hasattr(vae, "enable_tiling"):
        if tile_sample_min_size:
            try:
                vae.enable_tiling(tile_sample_min_size=tile_sample_min_size)
            except TypeError:
                vae.enable_tiling()
        else:
            vae.enable_tiling()


def enable_attention_optimizations(
    pipe: Any,
    enable_slicing: bool = True,
    slicing_size: int | str = "auto",
) -> None:
    """
    Habilita otimizações de attention para reduzir pico de VRAM.

    Args:
        pipe: Diffusion pipeline
        enable_slicing: Habilita attention slicing
        slicing_size: Tamanho do slice ("auto", 1, 2, etc)
    """
    if enable_slicing:
        if hasattr(pipe, "enable_attention_slicing"):
            if slicing_size == "auto":
                pipe.enable_attention_slicing()
            else:
                pipe.enable_attention_slicing(slicing_size)

        # VAE attention slicing
        if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()


def enable_model_cpu_offload_optimized(
    pipe: Any,
    device: str = "cuda",
    use_sequential: bool = False,
) -> None:
    """
    Habilita CPU offloading com otimizações.

    Args:
        pipe: Diffusion pipeline
        device: Device para offload
        use_sequential: Usar sequential offload (mais lento mas economiza mais VRAM)
    """
    if use_sequential and hasattr(pipe, "enable_sequential_cpu_offload"):
        pipe.enable_sequential_cpu_offload(device=device)
    elif hasattr(pipe, "enable_model_cpu_offload"):
        pipe.enable_model_cpu_offload(device=device)


def apply_torch_compile(
    model: Any,
    mode: str = "reduce-overhead",
    fullgraph: bool = False,
) -> Any:
    """
    Aplica torch.compile a um modelo se disponível.

    Args:
        model: Modelo PyTorch
        mode: Modo de compilação ("default", "reduce-overhead", "max-autotune")
        fullgraph: Forçar graph completo (sem breaks)

    Returns:
        Modelo compilado ou original se torch.compile não disponível
    """
    try:
        import torch

        if hasattr(torch, "compile"):
            return torch.compile(model, mode=mode, fullgraph=fullgraph)
    except Exception:
        pass

    return model


def get_torch_compile_recommendation(model_name: str = "") -> dict[str, Any]:
    """
    Retorna recomendações de torch.compile baseado no modelo.

    Args:
        model_name: Nome do modelo (ex: "unet", "vae", "transformer")

    Returns:
        Dict com configurações recomendadas
    """
    configs = {
        "unet": {
            "mode": "reduce-overhead",
            "fullgraph": False,
            "dynamic": True,
        },
        "transformer": {
            "mode": "reduce-overhead",
            "fullgraph": False,
            "dynamic": True,
        },
        "vae": {
            "mode": "default",
            "fullgraph": False,
            "dynamic": False,
        },
    }

    return configs.get(model_name.lower(), {"mode": "reduce-overhead", "fullgraph": False})


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def set_memory_optimization_env(
    enable_expandable_segments: bool = True,
    enable_cudnn_benchmark: bool = True,
) -> None:
    """
    Configura variáveis de ambiente para otimização de memória.

    Args:
        enable_expandable_segments: Habilita expandable segments no allocator CUDA
        enable_cudnn_benchmark: Habilita cudnn.benchmark para convoluções
    """
    if enable_expandable_segments:
        current = os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "")
        if "expandable_segments" not in current:
            if current:
                os.environ["PYTORCH_CUDA_ALLOC_CONF"] = f"{current},expandable_segments:True"
            else:
                os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    if enable_cudnn_benchmark:
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"


def suggest_environment_variables(vram_gb: float) -> dict[str, str]:
    """
    Sugere variáveis de ambiente baseado na VRAM.

    Args:
        vram_gb: VRAM disponível em GB

    Returns:
        Dict com variáveis e valores sugeridos
    """
    suggestions: dict[str, str] = {}

    # Sempre habilitar expandable segments
    suggestions["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    if vram_gb < 8:
        # Baixa VRAM: mais agressivo
        suggestions["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:128"
        suggestions["CUDA_LAUNCH_BLOCKING"] = "0"
    elif vram_gb < 16:
        # VRAM média
        suggestions["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:512"

    return suggestions
