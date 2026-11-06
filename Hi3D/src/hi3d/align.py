"""Geometria pura: enquadramento do coarse, alinhamento e decimação.

O Hi3D devolve a malha **recentrada na média dos vértices e normalizada para
raio máximo 1** (``sparc3d.sparcubes.postprocess.postprocess_mesh``). Como o
nosso pipeline precisa da escala/origem do ``_clean`` (metros reais, pés em
y=0), o refine tem de inverter exatamente essa normalização antes de entregar
a malha a jusante.

Este módulo não importa torch: os testes correm sem GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh


@dataclass(frozen=True)
class Frame:
    """Enquadramento de normalização usado pelo Hi3D."""

    center: np.ndarray  # média dos vértices
    radius: float  # norma radial máxima


def mesh_vertices(mesh: trimesh.Trimesh) -> np.ndarray:
    """Vértices como ``float64`` (cópia) — evita mutar o objeto de entrada."""
    return np.asarray(mesh.vertices, dtype=np.float64).copy()


def frame_of(vertices: np.ndarray) -> Frame:
    """Replica a normalização do upstream: média dos vértices + raio máximo."""
    verts = np.asarray(vertices, dtype=np.float64)
    if verts.size == 0:
        raise ValueError("malha sem vértices")
    center = verts.mean(axis=0, keepdims=True)
    radius = float(np.linalg.norm(verts - center, axis=1).max())
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("malha sem extensão radial positiva")
    return Frame(center=center[0].copy(), radius=radius)


def normalize_vertices(vertices: np.ndarray, frame: Frame) -> np.ndarray:
    """Aplica a normalização do Hi3D (útil em testes e diagnóstico)."""
    verts = np.asarray(vertices, dtype=np.float64)
    return (verts - frame.center) / frame.radius


def denormalize_vertices(vertices: np.ndarray, frame: Frame) -> np.ndarray:
    """Inverte a normalização do Hi3D: ``v * radius + center``."""
    verts = np.asarray(vertices, dtype=np.float64)
    return verts * frame.radius + frame.center


def restore_frame(mesh: trimesh.Trimesh, frame: Frame) -> trimesh.Trimesh:
    """Devolve uma cópia da malha refinada no enquadramento do coarse mesh."""
    out = mesh.copy()
    out.vertices = denormalize_vertices(mesh_vertices(mesh), frame)
    return out


def load_mesh(path: str | Path) -> trimesh.Trimesh:
    """Carrega GLB/OBJ/PLY como ``Trimesh`` (achata cenas, sem materiais)."""
    loaded = trimesh.load(str(path), force="mesh", process=False)
    if isinstance(loaded, trimesh.Scene):
        geoms = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError(f"sem geometria de triângulos em {path}")
        loaded = trimesh.util.concatenate(geoms)
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"{path} não é uma malha de triângulos")
    if len(loaded.vertices) == 0 or len(loaded.faces) == 0:
        raise ValueError(f"malha vazia: {path}")
    return loaded


def face_count(mesh: trimesh.Trimesh) -> int:
    """Número de triângulos (0 quando a malha é inválida)."""
    try:
        return len(mesh.faces)
    except (AttributeError, TypeError):
        return 0


def decimate(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    """Decima para ``target_faces`` com quadric decimation (pyfqmr).

    Alvo <= 0 ou malha já abaixo do alvo devolve a malha inalterada. A
    decimação é feita **depois** do alinhamento, para que o erro de
    simplificação seja medido em metros reais do asset.
    """
    if target_faces <= 0 or face_count(mesh) <= target_faces:
        return mesh
    simplified = mesh.simplify_quadric_decimation(face_count=int(target_faces))
    if simplified is None or len(simplified.faces) == 0:
        return mesh
    return simplified


def strip_debris(
    mesh: trimesh.Trimesh,
    *,
    ratio: float = 0.005,
    min_faces: int = 8,
) -> trimesh.Trimesh:
    """Remove ilhas soltas abaixo de ``ratio`` das faces do maior componente.

    O upstream já filtra componentes a 0.5% **antes** de simplificar
    (``sparc3d.sparcubes.postprocess.COMPONENT_FACE_RATIO``), mas a decimação
    posterior fragmenta lascas novas (medi: 214 ilhas de 1-7 faces num hero de
    32k). Aplicar o mesmo critério depois devolve o asset a um único corpo.
    """
    if face_count(mesh) == 0:
        return mesh
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh
    counts = [face_count(p) for p in parts]
    largest_idx = max(range(len(parts)), key=lambda i: counts[i])
    threshold = max(int(min_faces), int(counts[largest_idx] * float(ratio)))
    # O maior componente é sempre mantido: um ``min_faces`` acima do próprio
    # maior corpo não pode esvaziar o asset.
    keep = [p for i, (p, n) in enumerate(zip(parts, counts, strict=True)) if n >= threshold or i == largest_idx]
    if len(keep) == len(parts):
        return mesh
    return trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0]


def snap_to_bounds(mesh: trimesh.Trimesh, reference: trimesh.Trimesh) -> trimesh.Trimesh:
    """Encaixa a AABB da malha na do ``reference``: escala uniforme + centro XZ + base Y.

    ``restore_frame`` usa a média dos vértices do coarse, mas o upstream
    normaliza o output pela média dos **seus** vértices — com densidade
    diferente o centro desvia (medido: pés 1.8-5.2 cm abaixo de y=0, falha o
    gate ``origin.y_min`` do LOD0). A AABB é o invariante que interessa a
    jusante: escala pelo maior eixo, pés no chão do coarse.
    """
    src = mesh_vertices(mesh)
    ref = mesh_vertices(reference)
    smin, smax = src.min(axis=0), src.max(axis=0)
    rmin, rmax = ref.min(axis=0), ref.max(axis=0)
    s_ext, r_ext = float((smax - smin).max()), float((rmax - rmin).max())
    if s_ext <= 0.0 or r_ext <= 0.0:
        return mesh
    scale = r_ext / s_ext
    verts = src * scale
    vmin, vmax = verts.min(axis=0), verts.max(axis=0)
    offset = (rmin + rmax) / 2.0 - (vmin + vmax) / 2.0
    offset[1] = rmin[1] - vmin[1]
    out = mesh.copy()
    out.vertices = verts + offset
    return out


def export_glb(mesh: trimesh.Trimesh, path: str | Path) -> Path:
    """Exporta GLB (cria a pasta de destino)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(out), file_type="glb")
    return out


def refine_output_size_note(source_faces: int, target_faces: int) -> str:
    """Mensagem curta sobre a redução de faces (log/doctor)."""
    if source_faces <= 0:
        return "faces: ?"
    if target_faces <= 0 or target_faces >= source_faces:
        return f"faces: {source_faces}"
    pct = 100.0 * (1.0 - target_faces / source_faces)
    return f"faces: {source_faces} -> {target_faces} (-{pct:.0f}%)"
