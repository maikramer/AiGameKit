"""
Text2D — Text-to-2D com FLUX.3 Image Klein (SDNQ runtime).
"""

__version__ = "0.1.0"
__author__ = "Text2D Project"

from .generator import (
    DEFAULT_MODEL_ID,
    LOW_VRAM_MODEL_ID,
    MAX_REFS,
    KleinFluxGenerator,
    default_model_id,
)

__all__ = [
    "DEFAULT_MODEL_ID",
    "LOW_VRAM_MODEL_ID",
    "MAX_REFS",
    "KleinFluxGenerator",
    "default_model_id",
]
