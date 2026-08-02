"""T2M-GPT model components."""

from __future__ import annotations

from .t2m_trans import Text2Motion_Transformer
from .vqvae import HumanVQVAE

__all__ = ["HumanVQVAE", "Text2Motion_Transformer"]
