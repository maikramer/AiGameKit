"""Shared bpy mesh utilities — load, save, query, clear scene.

Provides I/O helpers that use bpy as the backend instead of trimesh,
plus conversion functions for trimesh compatibility at package boundaries.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Blender 5.2 LTS introduced native EXT/KHR_meshopt_compression in the glTF
# exporter and a matching decoder in the importer. Older bpy needs an external
# decompress step (gltf-transform) before import.
_MESHOPT_BPY_MIN = (5, 2, 0)


def _require_bpy() -> Any:
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required but not installed. Install with: pip install bpy") from None


def bpy_version_tuple() -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` for the installed bpy, or ``None``."""
    try:
        import bpy

        v = bpy.app.version
        return (int(v[0]), int(v[1]), int(v[2]) if len(v) > 2 else 0)
    except Exception:
        return None


def gltf_export_supports_meshopt() -> bool:
    """True when ``bpy.ops.export_scene.gltf`` exposes meshopt compression flags."""
    try:
        bpy = _require_bpy()
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        return "export_meshopt_compression_enable" in props
    except Exception:
        return False


def gltf_import_supports_meshopt() -> bool:
    """True when this bpy can import ``EXT_meshopt_compression`` GLBs natively.

    Blender 5.2+ ships a meshopt decoder in ``io_scene_gltf2``. Earlier
    releases require decompressing via ``gltf-transform copy`` first.
    """
    ver = bpy_version_tuple()
    return ver is not None and ver >= _MESHOPT_BPY_MIN


def meshopt_runtime_available() -> bool:
    """True when the MeshOptimizer shared library can be loaded.

    The bpy wheel ships ``libbf_intern_meshopt_bridge.so`` but links against
    system ``libmeshoptimizer.so``. On Debian/Ubuntu that comes from
    ``libmeshoptimizer-dev`` (provides the unversioned ``.so`` symlink).
    """
    if not gltf_export_supports_meshopt():
        return False
    for name in ("libmeshoptimizer.so", "libmeshoptimizer.so.2d"):
        try:
            ctypes.CDLL(name)
            return True
        except OSError:
            continue
    return False


def gltf_meshopt_export_kwargs(
    *,
    enable: bool = True,
    extension: str = "EXT_meshopt_compression",
) -> dict[str, Any]:
    """Kwargs for ``bpy.ops.export_scene.gltf`` enabling native meshopt.

    Returns an empty dict when the installed bpy lacks the RNA properties
    (caller should fall back to ``@gltf-transform/cli meshopt``).
    """
    if not enable or not gltf_export_supports_meshopt():
        return {}
    return {
        "export_meshopt_compression_enable": True,
        "export_meshopt_extension": extension,
    }


def strip_bone_display_meshes() -> int:
    """Remove bone viewport display meshes (``Icosphere``, etc.) from the scene.

    ``import_scene.gltf`` with default ``bone_heuristic=BLENDER`` materializes
    bone custom shapes as real mesh objects. Re-export then embeds them in the
    GLB; world bounds expand to the helper (often a unit icosphere at origin)
    and the character looks like its pivot sits far below the feet.

    Only runs when an armature is present — otherwise a legitimate mesh named
    ``Icosphere`` (tests / props) would be deleted and leave an empty scene.

    Returns:
        Number of mesh objects removed.
    """
    bpy = _require_bpy()
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        return 0

    for arm in arms:
        for bone in arm.pose.bones:
            if getattr(bone, "custom_shape", None) is not None:
                bone.custom_shape = None

    helpers: list[Any] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        name = obj.name.lower()
        if name.startswith("icosphere") or name.startswith("bone_display"):
            helpers.append(obj)
            continue
        # Tiny unskinned orphan mesh, no materials — typical leftover display.
        if (
            obj.parent is None
            and len(obj.data.vertices) <= 64
            and not obj.data.materials
            and not any(m.type == "ARMATURE" for m in obj.modifiers)
        ):
            helpers.append(obj)

    for obj in helpers:
        with contextlib.suppress(Exception):
            bpy.data.objects.remove(obj, do_unlink=True)
    return len(helpers)


