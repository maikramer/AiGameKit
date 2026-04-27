"""Mesh repair utilities — weld/merge by distance shared across all tools."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _weld_distance(vertex_count: int) -> float:
    if vertex_count > 150_000:
        return 0.003
    if vertex_count > 100_000:
        return 0.005
    if vertex_count > 50_000:
        return 0.008
    return 0.01


def weld_glb(path: str | Path) -> None:
    """Aplica merge by distance + shade smooth via bpy em qualquer GLB.

    Distância adaptativa baseada em vértices.
    Modifica o ficheiro in-place. Silencioso se bpy indisponível.
    """
    try:
        import bpy
    except ImportError:
        return

    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))

        has_armature = any(obj.type == "ARMATURE" for obj in bpy.data.objects)
        if has_armature:
            return

        for obj in bpy.data.objects:
            if obj.type != "MESH":
                continue
            nv = len(obj.data.vertices)
            dist = _weld_distance(nv)

            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.remove_doubles(threshold=dist)
            bpy.ops.object.mode_set(mode="OBJECT")

        bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLB",
            use_selection=False,
            export_animations=True,
            export_animation_mode="ACTIONS",
        )
        log.info("weld_glb via bpy: %s", path)
    except Exception:
        log.warning("weld_glb falhou para %s", path, exc_info=True)
