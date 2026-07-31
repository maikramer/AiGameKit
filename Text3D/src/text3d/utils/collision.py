"""Geração de mesh de colisão (hull / envelope / mesh) para física em engines de jogo."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Literal

CollisionMode = Literal["hull", "envelope", "mesh"]
COLLISION_MODES: tuple[str, ...] = ("hull", "envelope", "mesh")


def resolve_collision_mode(
    *,
    mode: str | None = None,
    convex_hull: bool | None = None,
) -> CollisionMode:
    """Resolve modo efectivo.

    Precedência: ``mode`` explícito > ``convex_hull`` legacy (False→mesh, True/None→hull).
    """
    if mode is not None:
        m = str(mode).strip().lower()
        if m not in COLLISION_MODES:
            raise ValueError(f"collision mode inválido {mode!r}; esperado {COLLISION_MODES}")
        return m  # type: ignore[return-value]
    if convex_hull is False:
        return "mesh"
    return "hull"


def _char_size(obj) -> float:
    dims = obj.dimensions
    vol = abs(float(dims.x) * float(dims.y) * float(dims.z))
    return max(vol ** (1.0 / 3.0), 1e-3)


def _join_meshes(mesh_objs: list) -> object:
    import bpy

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for o in mesh_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]
    if len(mesh_objs) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return obj


def _decimate_to(obj, target: int) -> None:
    """Collapse until ≤ target (ratio único falha em meshes grandes — itera)."""
    import bpy

    for _ in range(12):
        current = len(obj.data.polygons)
        if current <= target:
            return
        ratio = max(target / current, 0.05)
        # Cap step ≈50% para CAPACITY do COLLAPSE não estagnar.
        ratio = max(ratio, 0.45) if current > target * 2 else ratio
        if current * ratio > target * 1.15 and current > target * 2:
            ratio = 0.5
        mod = obj.modifiers.new("CollisionDecimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = min(ratio, 0.99)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    # Último sniper se ainda acima
    current = len(obj.data.polygons)
    if current > target:
        mod = obj.modifiers.new("CollisionDecimateFinal", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(target / current, 0.01)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _apply_hull(obj) -> None:
    import bpy

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.convex_hull()
    bpy.ops.object.mode_set(mode="OBJECT")


def _inflate(obj, amount: float) -> None:
    """Empurra a superfície para fora (metros) — evita CCT afundar no visual."""
    import bpy

    if amount <= 0:
        return
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    # shrink_fatten positivo = engorda ao longo das normais
    bpy.ops.transform.shrink_fatten(value=float(amount), use_even_offset=True)
    bpy.ops.object.mode_set(mode="OBJECT")


def _apply_envelope(obj, *, voxel_size: float | None) -> None:
    """Voxel remesh → envelope volumétrico côncavo (preserva buracos se voxel caber)."""
    import bpy

    char = _char_size(obj)
    # Mais fino que char/32 — pilares de arco (~1–2 m) não encolhem demais.
    vs = float(voxel_size) if voxel_size is not None and voxel_size > 0 else max(char / 48.0, 0.04)
    mod = obj.modifiers.new("CollisionEnvelope", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = vs
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _strip_visual_data(mesh) -> None:
    import bpy

    mesh.materials.clear()
    for uv in list(mesh.uv_layers):
        mesh.uv_layers.remove(uv)
    for attr in list(getattr(mesh, "color_attributes", [])):
        mesh.color_attributes.remove(attr)
    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = False
    with contextlib.suppress(AttributeError, RuntimeError):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()


def generate_collision_mesh(
    input_path: Path,
    output_path: Path,
    *,
    max_faces: int = 300,
    convex_hull: bool | None = None,
    mode: str | None = None,
    voxel_size: float | None = None,
    inflate: float | None = None,
) -> Path:
    """Generate a simplified collision mesh — geometry only, no materials/textures/UVs.

    Modes:
        hull: convex hull + decimate (default; seals cavities).
        envelope: inflate + voxel remesh + decimate — concave volume proxy.
        mesh: inflate + decimate source — most precise silhouette (arches/gates).

    ``inflate`` (metres, along normals): default 0 for hull, ``max(char*0.008, 0.04)``
    for envelope/mesh so CCT does not sink into the visual.
    """
    import bpy

    from aigamekit_shared.bpy_mesh import clear_scene

    resolved = resolve_collision_mode(mode=mode, convex_hull=convex_hull)

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(Path(input_path)))

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError("No mesh objects found in input file")
    obj = _join_meshes(mesh_objs)

    n = len(obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); collision não aplicável.")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)

    char = _char_size(obj)
    if inflate is None:
        inflate_m = 0.0 if resolved == "hull" else max(char * 0.008, 0.04)
    else:
        inflate_m = float(inflate)

    if resolved == "hull":
        _apply_hull(obj)
    elif resolved == "envelope":
        _inflate(obj, inflate_m)
        _apply_envelope(obj, voxel_size=voxel_size)
    else:  # mesh — silhueta precisa do source
        _inflate(obj, inflate_m)

    _decimate_to(obj, max(4, max_faces))
    _strip_visual_data(obj.data)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        use_selection=True,
        export_apply=True,
        export_normals=False,
        export_texcoords=False,
        export_materials="NONE",
        export_animations=False,
        export_skins=False,
        export_morph=False,
    )

    clear_scene()
    return output_path