def import_gltf(path: str | Path, *, bone_heuristic: str = "TEMPERANCE", **kwargs: Any) -> list:
    """Import GLB/GLTF with round-trip-safe bone heuristic and strip helpers.

    ``TEMPERANCE`` avoids creating bone display meshes on import (BLENDER
    default would spawn ``Icosphere`` custom shapes). Also strips any display
    meshes already baked into the file.

    KTX2/BasisU (``KHR_texture_basisu``) and meshopt-on-old-bpy are pre-decoded
    via :func:`aigamekit_shared.gltf_decode.bpy_readable_glb` so pipeline LODs
    after ``--finish-lod0`` import without
    ``Extension KHR_texture_basisu is not available``.
    """
    from aigamekit_shared.gltf_decode import bpy_readable_glb

    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()

    def _do_import(readable: Path) -> None:
        import_kw: dict[str, Any] = {
            "filepath": str(readable),
            "bone_heuristic": bone_heuristic,
            **kwargs,
        }
        try:
            bpy.ops.import_scene.gltf(**import_kw)
        except TypeError:
            import_kw.pop("bone_heuristic", None)
            bpy.ops.import_scene.gltf(**import_kw)

    with bpy_readable_glb(path) as readable:
        _do_import(readable)
    strip_bone_display_meshes()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def load_glb(path: str | Path) -> list:
    """Import GLB/GLTF via bpy, return all imported mesh objects.

    Clears scene before import to avoid object pollution.
    Preserves transforms, armatures, shape keys, materials.
    """
    clear_scene()
    return import_gltf(path)


def apply_smooth_by_angle(obj: Any, degrees: float = 60.0) -> None:
    """Smooth-shade *obj*, keeping hard edges only above *degrees*.

    Blender 4.1 removed ``mesh.use_auto_smooth`` / ``auto_smooth_angle`` in
    favour of the ``object.shade_smooth_by_angle`` operator. Older code that
    still set the removed attributes (wrapped in ``suppress``) silently did
    nothing on bpy 5.x, leaving meshes fully smooth. This wrapper applies the
    angle correctly on both APIs.

    A higher angle (default 60°) is friendlier to *organic* assets and their
    decimated LODs — only genuinely sharp creases stay hard, instead of the
    old 30° which faceted gently-curved surfaces.
    """
    import contextlib
    import math

    bpy = _require_bpy()
    mesh = obj.data
    for poly in mesh.polygons:
        poly.use_smooth = True

    angle = math.radians(degrees)
    if hasattr(bpy.ops.object, "shade_smooth_by_angle"):
        with contextlib.suppress(RuntimeError, TypeError):
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.shade_smooth_by_angle(angle=angle)
            return
    # Legacy bpy (< 4.1)
    with contextlib.suppress(AttributeError):
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle


#: Acima deste V/Tri o mesh tem vértices duplicados (normais per-face / split).
_SPLIT_VERTS_VPT = 2.0

#: Weld de pré-export. Acima de 1e-4 fecha também o ruído de quantização que os
#: exporters glTF introduzem, sem comer detalhe (µm num asset de metros).
DEFAULT_PREEXPORT_WELD_DIST = 3e-4

#: Acima deste número de vértices o BMesh do weld pesa mais que os buffers e o
#: caminho vetorizado assume (meshes sem atributos por-canto).
_ARRAYS_WELD_MIN_VERTS = 1_000_000


def _mesh_is_plain(obj: Any, mesh: Any) -> bool:
    """True se o mesh só tem posições/faces — seguro para reescrever buffers.

    UVs, shape keys, vertex groups e materiais múltiplos não sobrevivem ao
    rebuild vetorizado, logo esses meshes ficam no caminho bmesh.
    """
    if len(mesh.uv_layers) or len(mesh.materials) > 1:
        return False
    if getattr(mesh, "shape_keys", None) is not None:
        return False
    if len(getattr(obj, "vertex_groups", ()) or ()):
        return False
    if getattr(mesh, "color_attributes", None) and len(mesh.color_attributes):
        return False
    # Só topologia all-tri: o rebuild triangula e mudaria quads em silêncio.
    return len(mesh.loops) == 3 * len(mesh.polygons)


