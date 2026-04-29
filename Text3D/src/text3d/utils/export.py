"""
Utilitários para exportação e conversão de arquivos 3D (bpy-based).

Suporta GLB/GLTF e PLY. Suporte a OBJ foi removido.
"""

from __future__ import annotations

import contextlib
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from diffusers.utils import export_to_gif, export_to_ply

from ..defaults import get_export_origin, get_export_rotation_x_rad


def _require_bpy():
    """Lazy-import bpy com mensagem clara se em falta."""
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy é necessário. Instale com: pip install bpy") from None


def _load_as_bpy(path: str | Path) -> list:
    """Carrega ficheiro 3D via bpy (GLB/GLTF ou PLY), retorna lista de objectos mesh.

    Limpa a cena antes do import para evitar poluição de objectos.
    """
    from gamedev_shared.bpy_mesh import load_any

    return load_any(path)


def _export_glb_bpy(objects: Any, output_path: Path) -> None:
    """Exporta GLB via bpy + weld pass para limpar vértices duplicados."""
    from gamedev_shared.bpy_mesh import save_glb

    save_glb(objects, output_path)
    with contextlib.suppress(Exception):
        try:
            from gamedev_shared.mesh_utils import weld_glb as _weld_glb

            _weld_glb(str(output_path))
        except ImportError:
            pass


def _apply_rotation_bpy(obj: Any) -> Any:
    """Rotação X configurável no objecto bpy (defeito 0 = sem rotação)."""
    angle = float(get_export_rotation_x_rad())
    if angle == 0.0:
        return obj
    bpy = _require_bpy()
    from math import cos, sin

    c, s = cos(angle), sin(angle)
    rx = bpy.mathutils.Matrix(((1, 0, 0, 0), (0, c, -s, 0), (0, s, c, 0), (0, 0, 0, 1)))
    obj.matrix_world = rx @ obj.matrix_world
    return obj


def _apply_origin_bpy(obj: Any, mode: str) -> Any:
    """Reposiciona a malha para uma origem consistente **após** rotação Y-up.

    - ``feet``: base da AABB em Y=0, centro em X e Z.
    - ``center``: centro da AABB em (0, 0, 0).
    - ``none``: sem translação.
    """
    if mode == "none":
        return obj
    from gamedev_shared.bpy_mesh import get_bounds

    (bx0, by0, bz0), (bx1, by1, bz1) = get_bounds(obj)
    if mode == "feet":
        tx = -(bx0 + bx1) * 0.5
        ty = -by0
        tz = -(bz0 + bz1) * 0.5
    elif mode == "center":
        tx = -(bx0 + bx1) * 0.5
        ty = -(by0 + by1) * 0.5
        tz = -(bz0 + bz1) * 0.5
    else:
        raise ValueError(f"Modo de origem desconhecido: {mode!r}")
    bpy = _require_bpy()
    translate = bpy.mathutils.Matrix.Translation((tx, ty, tz))
    obj.matrix_world = translate @ obj.matrix_world
    return obj


def _numpy_to_bpy_object(vertices: np.ndarray, faces: np.ndarray) -> Any:
    """Cria objecto bpy a partir de arrays numpy (vértices + faces).

    Usado na fronteira com código vendored (hy3dshape / diffusers) que devolve
    arrays em vez de objectos bpy.
    """
    bpy = _require_bpy()
    mesh_data = bpy.data.meshes.new("imported")
    mesh_data.from_pydata(vertices.tolist(), [], faces.tolist())
    mesh_data.update()
    obj = bpy.data.objects.new("imported", mesh_data)
    bpy.context.collection.objects.link(obj)
    return obj


