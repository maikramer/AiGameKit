"""Path helpers, classification, install helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from .manifest import ManifestRow
from .profile import GameProfile

_ROW_DONE = "done"
_ROW_NEED_IMAGE = "need_image"
_ROW_NEED_SHAPE = "need_shape"
_ROW_NEED_PAINT = "need_paint"
_ROW_NEED_RIG = "need_rig"
_ROW_NEED_ANIMATE = "need_animate"


def _paths_for_row(profile: GameProfile, row: ManifestRow) -> tuple[Path, Path]:
    root = Path(profile.output_dir)
    ext = profile.image_ext
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            sub = Path(*parts[:-1])
            base = parts[-1]
            dir_ = root / sub
        else:
            dir_ = root
            base = rid
        img = dir_ / f"{base}.{ext}"
        mesh = dir_ / f"{base}.glb"
    else:
        img = root / profile.images_subdir / f"{rid}.{ext}"
        mesh = root / profile.meshes_subdir / f"{rid}.glb"
    return img, mesh


def _rigging3d_output_path(mesh_final: Path, suffix: str) -> Path:
    """ex.: ``hero.glb`` + ``_rigged`` → ``hero_rigged.glb``."""
    s = (suffix or "_rigged").strip()
    if s and not s.startswith("_"):
        s = f"_{s}"
    if not s:
        s = "_rigged"
    return mesh_final.with_name(f"{mesh_final.stem}{s}.glb")


def _shell_path(path: Path) -> str:
    """Caminho normalizado para argv de subprocess (expande user, resolve)."""
    return str(path.expanduser().resolve())


def _animator3d_output_path(base_output: Path) -> Path:
    """ex.: ``hero_rigged.glb`` → ``hero_rigged_animated.glb``."""
    return base_output.with_name(f"{base_output.stem}_animated.glb")


def _shape_path(mesh_final: Path) -> Path:
    return mesh_final.with_name(f"{mesh_final.stem}_shape{mesh_final.suffix}")


def _painted_path(mesh_final: Path) -> Path:
    return mesh_final.with_name(f"{mesh_final.stem}_painted{mesh_final.suffix}")


def _valid_file(p: Path) -> bool:
    return p.is_file() and p.stat().st_size > 0


def _classify_row_state(
    *,
    img_final: Path,
    mesh_final: Path,
    rig_out: Path,
    anim_out: Path,
    want_texture: bool,
    wants_rig: bool,
    wants_animate: bool,
) -> str:
    shape = _shape_path(mesh_final)
    painted = _painted_path(mesh_final)
    final_exists = _valid_file(mesh_final) if want_texture else _valid_file(shape)

    if final_exists:
        if wants_rig and not _valid_file(rig_out):
            return _ROW_NEED_RIG
        if wants_rig and wants_animate and not _valid_file(anim_out):
            return _ROW_NEED_ANIMATE
        return _ROW_DONE
    if want_texture and _valid_file(painted):
        return _ROW_NEED_PAINT
    if _valid_file(shape):
        return _ROW_NEED_PAINT if want_texture else _ROW_DONE
    if _valid_file(img_final):
        return _ROW_NEED_SHAPE
    return _ROW_NEED_IMAGE


def _paths_for_row_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> tuple[Path, Path]:
    """
    PNG/GLB absolutos. O perfil usa muitas vezes output_dir: '.' — sem isto, caminhos relativos
    dependem do CWD do processo e o Text3D pode ler ficheiros errados (GPU "parada").
    """
    img, mesh = _paths_for_row(profile, row)
    img = (manifest_dir / img).resolve() if not img.is_absolute() else img.resolve()
    mesh = (manifest_dir / mesh).resolve() if not mesh.is_absolute() else mesh.resolve()
    return img, mesh


def _path_for_log(path: Path, manifest_dir: Path) -> str:
    """Caminho para run.jsonl: relativo ao manifest quando possível."""
    try:
        return str(path.resolve().relative_to(manifest_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def _install_file(src: Path, dst: Path) -> None:
    """Copia ficheiro para destino final (pasta do jogo); cria pais se necessário."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _install_maps_dir(src: Path, dst: Path) -> None:
    """Copia mapas PBR de uma pasta de trabalho (tmp) para a pasta final no jogo."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
        elif p.is_dir():
            _install_maps_dir(p, dst / p.name)
