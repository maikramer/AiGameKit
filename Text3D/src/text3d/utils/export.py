"""
Utilitários para exportação e conversão de arquivos 3D.
"""

import warnings
from pathlib import Path

import numpy as np
import trimesh
from diffusers.utils import export_to_gif, export_to_obj, export_to_ply

from ..defaults import get_export_origin, get_export_rotation_x_rad


def _load_as_trimesh(path: str | Path) -> trimesh.Trimesh:
    """Carrega ficheiro 3D como um único ``Trimesh`` (fundir cena se necessário)."""
    path = Path(path)
    loaded = trimesh.load(str(path), force=None)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError(f"Mesh vazia: {path}")
        if len(loaded.geometry) == 1:
            return next(iter(loaded.geometry.values()))
        return trimesh.util.concatenate(list(loaded.geometry.values()))
    if isinstance(loaded, trimesh.Trimesh):
        return loaded
    raise TypeError(f"Formato não suportado: {type(loaded)}")


def _export_glb_with_normals(mesh: trimesh.Trimesh, output_path: Path) -> None:
    """Export GLB ensuring vertex normals and doubleSided are included."""
    _ = mesh.vertex_normals  # force computation
    if hasattr(mesh, "visual") and hasattr(mesh.visual, "material"):
        mesh.visual.material.doubleSided = True
    scene = trimesh.Scene(geometry={"mesh": mesh})
    glb_bytes = scene.export(file_type="glb", include_normals=True)
    with open(str(output_path), "wb") as f:
        f.write(glb_bytes)


def save_mesh(
    mesh_input: np.ndarray | trimesh.Trimesh,
    output_path: str | Path,
    format: str | None = None,
    rotate: bool = True,
    origin_mode: str | None = None,
) -> Path:
    """
    Salva mesh em formato PLY, OBJ ou GLB.

    Aceita array numpy (legado / diffusers) ou ``trimesh.Trimesh`` (Hunyuan3D).
    ``origin_mode``: ``feet`` | ``center`` | ``none`` (defeito: env / ``get_export_origin()``).
    """
    if origin_mode is None:
        origin_mode = get_export_origin()
    output_path = Path(output_path)

    if format is None:
        format = output_path.suffix.lstrip(".")

    format = format.lower()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(mesh_input, trimesh.Trimesh):
        mesh = mesh_input
        mesh = _apply_rotation_trimesh(mesh.copy()) if rotate else mesh.copy()
        _apply_origin_trimesh(mesh, origin_mode)
        if format == "glb":
            _export_glb_with_normals(mesh, output_path)
            return output_path
        if format == "ply":
            mesh.export(str(output_path), file_type="ply")
            return output_path
        if format == "obj":
            mesh.export(str(output_path), file_type="obj")
            return output_path
        raise ValueError(f"Formato não suportado: {format}")

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

        if format != "ply":
            # temp_ply já tem rotação + origem aplicadas em ``_apply_rotation``
            return convert_mesh(temp_ply, output_path, rotate=False, origin_mode="none")

    elif format == "obj":
        export_to_obj(mesh_array, str(output_path))

        if rotate:
            _apply_rotation(output_path, origin_mode=origin_mode)
        else:
            _apply_origin_only_path(output_path, origin_mode=origin_mode)

    elif format == "glb":
        temp_ply = output_path.with_suffix(".temp.ply")
        export_to_ply(mesh_array, str(temp_ply))

        try:
            mesh = trimesh.load(temp_ply)
            if rotate:
                mesh = _apply_rotation_trimesh(mesh)
            _apply_origin_trimesh(mesh, origin_mode)
            _export_glb_with_normals(mesh, output_path)
        finally:
            if temp_ply.exists():
                temp_ply.unlink()

    else:
        raise ValueError(f"Formato não suportado: {format}")

    return output_path


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
    Converte mesh entre formatos usando trimesh.

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

    mesh = _load_as_trimesh(input_path)

    if rotate:
        mesh = _apply_rotation_trimesh(mesh)

    _apply_origin_trimesh(mesh, origin_mode)

    # Determinar formato de saída
    output_format = output_path.suffix.lstrip(".").lower()

    # Exportar
    if output_format == "glb":
        _export_glb_with_normals(mesh, output_path)
    else:
        mesh.export(str(output_path), file_type=output_format)

    return output_path


