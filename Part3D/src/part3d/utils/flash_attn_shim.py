"""
Shim para flash_attn usando PyTorch SDPA nativo (torch >= 2.0).

Substitui ``flash_attn.flash_attn_varlen_qkvpacked_func`` por uma implementação
baseada em ``torch.nn.functional.scaled_dot_product_attention``, evitando a
necessidade de compilar o pacote flash-attn (CUDA C++ build complexo).

Regista-se como módulo ``flash_attn`` (+ ``flash_attn.flash_attn_interface``)
em ``sys.modules`` antes que XPart/Sonata/xformers tentem importá-lo.

Nota xformers: ``find_spec("flash_attn")`` encontra o shim; sem o submodule
``flash_attn_interface`` o import de ``xformers.ops`` rebenta. Versão
``0.0.0`` faz o xformers saltar o registo de custom ops CUDA (usa PT flash).
"""

from __future__ import annotations

import importlib.machinery
import os
import sys
import types

import torch
import torch.nn.functional as F

_SHIM_MARKER = "_gamedev_sdpa_shim"


def flash_attn_varlen_qkvpacked_func(
    qkv: torch.Tensor,
    cu_seqlens: torch.Tensor,
    max_seqlen: int,
    dropout_p: float = 0.0,
    softmax_scale: float | None = None,
    causal: bool = False,
    **kwargs,
) -> torch.Tensor:
    """Reimplementação de ``flash_attn.flash_attn_varlen_qkvpacked_func``
    usando PyTorch SDPA nativo.

    Args:
        qkv: (total_tokens, 3, num_heads, head_dim)
        cu_seqlens: (batch+1,) cumulative sequence lengths
        max_seqlen: maximum sequence length (patch_size)
        dropout_p: dropout probability
        softmax_scale: scaling factor (default: 1/sqrt(head_dim))
    Returns:
        output: (total_tokens, num_heads, head_dim)
    """
    total_tokens, three, num_heads, head_dim = qkv.shape
    assert three == 3

    q, k, v = qkv.unbind(dim=1)  # each (total_tokens, num_heads, head_dim)

    batch_size = cu_seqlens.shape[0] - 1

    # Pad sequences into a (batch, max_seqlen, heads, dim) tensor for SDPA
    q_padded = torch.zeros(batch_size, max_seqlen, num_heads, head_dim, dtype=q.dtype, device=q.device)
    k_padded = torch.zeros_like(q_padded)
    v_padded = torch.zeros_like(q_padded)
    mask = torch.zeros(batch_size, max_seqlen, dtype=torch.bool, device=q.device)

    for i in range(batch_size):
        start = cu_seqlens[i].item()
        end = cu_seqlens[i + 1].item()
        seq_len = end - start
        q_padded[i, :seq_len] = q[start:end]
        k_padded[i, :seq_len] = k[start:end]
        v_padded[i, :seq_len] = v[start:end]
        mask[i, :seq_len] = True

    # SDPA expects (batch, heads, seq_len, head_dim)
    q_sdpa = q_padded.transpose(1, 2)
    k_sdpa = k_padded.transpose(1, 2)
    v_sdpa = v_padded.transpose(1, 2)

    # Build attention mask: (batch, 1, 1, seq_len) for broadcasting
    attn_mask = mask.unsqueeze(1).unsqueeze(2)  # (batch, 1, 1, seq_len)

    out = F.scaled_dot_product_attention(
        q_sdpa.float() if q_sdpa.dtype == torch.float16 else q_sdpa,
        k_sdpa.float() if k_sdpa.dtype == torch.float16 else k_sdpa,
        v_sdpa.float() if v_sdpa.dtype == torch.float16 else v_sdpa,
        attn_mask=attn_mask.expand(-1, num_heads, max_seqlen, -1),
        dropout_p=dropout_p if q_sdpa.requires_grad else 0.0,
        scale=softmax_scale,
        is_causal=causal,
    )

    # Back to original dtype
    out = out.to(qkv.dtype)

    # (batch, heads, seq_len, dim) -> unpad to (total_tokens, heads, dim)
    out = out.transpose(1, 2)  # (batch, seq_len, heads, dim)
    result = torch.zeros(total_tokens, num_heads, head_dim, dtype=qkv.dtype, device=qkv.device)
    for i in range(batch_size):
        start = cu_seqlens[i].item()
        end = cu_seqlens[i + 1].item()
        seq_len = end - start
        result[start:end] = out[i, :seq_len]

    return result


def install_shim() -> None:
    """Injeta ``flash_attn`` + ``flash_attn.flash_attn_interface`` em ``sys.modules``."""
    # xformers: versão 0.0.0 → não regista custom ops; ignore check evita ImportError.
    os.environ.setdefault("XFORMERS_IGNORE_FLASH_VERSION_CHECK", "1")

    if "flash_attn" in sys.modules and getattr(sys.modules["flash_attn"], _SHIM_MARKER, False):
        return

    mod = types.ModuleType("flash_attn")
    # "0.0.0" → xformers.ops.fmha.flash salta o bloco de custom ops CUDA.
    mod.__version__ = "0.0.0"
    setattr(mod, _SHIM_MARKER, True)
    mod.flash_attn_varlen_qkvpacked_func = flash_attn_varlen_qkvpacked_func
    mod.__spec__ = importlib.machinery.ModuleSpec("flash_attn", None)
    mod.__path__ = []
    mod.__file__ = __file__
    sys.modules["flash_attn"] = mod

    iface = types.ModuleType("flash_attn.flash_attn_interface")
    iface.__spec__ = importlib.machinery.ModuleSpec("flash_attn.flash_attn_interface", None)
    # Atributo mínimo para o probe do xformers (nunca chamado com version 0.0.0).
    iface.flash_attn_gpu = object()
    sys.modules["flash_attn.flash_attn_interface"] = iface
    mod.flash_attn_interface = iface
