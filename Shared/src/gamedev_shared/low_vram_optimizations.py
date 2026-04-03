"""
Otimizações agressivas para GPUs com 6GB VRAM ou menos.

Específico para: RTX 4050, RTX 3050, RTX 2060, GTX 1660 Ti, etc.
Técnicas: xformers, SDPA, BF16, chunking agressivo, group offloading.
"""

from __future__ import annotations

import os
import warnings
from typing import Any


def is_xformers_available() -> bool:
    """Verifica se xformers está instalado."""
    try:
        import xformers  # noqa: F401

        return True
    except ImportError:
        return False


def enable_xformers_memory_efficient_attention(
    model: Any,
    attention_op: str | None = None,
) -> bool:
    """
    Habilita xformers memory efficient attention em um modelo.

    Args:
        model: Modelo com método enable_xformers_memory_efficient_attention
        attention_op: Operação específica (None para default)

    Returns:
        True se habilitado com sucesso
    """
    if not is_xformers_available():
        return False

    try:
        if hasattr(model, "enable_xformers_memory_efficient_attention"):
            if attention_op:
                model.enable_xformers_memory_efficient_attention(attention_op=attention_op)
            else:
                model.enable_xformers_memory_efficient_attention()
            return True
    except Exception as e:
        warnings.warn(f"xformers não pôde ser habilitado: {e}", stacklevel=2)

    return False


def set_sdpa_backend(
    backend: str = "flash_attention_2",
    enable_math_fallback: bool = True,
) -> None:
    """
    Configura o backend de SDPA (Scaled Dot Product Attention) do PyTorch.

    Args:
        backend: "flash_attention_2", "mem_efficient", "math"
        enable_math_fallback: Permitir fallback para math backend
    """
    try:
        import torch.nn.attention as attention

        # PyTorch 2.2+
        if hasattr(attention, "sdpa_kernel"):
            if backend == "flash_attention_2":
                attention.sdpa_kernel([attention.SDPBackend.FLASH_ATTENTION])
            elif backend == "mem_efficient":
                attention.sdpa_kernel([attention.SDPBackend.EFFICIENT_ATTENTION])
            elif backend == "math":
                attention.sdpa_kernel([attention.SDPBackend.MATH])
    except Exception:
        pass


def get_optimal_dtype_for_gpu(gpu_name: str = "") -> str:
    """
    Retorna o dtype ótimo baseado na arquitetura da GPU.

    Args:
        gpu_name: Nome da GPU (ex: "NVIDIA GeForce RTX 4050")

    Returns:
        "bfloat16", "float16", ou "float32"
    """
    gpu_lower = gpu_name.lower()

    # RTX 40 series (Ada Lovelace) - BF16 tem melhor performance
    if "rtx 40" in gpu_lower:
        return "bfloat16"

    # RTX 30 series (Ampere) - BF16 também bom
    if "rtx 30" in gpu_lower:
        return "bfloat16"

    # GPUs mais antigas - FP16 é mais seguro
    if "rtx" in gpu_lower or "gtx 16" in gpu_lower:
        return "float16"

    # Default
    return "float16"


def configure_cuda_for_6gb() -> None:
    """
    Configurações específicas do CUDA allocator para 6GB VRAM.
    Deve ser chamada antes de inicializar PyTorch CUDA.
    """
    # Expandable segments: essencial para evitar OOM
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = (
        "expandable_segments:True,max_split_size_mb:64,garbage_collection_threshold:0.6"
    )

    # Desativar caching de memória para operações cuDNN (economiza VRAM)
    os.environ["CUDNN_DETERMINISTIC"] = "1"

    # Usar memória de workspace mais eficiente para cuBLAS
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"


def configure_cuda_for_4gb() -> None:
    """Configurações ainda mais agressivas para 4GB VRAM."""
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = (
        "expandable_segments:True,max_split_size_mb:32,garbage_collection_threshold:0.8"
    )
    os.environ["CUDNN_DETERMINISTIC"] = "1"
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"