def weld_mesh_arrays(mesh: Any, threshold: float) -> int:
    """Funde vértices coincidentes em arrays (numpy/scipy) em vez de BMesh.

    Mesmo fecho transitivo do ``bmesh.ops.remove_doubles`` (cKDTree, ou grelha
    de voxels sem scipy), mas sem construir o BMesh — que num soup de milhões
    de vértices aloca uma ordem de grandeza mais memória do que os buffers e é
    onde o export encalhava. Só para meshes sem atributos por-canto: o rebuild
    reescreve posições/faces e perderia UVs/weights.

    Returns:
        Vértices fundidos (0 se nada mudou).
    """
    import numpy as np

    from aigamekit_shared.mesh_repair_arrays import compact_mesh, weld_vertices

    n_tris = len(mesh.polygons)
    if len(mesh.vertices) == 0 or n_tris == 0:
        return 0

    verts = vertex_coords(mesh)
    tri = np.empty(n_tris * 3, dtype=np.int32)
    mesh.polygons.foreach_get("vertices", tri)
    tris = tri.reshape(-1, 3).astype(np.int64)

    try:
        verts, tris, merged = weld_vertices(verts, tris, float(threshold), method="exact")
    except ImportError:
        verts, tris, merged = weld_vertices(verts, tris, float(threshold), method="grid")
    if merged <= 0:
        return 0

    verts, tris = compact_mesh(verts, tris)
    mesh.clear_geometry()
    mesh.from_pydata(verts.tolist(), [], tris.tolist())
    mesh.update()
    return int(merged)


def tri_count(mesh: Any) -> int:
    """Triângulos de um mesh bpy em O(1), sem iterar polígonos.

    ``len(loops) - 2 * len(polygons)`` é a contagem fan-triangulada, logo vale
    para quads e n-gons. Iterar ``polygons`` custa segundos em meshes de
    milhões de faces (voxel remesh), por isso nunca fazê-lo só para contar.
    """
    return max(0, len(mesh.loops) - 2 * len(mesh.polygons))


