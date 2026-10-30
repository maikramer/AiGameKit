"""SprongMotion-14M runtime bridge (CPU-only text-to-motion).

Public API:
    ``Model(path)`` — load a GGUF model; ``.generate(prompt, seconds=…)`` → :class:`Clip`.
    ``load_library()`` — ctypes handle (built from the vendored C sources on first use).
    ``ensure_runtime()`` — build/locate ``libsprongmotion`` without loading a model.

Example:
    >>> from motion3d.sprong import Model           # doctest: +SKIP
    >>> with Model("SprongMotion-14M-v1.0-Q8_0.gguf") as model:   # doctest: +SKIP
    ...     clip = model.generate("a person walks forward", seconds=2, seed=7)
    ...     clip.joints.shape
    (40, 22, 3)
"""

from __future__ import annotations

from .bindings import (
    Clip,
    GenerateOptions,
    Model,
    SmMotion,
    SmOptions,
    SprongError,
    last_error,
    load_library,
    set_thread_count,
)
from .runtime import (
    RuntimeBuildError,
    build_runtime,
    ensure_runtime,
    library_path,
    runtime_info,
    vendored_version,
)

__all__ = [
    "Clip",
    "GenerateOptions",
    "Model",
    "RuntimeBuildError",
    "SmMotion",
    "SmOptions",
    "SprongError",
    "build_runtime",
    "ensure_runtime",
    "last_error",
    "library_path",
    "load_library",
    "runtime_info",
    "set_thread_count",
    "vendored_version",
]
