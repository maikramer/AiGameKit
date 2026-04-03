"""
Text3D — Text-to-3D com Text2D + Hunyuan3D-2.1 SDNQ INT4 (image-to-3D).

Descarrega o modelo 2D antes de carregar o Hunyuan3D. Padrões de inferência em
``text3d.defaults`` (perfil ~6-8GB VRAM CUDA com SDNQ INT4); constantes ``HUNYUAN_HQ_*`` para GPU grande.

Textura e PBR: CLI ``paint3d`` ou orquestração ``gameassets`` — não fazem parte deste pacote.
"""

__version__ = "0.1.0"
__author__ = "Text3D Project"

from . import defaults
from .generator import HunyuanTextTo3DGenerator

__all__ = [
    "HunyuanTextTo3DGenerator",
    "defaults",
]