def vertex_coords(mesh: Any) -> Any:
    """``(N, 3)`` float64 das posições locais via ``foreach_get`` (vetorizado)."""
    import numpy as np

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def signed_volume(mesh: Any) -> float:
    """Volume assinado do mesh (teorema da divergência), vetorizado.

    Positivo quando as faces estão com winding para fora. Em malhas abertas ou
    fragmentadas (marching cubes) o valor é aproximado, mas o **sinal** continua
    a ser um indicador fiável de qual lado é o exterior.

    Args:
        mesh: ``bpy.types.Mesh``.

    Returns:
        Volume assinado em unidades locais ao cubo; ``0.0`` se não há triângulos.
    """
    import numpy as np

    mesh.calc_loop_triangles()
    n_tris = len(mesh.loop_triangles)
    if n_tris == 0 or len(mesh.vertices) == 0:
        return 0.0
    idx = np.empty(n_tris * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", idx)
    tri = vertex_coords(mesh)[idx.reshape(-1, 3)]
    return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


def flip_normals(obj: Any) -> None:
    """Inverte o winding de todas as faces do objeto."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.flip_normals()
    bpy.ops.object.mode_set(mode="OBJECT")


def mesh_v_per_tri(mesh: Any) -> float | None:
    """V/Tri de um mesh bpy em O(1) (sem iterar polígonos).

    ``len(mesh.loops) - 2 * len(mesh.polygons)`` dá a contagem de triângulos de
    qualquer topologia fan-triangulável, logo serve para quads e n-gons.
    """
    polys = len(mesh.polygons)
    tris = len(mesh.loops) - 2 * polys
    if tris <= 0:
        return None
    return len(mesh.vertices) / tris


def smooth_shade_scene(
    objects: Any,
    degrees: float = 60.0,
    *,
    force_weld: bool = False,
    weld_dist: float = DEFAULT_PREEXPORT_WELD_DIST,
) -> None:
    """Smooth-shade + weld coincidentes antes de re-export glTF.

    Dois modos de V/Tri≈3:
    1. Import flat (sem NORMAL) → exporter parte loops. ``shade_smooth`` basta.
    2. Vértices **já duplicados** no mesh (rigged/animated SkinTokens, etc.) →
       ``shade_smooth`` sozinho NÃO funde; Decimate COLLAPSE rasga o LOD
       (triângulos isolados / moth-eaten). O weld fecha duplicados sem comer
       costuras UV úteis.

    Duas optimizações mantêm isto fora do caminho crítico:

    * **Skip por V/Tri** — um shape marching-cubes tem V/Tri≈0.5 e não tem nada
      para fundir; só V/Tri ≥ 2 denuncia vértices partidos. ``force_weld``
      ignora a heurística.
    * **Weld vetorizado** — em meshes grandes sem atributos por-canto o weld
      corre em arrays (:func:`weld_mesh_arrays`), poupando o BMesh que domina
      memória e tempo num soup de milhões de vértices.

    Idempotente. Aplicar antes de qualquer ``bpy.ops.export_scene.gltf`` da
    pipeline (skin_transfer, rigging3d bone_repair, animator3d game-pack).
    """
    import bmesh

    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = obj.data
        vpt = mesh_v_per_tri(mesh)
        needs_weld = force_weld or vpt is None or vpt >= _SPLIT_VERTS_VPT
        if not needs_weld:
            log.debug("smooth_shade_scene: weld saltado (%s V/Tri=%.2f)", mesh.name, vpt)
        elif len(mesh.vertices) >= _ARRAYS_WELD_MIN_VERTS and _mesh_is_plain(obj, mesh):
            merged = 0
            try:
                merged = weld_mesh_arrays(mesh, weld_dist)
            except Exception as exc:
                log.warning("weld vetorizado falhou (%s) — a usar bmesh: %s", mesh.name, exc)
                _weld_mesh_bmesh(bmesh, mesh, weld_dist)
            else:
                log.debug("smooth_shade_scene: weld vetorizado fundiu %d verts (%s)", merged, mesh.name)
        else:
            _weld_mesh_bmesh(bmesh, mesh, weld_dist)
        with contextlib.suppress(Exception):
            # Larga custom split normals herdadas do import (bpy < 4.1 API;
            # em 5.x o atributo é recriado pelo shade_smooth_by_angle).
            mesh.free_normals_split()
        apply_smooth_by_angle(obj, degrees)


def _weld_mesh_bmesh(bmesh: Any, mesh: Any, threshold: float) -> int:
    """Weld ``remove_doubles`` preservando UVs/weights (single-thread)."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=float(threshold))
    merged = before - len(bm.verts)
    if merged > 0:
        bm.to_mesh(mesh)
        mesh.update()
    bm.free()
    return merged


def _needs_tangents(objects: Any) -> bool:
    """Whether any mesh in *objects* has both UVs and a normal-map material.

    Tangents only matter for tangent-space normal maps; computing them when no
    normal map is present just splits vertices at UV seams for nothing.
    """
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        if not obj.data.uv_layers:
            continue
        for mat in obj.data.materials:
            if mat is None or not getattr(mat, "use_nodes", False):
                continue
            if any(n.type == "NORMAL_MAP" for n in mat.node_tree.nodes):
                return True
    return False


def save_glb(objects: Any, path: str | Path, **kwargs: Any) -> None:
    """Export scene/objects to GLB via bpy native exporter.

    Preserves armature, skinning, animations, materials, UVs.

    Defaults are conservative for game assets:

    - ``export_image_format="JPEG"``: textures saved as JPEG (3-5 MB total for a
      typical 2048² PBR set vs 30-40 MB as PNG).
    - ``export_normals=True``: normals are kept (turn off for shape-only stages
      where they will be recomputed downstream).
    - ``export_tangents=True``: MikkTSpace tangents are written so tangent-space
      normal maps render without seams across UV islands (and stay correct when
      a skinned mesh deforms). When the caller does **not** pass
      ``export_tangents`` explicitly, auto-disabled unless a NORMAL_MAP node is
      present (avoids UV-seam splits on plain geometry). Explicit
      ``export_tangents=True|False`` is always respected. Forced off when
      ``export_normals`` is off.
    - ``export_all_influences=False``: skin weights limited to the 4 most
      influential joints per vertex (GLTF standard); avoids extra
      ``JOINTS_n/WEIGHTS_n`` attribute sets.

    Any keyword passed via ``**kwargs`` overrides the corresponding default and
    is forwarded to ``bpy.ops.export_scene.gltf``.
    """
    import contextlib
    import io

    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if objects is not None:
        if not isinstance(objects, (list, tuple)):
            objects = [objects]
        bpy.ops.object.select_all(action="DESELECT")
        for o in objects:
            o.select_set(True)
        use_selection = True
    else:
        use_selection = False

    # Detect armatures early — export_apply=True freezes the Armature modifier
    # and the glTF exporter then omits skins[] / breaks LOD rebinding.
    scene_objs = list(bpy.context.scene.objects)
    sel_objs = list(objects) if objects is not None else scene_objs
    has_armature = any(getattr(o, "type", None) == "ARMATURE" for o in sel_objs) or any(
        getattr(o, "type", None) == "ARMATURE" for o in scene_objs
    )

    export_kwargs: dict[str, Any] = {
        "filepath": str(path),
        # Default False when skinned; True only for static geometry (unless overridden).
        "export_apply": not has_armature,
        "export_animations": True,
        "export_skins": True,
        "export_morph": True,
        "export_normals": True,
        "export_tangents": True,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "JPEG",
        "export_keep_originals": False,
        "export_all_influences": False,
        "use_selection": use_selection,
    }
    # Blender 5.2+: faster animated exports by skipping viewport updates.
    with contextlib.suppress(Exception):
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        if "export_optimize_disable_viewport" in props:
            export_kwargs["export_optimize_disable_viewport"] = True

    meshopt = bool(kwargs.pop("meshopt", False))
    meshopt_ext = str(kwargs.pop("meshopt_extension", "EXT_meshopt_compression"))
    # Verificação pós-export (não são kwargs do exporter glTF).
    verify_stage = kwargs.pop("verify_stage", None)
    skip_verify = bool(kwargs.pop("skip_verify", False))
    verify_require_normals = kwargs.pop("verify_require_normals", None)
    verify_require_uv = kwargs.pop("verify_require_uv", None)
    verify_require_tangents = kwargs.pop("verify_require_tangents", None)
    user_set_apply = "export_apply" in kwargs
    user_set_tangents = "export_tangents" in kwargs
    export_kwargs.update(kwargs)
    if meshopt:
        export_kwargs.update(gltf_meshopt_export_kwargs(enable=True, extension=meshopt_ext))
    # Never silently apply armatures unless caller forced export_apply=True.
    if has_armature and not user_set_apply:
        export_kwargs["export_apply"] = False

    # Tangents: explicit kwargs win. Otherwise auto — only when a NORMAL_MAP
    # material exists (UV-seam splits are otherwise pure cost).
    if not export_kwargs.get("export_normals", True):
        export_kwargs["export_tangents"] = False
    elif not user_set_tangents and export_kwargs.get("export_tangents"):
        candidates = objects if objects else bpy.context.scene.objects
        export_kwargs["export_tangents"] = _needs_tangents(candidates)

    # Suppress bpy stdout spam
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        bpy.ops.export_scene.gltf(**export_kwargs)

    if not skip_verify:
        with contextlib.suppress(Exception):
            from aigamekit_shared.glb_verify import post_save_verify

            post_save_verify(
                path,
                stage=verify_stage if isinstance(verify_stage, str) else None,
                require_normals=verify_require_normals if isinstance(verify_require_normals, bool) else None,
                require_uv=verify_require_uv if isinstance(verify_require_uv, bool) else None,
                require_tangents=verify_require_tangents if isinstance(verify_require_tangents, bool) else None,
            )


def get_mesh_objects() -> list:
    """Return all mesh objects in current scene."""
    bpy = _require_bpy()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def get_bounds(obj: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """World-space AABB of *obj* as (min_corner, max_corner)."""
    _require_bpy()
    verts_world = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not verts_world:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xs = [v.x for v in verts_world]
    ys = [v.y for v in verts_world]
    zs = [v.z for v in verts_world]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def face_count(obj: Any) -> int:
    """Total polygon count of *obj*."""
    return len(obj.data.polygons)


def vertex_count(obj: Any) -> int:
    """Total vertex count of *obj*."""
    return len(obj.data.vertices)


def clear_scene() -> None:
    """Delete ALL objects in current scene (canonical Blender reset).

    Uses ``bpy.ops.wm.read_factory_settings(use_empty=True)`` to reset
    to a clean state — removes objects, meshes, armatures, cameras, lights,
    materials, images, textures, shape keys.
    """
    bpy = _require_bpy()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def load_any(path: str | Path) -> list:
    """Dispatch to GLB/GLTF or PLY importer based on file extension."""
    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()
    clear_scene()
    ext = path.suffix.lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == ".ply":
        bpy.ops.import_mesh.ply(filepath=str(path))
    else:
        raise ValueError(f"Unsupported format: {ext}")
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


# ---------------------------------------------------------------------------
# Conversion helpers — numpy arrays ↔ bpy meshes (no trimesh dependency)
# ---------------------------------------------------------------------------


def create_mesh_from_arrays(
    vertices: Any,
    faces: Any,
    name: str = "Mesh",
) -> Any:
    """Create a bpy mesh object from numpy-compatible vertex/face arrays.

    Args:
        vertices: (N, 3) array-like of vertex positions.
        faces: (M, K) array-like of face indices (triangles or quads).
        name: Object/mesh name in Blender.

    Returns:
        The created bpy object.
    """
    import numpy as np

    bpy = _require_bpy()

    verts_np = np.asarray(vertices, dtype=np.float64)
    faces_np = np.asarray(faces, dtype=np.int64)

    mesh_data = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh_data)
    bpy.context.collection.objects.link(obj)

    mesh_data.from_pydata(verts_np.tolist(), [], faces_np.tolist())
    mesh_data.update()
    return obj


def apply_face_colors(obj: Any, face_colors: Any) -> None:
    """Apply per-face RGB colors as a vertex color (color attribute) layer.

    Args:
        obj: bpy mesh object (must have polygons).
        face_colors: (F, 3) uint8 array of RGB colours, one per face.
    """
    import numpy as np

    _require_bpy()
    mesh = obj.data
    colors = np.asarray(face_colors, dtype=np.float64) / 255.0

    # Use modern color_attributes API (Blender 4.x+ / bpy 4.x+)
    if hasattr(mesh, "color_attributes") and hasattr(mesh.color_attributes, "new"):
        color_attr = mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="CORNER")
    elif hasattr(mesh, "vertex_colors") and hasattr(mesh.vertex_colors, "new"):
        color_attr = mesh.vertex_colors.new(name="Col")
    else:
        raise RuntimeError("bpy mesh has no color_attributes or vertex_colors API")

    for i, poly in enumerate(mesh.polygons):
        r, g, b = float(colors[i, 0]), float(colors[i, 1]), float(colors[i, 2])
        for loop_idx in poly.loop_indices:
            color_attr.data[loop_idx].color = (r, g, b, 1.0)


