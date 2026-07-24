"""Native GLB inspection and debug helpers — bpy-based (no Animator3D subprocess)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gamedev_lab.glb_import import import_glb

__all__ = [
    "extract_json_from_output",
    "inspect_glb",
    "inspect_materials",
]


def _require_bpy():
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required. Install with: pip install bpy") from None


def _inspect_scene() -> dict[str, Any]:
    """Read metadata from the current Blender scene (no clear/import side effects)."""
    import bpy
    from mathutils import Vector

    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    actions = list(bpy.data.actions)
    out: dict[str, Any] = {
        "blender_version": ".".join(str(x) for x in bpy.app.version),
        "frame_start": int(bpy.context.scene.frame_start),
        "frame_end": int(bpy.context.scene.frame_end),
        "fps": float(bpy.context.scene.render.fps),
        "armatures": [],
        "actions": [],
        "meshes": [],
        "mesh_totals": {"vertex_count": 0, "face_count": 0},
        "textures": [],
        "world_bounds": None,
    }
    _seen_images: set[int] = set()

    for arm in armatures:
        bone_names = [b.name for b in arm.data.bones]
        ad = arm.animation_data
        nla_n = len(ad.nla_tracks) if ad else 0
        active = ad.action.name if ad and ad.action else None
        out["armatures"].append(
            {
                "name": arm.name,
                "bone_count": len(bone_names),
                "bones": bone_names,
                "bones_sample": bone_names[:32],
                "nla_track_count": nla_n,
                "active_action": active,
            }
        )

    world_corners: list = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        data = obj.data
        nv = len(data.vertices)
        nf = len(data.polygons)
        out["mesh_totals"]["vertex_count"] += nv
        out["mesh_totals"]["face_count"] += nf
        out["meshes"].append(
            {
                "name": obj.name,
                "vertex_count": nv,
                "face_count": nf,
                "vertex_groups": len(obj.vertex_groups),
                "uv_layers": len(data.uv_layers),
            }
        )
        for mat in obj.data.materials:
            if mat is None or mat.node_tree is None:
                continue
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE":
                    continue
                img = node.image
                if img is None:
                    continue
                img_id = id(img)
                if img_id in _seen_images:
                    continue
                _seen_images.add(img_id)
                cs_name = img.colorspace_settings.name if hasattr(img, "colorspace_settings") else None
                out["textures"].append(
                    {
                        "name": img.name,
                        "width": img.size[0],
                        "height": img.size[1],
                        "channels": img.channels,
                        "colorspace": cs_name,
                    }
                )
        for c in obj.bound_box:
            world_corners.append(obj.matrix_world @ Vector(c))

    if world_corners:
        xs = [v.x for v in world_corners]
        ys = [v.y for v in world_corners]
        zs = [v.z for v in world_corners]
        mn = [min(xs), min(ys), min(zs)]
        mx = [max(xs), max(ys), max(zs)]
        out["world_bounds"] = {
            "min": mn,
            "max": mx,
            "center": [(mn[i] + mx[i]) / 2 for i in range(3)],
            "size": [mx[i] - mn[i] for i in range(3)],
            "max_extent": max(mx[i] - mn[i] for i in range(3)),
        }

    for act in actions:
        out["actions"].append(
            {
                "name": act.name,
                "frame_range": (int(act.frame_range[0]), int(act.frame_range[1])),
            }
        )
    return out


def _enrich_inspect_data(data: dict[str, Any], path: Path) -> None:
    """Add ``mesh``, ``animations``, ``input`` and ``file_size_bytes`` aliases."""
    data["input"] = str(path)
    data["file_size_bytes"] = path.stat().st_size if path.is_file() else 0
    data["mesh"] = {
        "total_vertex_count": data.get("mesh_totals", {}).get("vertex_count", 0),
        "total_face_count": data.get("mesh_totals", {}).get("face_count", 0),
        "objects": data.get("meshes", []),
    }
    data["animations"] = [
        {
            "name": a["name"],
            "frame_range": [int(a["frame_range"][0]), int(a["frame_range"][1])],
        }
        for a in data.get("actions", [])
    ]


def inspect_glb(glb_path: str | Path) -> dict[str, Any]:
    """Native GLB inspection — metadata dump via bpy (no subprocess).

    Returns dict matching Animator3D's ``inspect --json-out`` format,
    including ``mesh_totals``, ``world_bounds``, ``armatures``, ``actions``,
    ``meshes`` (with ``uv_layers``), ``textures``, ``mesh`` alias, and
    ``animations`` alias.
    """
    from gamedev_shared.bpy_mesh import clear_scene

    path = Path(glb_path).expanduser().resolve()
    _require_bpy()
    clear_scene()
    import_glb(path)

    data = _inspect_scene()
    _enrich_inspect_data(data, path)
    return data


def extract_json_from_output(text: str) -> dict[str, Any]:
    """Extract the first valid JSON object from mixed stdout/logs."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
            return {"_json_value": obj}
        except json.JSONDecodeError:
            continue
    return {
        "_parse_error": True,
        "raw_preview": text[:8000] if len(text) > 8000 else text,
    }