def get_6gb_vram_profile() -> dict[str, Any]:
    """
    Retorna perfil completo de otimizações para GPUs com 6GB VRAM.

    Returns:
        Dict com todas as configurações recomendadas
    """
    return {
        "dtype": "bfloat16",
        "quantization": "int4",  # NF4 é essencial para 6GB
        "attention": "xformers",  # ou "sdpa_flash" se xformers não disponível
        "vae_slicing": True,
        "vae_tiling": True,
        "vae_tile_size": 128,  # Menor tile para economizar VRAM
        "enable_tiny_vae": True,
        "torch_compile": False,  # Compilação usa VRAM extra, desabilitar em 6GB
        "cpu_offload": "sequential",  # Mais agressivo que model offload
        "max_split_size_mb": 64,
        "batch_size": 1,  # Sempre 1 para 6GB
        "gradient_checkpointing": False,  # Só para treino
        "enable_vae_slicing": True,
        "enable_attention_slicing": True,
        "attention_slice_size": 1,  # Mínimo
    }


def get_4gb_vram_profile() -> dict[str, Any]:
    """Perfil extremo para 4GB VRAM."""
    return {
        "dtype": "bfloat16",
        "quantization": "int4",
        "attention": "xformers",
        "vae_slicing": True,
        "vae_tiling": True,
        "vae_tile_size": 64,  # Muito pequeno
        "enable_tiny_vae": True,
        "torch_compile": False,
        "cpu_offload": "sequential",
        "max_split_size_mb": 32,
        "batch_size": 1,
        "enable_vae_slicing": True,
        "enable_attention_slicing": True,
        "attention_slice_size": 1,
        "force_seq_cpu_offload": True,
    }


def apply_memory_efficient_settings(vram_gb: float) -> dict[str, Any]:
    """
    Aplica configurações de memória baseado na VRAM detectada.

    Args:
        vram_gb: VRAM disponível em GB

    Returns:
        Dict com configurações aplicadas
    """
    if vram_gb <= 4:
        configure_cuda_for_4gb()
        return get_4gb_vram_profile()
    elif vram_gb <= 6:
        configure_cuda_for_6gb()
        return get_6gb_vram_profile()
    elif vram_gb <= 8:
        # Perfil para 8GB (menos agressivo)
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:128"
        return {
            "dtype": "bfloat16",
            "quantization": "int8",
            "attention": "sdpa_flash",
            "vae_slicing": True,
            "vae_tiling": True,
            "vae_tile_size": 256,
            "enable_tiny_vae": False,
            "torch_compile": True,
            "cpu_offload": "model",
        }
    else:
        # 12GB+
        return {
            "dtype": "bfloat16",
            "quantization": "none",
            "attention": "sdpa_flash",
            "torch_compile": True,
            "cpu_offload": None,
        }


def enable_group_offloading(pipe: Any, num_model_slices: int = 1) -> bool:
    """
    Habilita group offloading quando disponível (diffusers mais recente).

    Args:
        pipe: Pipeline diffusers
        num_model_slices: Número de slices do modelo

    Returns:
        True se habilitado
    """
    try:
        # Group offloading é uma feature mais recente do diffusers
        if hasattr(pipe, "enable_group_offload"):
            pipe.enable_group_offload(num_model_slices=num_model_slices)
            return True
        elif hasattr(pipe, "enable_model_cpu_offload"):
            # Fallback para model cpu offload
            pipe.enable_model_cpu_offload()
            return True
    except Exception:
        pass
    return False


def enable_sequential_offloading_optimized(pipe: Any) -> bool:
    """
    Habilita sequential CPU offload com otimizações para 6GB.

    Args:
        pipe: Pipeline diffusers

    Returns:
        True se habilitado
    """
    try:
        if hasattr(pipe, "enable_sequential_cpu_offload"):
            pipe.enable_sequential_cpu_offload()
            return True
    except Exception:
        pass
    return False


