"""Builders de request vramd para Text3D generate (CLI + GameAssets).

A implementação vive em :mod:`aigamekit_shared.vramd_shape_payload` para o
GameAssets montar as waves sem importar o pacote Text3D (que puxa torch).
Este módulo mantém a API histórica ``text3d.vramd_payload``.
"""

from __future__ import annotations

from aigamekit_shared.vramd_shape_payload import build_generate_request, quant_for_mib

__all__ = ["build_generate_request", "quant_for_mib"]