def _apply_rotation(ply_path: Path, *, origin_mode: str | None = None) -> None:
    """
    Aplica rotação Hunyuan→Y-up e reposiciona a origem (PLY in-place).
    """
    if origin_mode is None:
        origin_mode = get_export_origin()
    try:
        mesh = _load_as_trimesh(ply_path)
        mesh = _apply_rotation_trimesh(mesh)
        _apply_origin_trimesh(mesh, origin_mode)
        mesh.export(str(ply_path), file_type="ply")
    except Exception as e:
        warnings.warn(f"Não foi possível aplicar rotação/origem: {e}", stacklevel=2)


def _apply_origin_only_path(path: Path, *, origin_mode: str | None = None) -> None:
    """Só translada origem (sem rotação), útil quando ``rotate=False`` no legado numpy."""
    if origin_mode is None:
        origin_mode = get_export_origin()
    if origin_mode == "none":
        return
    try:
        mesh = _load_as_trimesh(path)
        _apply_origin_trimesh(mesh, origin_mode)
        ext = path.suffix.lower().lstrip(".") or "ply"
        mesh.export(str(path), file_type=ext)
    except Exception as e:
        warnings.warn(f"Não foi possível aplicar origem: {e}", stacklevel=2)


def _apply_rotation_trimesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Rotação X configurável (defeito 0 = sem rotação). Usar ``--export-rotation-x-deg`` para override."""
    angle = float(get_export_rotation_x_rad())
    if angle == 0.0:
        return mesh
    rx = trimesh.transformations.rotation_matrix(angle, [1, 0, 0])
    mesh.apply_transform(rx)
    return mesh


def _apply_origin_trimesh(mesh: trimesh.Trimesh, mode: str) -> trimesh.Trimesh:
    """
    Reposiciona a malha para uma origem consistente **após** a rotação Y-up.

    - ``feet``: base da AABB em Y=0, centro em X e Z (convénio personagens Godot/Blender).
    - ``center``: centro da AABB em (0, 0, 0).
    - ``none``: sem translação.
    """
    if mode == "none":
        return mesh
    bounds = mesh.bounds
    if mode == "feet":
        cx = (bounds[0][0] + bounds[1][0]) * 0.5
        cy = float(bounds[0][1])
        cz = (bounds[0][2] + bounds[1][2]) * 0.5
        mesh.apply_translation([-cx, -cy, -cz])
    elif mode == "center":
        cx = (bounds[0][0] + bounds[1][0]) * 0.5
        cy = (bounds[0][1] + bounds[1][1]) * 0.5
        cz = (bounds[0][2] + bounds[1][2]) * 0.5
        mesh.apply_translation([-cx, -cy, -cz])
    else:
        raise ValueError(f"Modo de origem desconhecido: {mode!r}")
    return mesh


def get_mesh_info(mesh_path: str | Path) -> dict:
    """
    Obtém informações sobre um arquivo mesh.

    Args:
        mesh_path: Caminho do arquivo mesh

    Returns:
        Dicionário com informações
    """
    mesh_path = Path(mesh_path)

    if not mesh_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {mesh_path}")

    mesh = trimesh.load(mesh_path)

    info = {
        "path": str(mesh_path),
        "format": mesh_path.suffix.lstrip(".").lower(),
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces) if hasattr(mesh, "faces") else 0,
        "bounds": mesh.bounds.tolist(),
        "is_watertight": mesh.is_watertight if hasattr(mesh, "is_watertight") else None,
        "volume": mesh.volume if hasattr(mesh, "volume") and mesh.is_watertight else None,
    }

    return info
