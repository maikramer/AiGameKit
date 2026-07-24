"""GLB import for debug/render tools — KTX2/BasisU and meshopt aware.

bpy's glTF importer rejects GLBs requiring ``KHR_texture_basisu`` (and
``EXT_meshopt_compression`` on bpy < 5.2). Pipeline deliverables (lod0/1/2
after ``--finish-lod0`` / bake-master) use exactly those extensions, so every
GameDevLab import goes through :func:`import_glb`, which pre-decodes via
``@gltf-transform/cli`` when needed (see ``gamedev_shared.gltf_decode``).
"""

from __future__ import annotations

from pathlib import Path


def import_glb(glb_path: str | Path) -> None:
    """Import a GLB/GLTF into the current bpy scene, decoding if needed.

    Uses ``bone_heuristic=TEMPERANCE`` and strips bone display meshes
    (``Icosphere``) so screenshot framing follows the real asset bounds —
    not a unit helper at the origin.
    """
    from gamedev_shared.bpy_mesh import import_gltf
    from gamedev_shared.gltf_decode import bpy_readable_glb

    with bpy_readable_glb(glb_path) as readable:
        import_gltf(readable)