def save_mesh(
    mesh_input: np.ndarray | Any,
    output_path: str | Path,
    format: str | None = None,
    rotate: bool = True,
    origin_mode: str | None = None,
) -> Path:
    """
    Salva mesh em formato PLY ou GLB.

    Aceita array numpy (legado / diffusers) ou objecto com ``.vertices``/``.faces``
    (ex-trimesh, bpy). ``origin_mode``: ``feet`` | ``center`` | ``none``
    (defeito: env / ``get_export_origin()``).
    """
    if origin_mode is None:
        origin_mode = get_export_origin()
    output_path = Path(output_path)

    if format is None:
        format = output_path.suffix.lstrip(".")

    format = format.lower()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Caminho: objecto com .vertices/.faces (ex-trimesh, bpy) ---
    has_mesh_attrs = hasattr(mesh_input, "vertices") and hasattr(mesh_input, "faces")
    if has_mesh_attrs:
        try:
            from gamedev_shared.bpy_mesh import clear_scene

            clear_scene()
            verts = np.asarray(mesh_input.vertices, dtype=np.float64)
            faces = np.asarray(mesh_input.faces, dtype=np.int32)
            obj = _numpy_to_bpy_object(verts, faces)
            if rotate:
                _apply_rotation_bpy(obj)
            _apply_origin_bpy(obj, origin_mode)
            if format == "glb":
                _export_glb_bpy(obj, output_path)
                return output_path
            if format == "ply":
                bpy = _require_bpy()
                bpy.ops.export_mesh.ply(filepath=str(output_path), use_selection=True)
                return output_path
            raise ValueError(f"Formato não suportado: {format}")
        except ImportError:
            if hasattr(mesh_input, "export"):
                mesh_input.export(str(output_path), file_type=format)
                return output_path
            raise

    # --- Caminho: numpy array (legado diffusers / hy3dshape) ---
    mesh_array = mesh_input

    if format == "ply":
        temp_ply = output_path.with_suffix(".ply")
        export_to_ply(mesh_array, str(temp_ply))

        if rotate:
            _apply_rotation(temp_ply, origin_mode=origin_mode)
        else:
            _apply_origin_only_path(temp_ply, origin_mode=origin_mode)

        if output_path.suffix.lower() == ".ply":
            return temp_ply

        # Converter PLY → GLB (já com rotação + origem)
        return convert_mesh(temp_ply, output_path, rotate=False, origin_mode="none")

    if format == "glb":
        temp_ply = output_path.with_suffix(".temp.ply")
        export_to_ply(mesh_array, str(temp_ply))
        try:
            objs = _load_as_bpy(temp_ply)
            if not objs:
                raise ValueError(f"Mesh vazia: {temp_ply}")
            obj = objs[0]
            if rotate:
                _apply_rotation_bpy(obj)
            _apply_origin_bpy(obj, origin_mode)
            _export_glb_bpy(obj, output_path)
        finally:
            if temp_ply.exists():
                temp_ply.unlink()
        return output_path

    raise ValueError(f"Formato não suportado: {format}")


