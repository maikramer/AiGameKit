"""Geração de mesh de colisão (hull / envelope / mesh) para física em engines de jogo."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

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


def _mesh_volume(obj) -> float:
    """Volume assinado da malha (unidades de mundo)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    v = float(bm.calc_volume(signed=True))
    bm.free()
    return abs(v)


def _boundary_edges(obj) -> int:
    from aigamekit_shared.mesh_repair import count_boundary_edges_fast

    return int(count_boundary_edges_fast(obj))


def _weld_seams(obj) -> int:
    """Funde vértices coincidentes (costuras UV/normal do glTF).

    Sem isto o mesh importado chega partido em milhares de retalhos: o
    ``Displace``/COLLAPSE tratam cada retalho isolado e a colisão sai como uma
    nuvem de triângulos soltos.
    """
    from aigamekit_shared.mesh_repair import remove_doubles

    return int(remove_doubles(obj, threshold=1e-6) or 0)


def _inflate(obj, amount: float) -> None:
    """Empurra a superfície para fora (metros) — evita CCT afundar no visual.

    Usa ``Displace`` ao longo das normais: deslocamento **limitado** a
    ``amount``. O antigo ``shrink_fatten(use_even_offset=True)`` escala por
    1/cos(ângulo) para manter espessura constante e explode em vértices
    quase-degenerados — medido no ``city_wall_seg_c``: área 77 → 1922 m² (25x)
    com um inflate de 8 cm.
    """
    import bpy

    if amount <= 0:
        return
    mod = obj.modifiers.new("CollisionInflate", "DISPLACE")
    mod.mid_level = 0.0
    mod.strength = float(amount)
    mod.direction = "NORMAL"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


# Abaixo desta fração do volume de entrada, o voxel remesh falhou (o campo de
# distância assinado degenerou) e o resultado é uma crosta de fragmentos.
_ENVELOPE_MIN_VOLUME_RATIO = 0.5


def _apply_envelope(obj, *, voxel_size: float | None) -> bool:
    """Voxel remesh → envelope volumétrico côncavo (preserva buracos se voxel caber).

    O remesh VOXEL do Blender (OpenVDB) só produz um sólido a partir de uma
    superfície **fechada**: com arestas de fronteira o campo passa a não
    assinado e sai uma crosta fina em pedaços. Medido no ``city_wall_seg_c``
    pintado (353 arestas abertas): volume 29.3 → 0.068 m³ e 101 componentes —
    a colisão entregue era uma nuvem de blobs. A partir do ``_clean``
    (fechado) o mesmo remesh dá 30.17 m³ e 1 componente.

    Aqui o remesh é validado à posteriori: se o volume colapsar, a malha
    original é restaurada e o chamador cai para decimação directa.

    Returns:
        True se o envelope foi aplicado; False se foi rejeitado e revertido.
    """
    import bpy

    char = _char_size(obj)
    # Mais fino que char/32 — pilares de arco (~1-2 m) não encolhem demais.
    vs = float(voxel_size) if voxel_size is not None and voxel_size > 0 else max(char / 48.0, 0.04)

    bnd_before = _boundary_edges(obj)
    if bnd_before:
        # Sem isto o VDB entrega a crosta descrita acima. Fechar primeiro é o
        # que torna o envelope utilizável a partir de um ``_painted``
        # (city_wall_seg_c: 353 → 76 arestas abertas, remesh 28.0 m³ e 1 peça).
        from aigamekit_shared.mesh_repair import make_watertight

        wt = make_watertight(obj, planar_tol=0.15, max_loop_edges=400, cap_base=True, final_fill=True)
        log.info(
            "collision envelope: fecho prévio %s → %s arestas de fronteira",
            wt.get("boundary_before"),
            wt.get("boundary_after"),
        )

    vol_before = _mesh_volume(obj)
    backup = obj.data.copy()

    mod = obj.modifiers.new("CollisionEnvelope", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = vs
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)

    vol_after = _mesh_volume(obj)
    if vol_before > 0 and vol_after < _ENVELOPE_MIN_VOLUME_RATIO * vol_before:
        log.warning(
            "collision envelope rejeitado: voxel remesh (%.3f m) levou o volume de %.3f para %.3f m³ "
            "(%d arestas de fronteira na entrada) — a usar a malha original",
            vs,
            vol_before,
            vol_after,
            bnd_before,
        )
        old = obj.data
        obj.data = backup
        bpy.data.meshes.remove(old)
        obj.data.validate(verbose=False)
        obj.data.update()
        return False
    bpy.data.meshes.remove(backup)
    return True


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

    from aigamekit_shared.bpy_mesh import clear_scene, import_gltf

    resolved = resolve_collision_mode(mode=mode, convex_hull=convex_hull)

    clear_scene()
    # import_gltf (não bpy.ops directo): trata KTX2/meshopt — um lod0 finalizado
    # rebentava com "Extension KHR_texture_basisu is not available".
    import_gltf(Path(input_path))

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

    # Reconectar antes de qualquer coisa: inflate e COLLAPSE sobre uma malha
    # partida nas costuras produzem uma colisão em pedaços.
    welded = _weld_seams(obj)
    if welded:
        log.info("collision: weld de costuras fundiu %d vértices", welded)

    char = _char_size(obj)
    inflate_m = (0.0 if resolved == "hull" else max(char * 0.008, 0.04)) if inflate is None else float(inflate)

    if resolved == "hull":
        _apply_hull(obj)
    elif resolved == "envelope":
        # Inflate **depois** do remesh: sobre a malha crua (auto-intersectada,
        # com agulhas) o Displace amplifica ruído que o remesh depois solidifica.
        if not _apply_envelope(obj, voxel_size=voxel_size):
            log.warning("collision: envelope indisponível — a cair para modo 'mesh' (silhueta directa)")
        _inflate(obj, inflate_m)
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
