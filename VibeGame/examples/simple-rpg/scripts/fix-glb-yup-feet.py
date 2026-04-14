#!/usr/bin/env python3
"""
Alinha GLBs do exemplo a glTF Y-up (Three.js).

**Paridade com Text3D** (`Text3D/src/text3d/utils/export.py`):
rotação = ``get_export_rotation_x_rad()`` (defeito π/2 em X) + origem ``feet`` =
mesmas fórmulas que ``_apply_rotation_trimesh`` e ``_apply_origin_trimesh(..., 'feet')``.
O ``text3d generate`` aplica **sempre** essa rotação à malha Hunyuan; este script só
adiciona a rotação quando o GLB ainda tem altura dominante em Z (reparo de ficheiros
exportados sem esse passo).

Modelos com skeleton: não re-exportar com trimesh; opcionalmente transladar o nó raiz
com pygltflib.

Requisitos: pip install trimesh numpy pygltflib
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
import trimesh
from trimesh.transformations import rotation_matrix

try:
    from pygltflib import GLTF2
except ImportError:
    GLTF2 = None  # type: ignore

# Ângulo X idêntico ao CLI Text3D / env TEXT3D_EXPORT_ROTATION_X_*.
try:
    from text3d.defaults import get_export_rotation_x_rad
except ImportError:
    def get_export_rotation_x_rad() -> float:  # type: ignore[misc]
        return float(np.pi / 2)


def _scene_bounds(scene: trimesh.Scene) -> np.ndarray:
    return scene.bounds


def _feet_translation(bounds: np.ndarray) -> np.ndarray:
    """Mesmo vector que ``_apply_origin_trimesh(..., 'feet')`` aplica em export.py."""
    b = bounds
    dx = -0.5 * (b[0][0] + b[1][0])
    dy = -float(b[0][1])
    dz = -0.5 * (b[0][2] + b[1][2])
    return np.array([dx, dy, dz], dtype=np.float64)


def _needs_z_up_fix(extents: np.ndarray) -> bool:
    """True se a maior extensão for Z e Z claramente > Y (malha tipo Hunyuan Z-up)."""
    ex, ey, ez = float(extents[0]), float(extents[1]), float(extents[2])
    if max(ex, ey, ez) < 1e-9:
        return False
    j = int(np.argmax([ex, ey, ez]))
    if j != 2:
        return False
    return ez > ey * 1.08


def _trimesh_export_glb(scene: trimesh.Scene, out_path: Path) -> None:
    for geom in scene.geometry.values():
        if hasattr(geom, "vertex_normals"):
            _ = geom.vertex_normals
        vis = getattr(geom, "visual", None)
        if vis is not None and hasattr(vis, "material"):
            vis.material.doubleSided = True
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        tmp.write_bytes(scene.export(file_type="glb", include_normals=True))
        tmp.replace(out_path)
    except OSError:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        raise


def _gltf_skin_count(path: Path) -> int:
    buf = path.read_bytes()
    if len(buf) < 20 or buf[:4] != b"glTF":
        return 0
    json_len = struct.unpack_from("<I", buf, 12)[0]
    j = json.loads(buf[20 : 20 + json_len].decode())
    return len(j.get("skins", []))


def fix_mesh_only_glb(path: Path, *, dry_run: bool) -> bool:
    """Props sem skeleton: rotação X opcional + pés; trimesh preserva materiais/UVs no export."""
    scene = trimesh.load(str(path), force=None)
    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene(geometry={"mesh": scene})

    b0 = _scene_bounds(scene)
    ext0 = b0[1] - b0[0]
    rot = _needs_z_up_fix(ext0)

    if rot:
        scene.apply_transform(
            rotation_matrix(float(get_export_rotation_x_rad()), [1, 0, 0])
        )
    b1 = _scene_bounds(scene)
    delta = _feet_translation(b1)
    if np.linalg.norm(delta) > 1e-9:
        scene.apply_transform(trimesh.transformations.translation_matrix(delta))

    changed = rot or np.linalg.norm(delta) > 1e-9
    if not changed:
        if dry_run:
            print(f"[dry-run] {path.name}: sem alteração (já Y-up + pés)")
        else:
            print(f"[skip] {path.name}: sem alteração (já Y-up + pés)")
        return False
    if dry_run:
        print(
            f"[dry-run] {path.name}: rotate_x_90={rot} "
            f"ext_before={np.round(ext0, 4)} delta={np.round(delta, 6)}"
        )
        return changed

    _trimesh_export_glb(scene, path)
    b2 = _scene_bounds(scene)
    ext2 = b2[1] - b2[0]
    print(
        f"[ok] {path.name}: rotate_x_90={rot} "
        f"ext_final={np.round(ext2, 4)} minY={float(b2[0][1]):.6f}"
    )
    return changed


def fix_skinned_translation_only(path: Path, *, dry_run: bool) -> bool:
    if GLTF2 is None:
        raise RuntimeError("pygltflib é necessário para GLBs com skeleton: pip install pygltflib")

    scene = trimesh.load(str(path), force=None)
    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene(geometry={"m": scene})
    delta = _feet_translation(scene.bounds)
    if np.linalg.norm(delta) < 1e-6:
        print(f"[skip] {path.name}: já centrado (|delta|≈0)")
        return False

    gltf = GLTF2().load_binary(str(path))
    scene_idx = gltf.scene if gltf.scene is not None else 0
    roots = gltf.scenes[scene_idx].nodes
    world_idx = roots[0] if roots else 0
    # Prefer nó nomeado "world" se existir e for raiz da cena
    for ri in roots:
        nm = gltf.nodes[ri].name
        if nm and str(nm).lower() == "world":
            world_idx = ri
            break

    n = gltf.nodes[world_idx]
    t0 = np.array(n.translation or [0, 0, 0], dtype=np.float64)
    t1 = t0 + delta
    if dry_run:
        print(f"[dry-run] {path.name}: skinned translation {t0} -> {t1}")
        return True

    n.translation = [float(t1[0]), float(t1[1]), float(t1[2])]
    tmp = path.with_suffix(path.suffix + ".tmp")
    gltf.save_binary(str(tmp))
    tmp.replace(path)
    print(f"[ok] {path.name}: skinned root translation += {np.round(delta, 6)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "dir",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "public" / "assets" / "models",
        help="Pasta com .glb (defeito: …/public/assets/models)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Só mostrar o que faria")
    args = ap.parse_args()
    root: Path = args.dir
    if not root.is_dir():
        print(f"Pasta não existe: {root}", file=sys.stderr)
        return 1

    glbs = sorted(root.glob("*.glb"))
    if not glbs:
        print(f"Nenhum .glb em {root}")
        return 0

    any_change = False
    for path in glbs:
        try:
            if _gltf_skin_count(path) > 0:
                any_change = fix_skinned_translation_only(path, dry_run=args.dry_run) or any_change
            else:
                any_change = fix_mesh_only_glb(path, dry_run=args.dry_run) or any_change
        except Exception as e:
            print(f"[erro] {path.name}: {e}", file=sys.stderr)
            return 1

    if not args.dry_run and any_change:
        print("Concluído. Recomendação: abrir um modelo no viewer e confirmar texturas PBR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