def save_empty_glb(path: str | Path) -> None:
    """Export an empty GLB (no geometry). Useful as placeholder."""
    clear_scene()
    save_glb(None, path)


def save_colored_mesh(mesh: Any, face_colors: Any, path: str | Path) -> None:
    """Save a mesh-like object with per-face colours as GLB via bpy.

    *mesh* only needs ``.vertices`` (Nx3) and ``.faces`` (MxK) attributes, in
    Blender world space (as returned by :func:`load_mesh_as_trimesh`).
    """
    import numpy as np

    clear_scene()
    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    obj = create_mesh_from_arrays(verts, faces)
    apply_face_colors(obj, np.asarray(face_colors))
    save_glb([obj], path)


def save_scene_geometries(scene: Any, path: str | Path) -> None:
    """Save a trimesh.Scene-like object as GLB via bpy.

    Iterates *scene.geometry* (dict of name → mesh-like with .vertices/.faces)
    and exports all meshes. Arrays in Blender world space (as returned by
    :func:`load_mesh_as_trimesh`).
    """
    import numpy as np

    clear_scene()
    for name, geom in scene.geometry.items():
        verts = np.asarray(geom.vertices)
        faces = np.asarray(geom.faces)
        create_mesh_from_arrays(verts, faces, name=str(name))
    save_glb(None, path)


