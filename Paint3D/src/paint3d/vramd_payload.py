"""Builders de request vramd para Paint3D texture (CLI + GameAssets).

A implementação vive em :mod:`aigamekit_shared.vramd_paint_payload` para o
GameAssets montar as waves sem importar o pacote Paint3D (que puxa torch).
Este módulo mantém a API histórica ``paint3d.vramd_payload``.
"""

from __future__ import annotations

from aigamekit_shared.vramd_paint_payload import (  # noqa: F401
    _coerce_ref_azims,
    _validate_multi_ref,
    build_texture_request,
)

__all__ = ["build_texture_request"]
