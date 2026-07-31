"""Funções utilitárias para Texture2D — shims sobre ``gamedev_shared``.

As validações vivem em ``gamedev_shared.validation`` (os defaults do shared
já são os do Texture2D: max_length 500, guidance 7.5, steps 10-100, 1024²).
Aqui ficam só os re-exports históricos.
"""

from __future__ import annotations

from gamedev_shared.gpu import format_bytes  # noqa: F401
from gamedev_shared.path_utils import ensure_directory  # noqa: F401
from gamedev_shared.seed_utils import generate_seed  # noqa: F401
from gamedev_shared.validation import (  # noqa: F401
    format_timestamp,
    validate_dimensions,
    validate_params,
    validate_prompt,
)