def load_mesh_as_trimesh(path: str | Path) -> Any:
    """Load mesh via bpy, return trimesh.Trimesh for pipeline compatibility.

    Lazy-imports trimesh internally so the calling module stays trimesh-free.
    Used at package boundaries where the pipeline still expects trimesh input.
    """
    import numpy as np
    import trimesh

    bpy = _require_bpy()
    objs = load_glb(path)
    if not objs:
        raise ValueError(f"No mesh objects found in {path}")
    obj = objs[0]

    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()

    verts = vertex_coords(mesh_eval)
    mesh_eval.calc_loop_triangles()
    tri_flat = np.empty(len(mesh_eval.loop_triangles) * 3, dtype=np.int64)
    mesh_eval.loop_triangles.foreach_get("vertices", tri_flat)
    faces = tri_flat.reshape(-1, 3)

    # Aplicar matrix_world: o importer glTF põe as rotações de node (ex. o
    # +90°X que endireita assets) na matriz do objeto — ler só ``v.co``
    # descarta-as e o re-export sai deitado/rodado.
    mw = np.array(obj_eval.matrix_world, dtype=np.float64)
    if not np.allclose(mw, np.eye(4)):
        verts = (np.c_[verts, np.ones(len(verts))] @ mw.T)[:, :3]

    obj_eval.to_mesh_clear()
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False)
