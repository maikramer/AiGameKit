"""
Redução de faces em GLB preservando textura (UV + mapa) ou geometria só (cinza uniforme).

Malhas com :class:`trimesh.visual.texture.TextureVisuals` e imagem baseColor: export
intermédio OBJ + ``meshing_decimation_quadric_edge_collapse_with_texture`` (PyMeshLab).

Outros casos: quadric trimesh (``fast-simplification``), igual a :func:`mesh_lod.simplify_to_face_count`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import trimesh
from trimesh.visual.texture import TextureVisuals

from .export import _export_glb_with_normals
from .mesh_lod import simplify_to_face_count


def _load_single_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(str(path), force=None)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError(f"Mesh vazia: {path}")
        if len(loaded.geometry) == 1:
            return next(iter(loaded.geometry.values())).copy()
        return trimesh.util.concatenate([g.copy() for g in loaded.geometry.values()])
    if isinstance(loaded, trimesh.Trimesh):
        return loaded.copy()
    raise TypeError(f"Formato não suportado: {type(loaded)}")


def _has_uv_texture_image(mesh: trimesh.Trimesh) -> bool:
    v = mesh.visual
    if not isinstance(v, TextureVisuals):
        return False
    if getattr(v, "uv", None) is None:
        return False
    mat = getattr(v, "material", None)
    if mat is None:
        return False
    img = getattr(mat, "image", None)
    if img is None and hasattr(mat, "baseColorTexture"):
        img = mat.baseColorTexture
    return img is not None


def _target_face_count(n_faces: int, face_ratio: float) -> int:
    return max(4, min(int(n_faces * face_ratio), n_faces - 1))


def simplify_glb_preserving_texture(
    path_in: str | Path,
    path_out: str | Path,
    *,
    face_ratio: float = 0.45,
    qualitythr: float = 0.5,
    extratcoordw: float = 1.0,
) -> Path:
    """
    Escreve ``path_out`` (GLB) com ~``face_ratio`` das faces da entrada.

    Usa decimação *with texture* (PyMeshLab) quando há UV + textura; caso contrário
    quadric trimesh.
    """
    path_in = Path(path_in)
    path_out = Path(path_out)

    mesh = _load_single_mesh(path_in)
    n = len(mesh.faces)
    if n < 8:
        _export_glb_with_normals(mesh, path_out)
        return path_out

    target = _target_face_count(n, face_ratio)
    if target >= n:
        _export_glb_with_normals(mesh, path_out)
        return path_out

    if _has_uv_texture_image(mesh):
        try:
            import pymeshlab  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(
                "Malha texturada requer pymeshlab para decimação com textura. "
                "Instala o pacote `pymeshlab`."
            ) from e

        with tempfile.TemporaryDirectory(prefix="t3d_texsimp_") as tmp:
            td = Path(tmp)
            obj_in = td / "in.obj"
            mesh.export(str(obj_in))

            ms = pymeshlab.MeshSet()
            ms.load_new_mesh(str(obj_in))
            ms.meshing_remove_unreferenced_vertices()
            ms.meshing_decimation_quadric_edge_collapse_with_texture(
                targetfacenum=target,
                targetperc=0,
                qualitythr=float(qualitythr),
                extratcoordw=float(extratcoordw),
                preserveboundary=True,
                boundaryweight=1.0,
                optimalplacement=True,
                preservenormal=True,
                planarquadric=False,
                selected=False,
            )
            obj_out = td / "out.obj"
            ms.save_current_mesh(str(obj_out))

            out_mesh = trimesh.load(str(obj_out), force="mesh")
            if isinstance(out_mesh, trimesh.Scene):
                out_mesh = next(iter(out_mesh.geometry.values()))
            path_out.parent.mkdir(parents=True, exist_ok=True)
            _export_glb_with_normals(out_mesh, path_out)
            return path_out

    reduced = simplify_to_face_count(mesh, target)
    path_out.parent.mkdir(parents=True, exist_ok=True)
    _export_glb_with_normals(reduced, path_out)
    return path_out