def save_gif(
    frames: list,
    output_path: str | Path,
) -> Path:
    """
    Salva frames como GIF animado.

    Args:
        frames: Lista de frames PIL.Image
        output_path: Caminho de saída

    Returns:
        Caminho do arquivo salvo
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    export_to_gif(frames, str(output_path))

    return output_path


def convert_mesh(
    input_path: str | Path,
    output_path: str | Path,
    rotate: bool = False,
    origin_mode: str | None = None,
) -> Path:
    """
    Converte mesh entre formatos usando bpy.

    Args:
        input_path: Arquivo de entrada
        output_path: Arquivo de saída
        rotate: Aplicar rotação
        origin_mode: ``feet`` | ``center`` | ``none`` (defeito: ``get_export_origin()``)

    Returns:
        Caminho do arquivo convertido
    """
    if origin_mode is None:
        origin_mode = get_export_origin()

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {input_path}")

    objs = _load_as_bpy(input_path)
    if not objs:
        raise ValueError(f"Mesh vazia: {input_path}")

    for obj in objs:
        if rotate:
            _apply_rotation_bpy(obj)
        _apply_origin_bpy(obj, origin_mode)

    output_format = output_path.suffix.lstrip(".").lower()

    if output_format == "glb":
        _export_glb_bpy(objs, output_path)
    elif output_format == "ply":
        bpy = _require_bpy()
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.ops.export_mesh.ply(filepath=str(output_path), use_selection=True)
    else:
        raise ValueError(f"Formato de saída não suportado: {output_format}")

    return output_path


def _apply_rotation(ply_path: Path, *, origin_mode: str | None = None) -> None:
    """Aplica rotação Hunyuan→Y-up e reposiciona a origem (PLY in-place)."""
    if origin_mode is None:
        origin_mode = get_export_origin()
    try:
        objs = _load_as_bpy(ply_path)
        if not objs:
            return
        for obj in objs:
            _apply_rotation_bpy(obj)
            _apply_origin_bpy(obj, origin_mode)
        bpy = _require_bpy()
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.ops.export_mesh.ply(filepath=str(ply_path), use_selection=True)
    except Exception as e:
        warnings.warn(f"Não foi possível aplicar rotação/origem: {e}", stacklevel=2)


def _apply_origin_only_path(path: Path, *, origin_mode: str | None = None) -> None:
    """Só translada origem (sem rotação), útil quando ``rotate=False`` no legado numpy."""
    if origin_mode is None:
        origin_mode = get_export_origin()
    if origin_mode == "none":
        return
    try:
        objs = _load_as_bpy(path)
        if not objs:
            return
        for obj in objs:
            _apply_origin_bpy(obj, origin_mode)
        ext = path.suffix.lower().lstrip("") or "ply"
        bpy = _require_bpy()
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        if ext == "ply":
            bpy.ops.export_mesh.ply(filepath=str(path), use_selection=True)
        else:
            bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True)
    except Exception as e:
        warnings.warn(f"Não foi possível aplicar origem: {e}", stacklevel=2)


def get_mesh_info(mesh_path: str | Path) -> dict:
    """
    Obtém informações sobre um arquivo mesh.

    Args:
        mesh_path: Caminho do arquivo mesh

    Returns:
        Dicionário com informações
    """
    from gamedev_shared.bpy_mesh import face_count, get_bounds, vertex_count

    mesh_path = Path(mesh_path)

    if not mesh_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {mesh_path}")

    objs = _load_as_bpy(mesh_path)
    if not objs:
        raise ValueError(f"Mesh vazia: {mesh_path}")

    # Agregação de múltiplos objectos (cenas multi-mesh)
    total_verts = sum(vertex_count(o) for o in objs)
    total_faces = sum(face_count(o) for o in objs)

    # Bounds globais
    all_bounds = [get_bounds(o) for o in objs]
    if all_bounds:
        (min_x, min_y, min_z) = all_bounds[0][0]
        (max_x, max_y, max_z) = all_bounds[0][1]
        for (bx0, by0, bz0), (bx1, by1, bz1) in all_bounds[1:]:
            min_x, min_y, min_z = min(min_x, bx0), min(min_y, by0), min(min_z, bz0)
            max_x, max_y, max_z = max(max_x, bx1), max(max_y, by1), max(max_z, bz1)
        bounds_list = [[min_x, min_y, min_z], [max_x, max_y, max_z]]
    else:
        bounds_list = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]

    info = {
        "path": str(mesh_path),
        "format": mesh_path.suffix.lstrip(".").lower(),
        "vertices": total_verts,
        "faces": total_faces,
        "bounds": bounds_list,
        "is_watertight": None,
        "volume": None,
    }

    return info


# ── Backward-compatible aliases (remover após migrar módulos irmãos) ──
_load_as_trimesh = _load_as_bpy
_export_glb_with_normals = _export_glb_bpy
_apply_rotation_trimesh = _apply_rotation_bpy
_apply_origin_trimesh = _apply_origin_bpy
