from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def weld_glb(path: str | Path) -> None:
    """No-op: kept for backward compatibility.

    GLTF format naturally requires split vertices for UV seams,
    normals, and multiple texture coordinate sets. Welding destroys
    this data. This function is retained as a no-op so existing
    callers (e.g. Text3D) continue to work without modification.
    """
    pass