def inspect_materials() -> list[dict[str, Any]]:
    """Read material and texture metadata from the current Blender scene.

    For each material in ``bpy.data.materials``: name, ``use_nodes``,
    blend method, and a list of nodes (type + label). If a Principled BSDF
    node is present, captures its key inputs (base color, metallic,
    roughness, alpha, emissive). Image texture nodes expose size,
    channels, colorspace, and extension/wrap mode.

    Returns:
        List of material dicts (empty list if no materials).
    """
    import bpy

    _PRINCIPLED_INPUTS = (
        "Base Color",
        "Metallic",
        "Roughness",
        "Alpha",
        "Emission Color",
        "Emission",
        "IOR",
        "Normal",
    )
    out: list[dict[str, Any]] = []
    for mat in bpy.data.materials:
        entry: dict[str, Any] = {
            "name": mat.name,
            "use_nodes": bool(mat.use_nodes),
            "blend_method": getattr(mat, "blend_method", "OPAQUE"),
            "nodes": [],
        }
        tree = mat.node_tree
        if not mat.use_nodes or tree is None:
            out.append(entry)
            continue

        principled: dict[str, Any] | None = None
        for node in tree.nodes:
            node_info: dict[str, Any] = {
                "name": node.name,
                "type": node.type,
                "label": node.label or "",
            }
            if node.type == "BSDF_PRINCIPLED":
                pinputs: dict[str, Any] = {}
                for pin_name in _PRINCIPLED_INPUTS:
                    sock = node.inputs.get(pin_name)
                    if sock is None:
                        continue
                    val: Any
                    if hasattr(sock, "default_value"):
                        dv = sock.default_value
                        # Color/Multi-value sockets expose array-like values.
                        if hasattr(dv, "__len__") and not isinstance(dv, str):
                            val = [float(x) for x in dv]
                        else:
                            val = float(dv) if isinstance(dv, (int, float)) else dv
                    else:
                        val = None
                    pinputs[pin_name] = val
                # Detect linked image texture on base color.
                bc_sock = node.inputs.get("Base Color")
                if bc_sock is not None and bc_sock.is_linked:
                    link = bc_sock.links[0] if bc_sock.links else None
                    if link is not None:
                        node_info["base_color_link"] = link.from_node.type
                principled = pinputs
            elif node.type == "TEX_IMAGE":
                img = getattr(node, "image", None)
                node_info["image"] = (
                    {
                        "name": img.name if img else None,
                        "width": int(img.size[0]) if img else 0,
                        "height": int(img.size[1]) if img else 0,
                        "channels": int(img.channels) if img else 0,
                        "colorspace": img.colorspace_settings.name
                        if img and hasattr(img, "colorspace_settings")
                        else None,
                        "filepath": (img.filepath if img and img.filepath else None),
                    }
                    if img
                    else None
                )
                ext = node.image_extension if hasattr(node, "image_extension") else None
                node_info["extension"] = ext
            entry["nodes"].append(node_info)

        if principled is not None:
            entry["principled_inputs"] = principled
        out.append(entry)
    return out