def apply_chunking_to_transformer(
    model: Any,
    chunk_size: int = 1,
) -> bool:
    """
    Aplica chunking ao transformer para processar tokens em batches menores.

    Args:
        model: Modelo transformer
        chunk_size: Tamanho do chunk (1 = mínimo)

    Returns:
        True se aplicado
    """
    try:
        # Alguns modelos suportam chunking via atributo
        if hasattr(model, "chunk_size"):
            model.chunk_size = chunk_size
            return True
        if hasattr(model, "set_chunk_size"):
            model.set_chunk_size(chunk_size)
            return True
    except Exception:
        pass
    return False


def get_recommended_batch_size(vram_gb: float, image_size: int = 512) -> int:
    """
    Retorna batch size recomendado baseado na VRAM e tamanho da imagem.

    Args:
        vram_gb: VRAM em GB
        image_size: Tamanho da imagem (512, 768, 1024)

    Returns:
        Batch size recomendado (1, 2, 4)
    """
    # Multiplicador baseado na resolução
    size_multiplier = (image_size / 512) ** 2

    if vram_gb <= 6:
        return 1
    elif vram_gb <= 8:
        return 1 if size_multiplier > 2 else 2
    elif vram_gb <= 12:
        return 2 if size_multiplier > 1 else 4
    else:
        return 4


class RTX4050Optimizer:
    """
    Otimizador específico para RTX 4050 6GB.

    Aplica todas as otimizações necessárias para esta GPU.
    """

    def __init__(self) -> None:
        self.vram_gb = 6.0
        self.dtype = "bfloat16"
        self.compute_capability = (8, 9)  # Ada Lovelace

    def setup(self) -> dict[str, Any]:
        """Configura ambiente completo para RTX 4050."""
        # Configurar ambiente CUDA
        configure_cuda_for_6gb()

        # Retornar configurações
        return {
            "dtype": self.dtype,
            "use_fp8": True,  # RTX 4050 suporta FP8
            "quantization": "int4",
            "attention_backend": "xformers",
            "enable_vae_slicing": True,
            "enable_vae_tiling": True,
            "vae_tile_size": 128,
            "enable_tiny_vae": True,
            "cpu_offload": "sequential",
            "torch_compile": False,  # Desabilitado para economizar VRAM
            "batch_size": 1,
        }

    def apply_to_pipeline(self, pipe: Any) -> None:
        """Aplica otimizações a um pipeline diffusers."""
        try:
            # xformers
            if is_xformers_available():
                enable_xformers_memory_efficient_attention(pipe)

            # VAE otimizações
            if hasattr(pipe, "vae"):
                if hasattr(pipe.vae, "enable_slicing"):
                    pipe.vae.enable_slicing()
                if hasattr(pipe.vae, "enable_tiling"):
                    pipe.vae.enable_tiling(tile_sample_min_size=128)

            # Sequential offload
            enable_sequential_offloading_optimized(pipe)

        except Exception as e:
            warnings.warn(f"Erro ao aplicar otimizações RTX 4050: {e}", stacklevel=2)


def detect_gpu_and_optimize() -> dict[str, Any]:
    """
    Detecta GPU e aplica otimizações automaticamente.

    Returns:
        Dict com configurações aplicadas
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return {"device": "cpu", "optimizations": []}

        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024**3)
        gpu_name = props.name

        # Verificar se é RTX 4050 ou similar
        if "rtx 4050" in gpu_name.lower() or vram_gb <= 6.5:
            optimizer = RTX4050Optimizer()
            config = optimizer.setup()
            config["gpu_name"] = gpu_name
            config["vram_gb"] = vram_gb
            return config

        # Outras GPUs
        return apply_memory_efficient_settings(vram_gb)

    except Exception:
        return {"device": "cpu"}


# Backwards compatibility
get_optimization_profile = apply_memory_efficient_settings
