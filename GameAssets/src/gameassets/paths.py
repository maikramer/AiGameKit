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
_ROW_NEED_LOD = "need_lod"
_ROW_NEED_COLLISION = "need_collision"

# Round 2 — checkpoints do master pipeline (DAG novo).
_ROW_NEED_TOPOLOGY_FIX = "need_topology_fix"  # tem _shape, falta _clean
_ROW_NEED_BAKE_MASTER = "need_bake_master"  # tem _painted+_clean, falta _lod0
_ROW_NEED_LOD_GEN = "need_lod_gen"  # tem _lod0, faltam _lod1/_lod2
_ROW_NEED_RIG_HI = "need_rig_hi"  # tem _clean, falta _rigged_hi
_ROW_NEED_TRANSFER = "need_transfer"  # tem _rigged_hi+_lodN, faltam _lodN_rigged
_ROW_NEED_ANIMATE_LOD = "need_animate_lod"  # tem _lodN_rigged, faltam _lodN_animated
_ROW_NEED_VALIDATE = "need_validate"  # tudo gerado, falta validação


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


def _base_stem(name: str) -> str:
    """Strip known suffixes from a stem like 'wooden_crate_painted' → 'wooden_crate'."""
    for sfx in (
        "_painted",
        "_shape",
        "_rigged_animated",
        "_rigged",
        "_segmented",
        "_collision",
        "_lod0",
        "_lod1",
        "_lod2",
    ):
        if name.endswith(sfx):
            return name[: -len(sfx)]
    return name


def _rigging3d_output_path(mesh_final: Path, suffix: str) -> Path:
    """ex.: ``hero.glb`` + ``_rigged`` → ``hero_rigged.glb``."""
    s = (suffix or "_rigged").strip()
    if s and not s.startswith("_"):
        s = f"_{s}"
    if not s:
        s = "_rigged"
    stem = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{stem}{s}.glb")


def _shell_path(path: Path) -> str:
    """Caminho normalizado para argv de subprocess (expande user, resolve)."""
    return str(path.expanduser().resolve())


def _animator3d_output_path(base_output: Path) -> Path:
    """ex.: ``hero_rigged.glb`` → ``hero_rigged_animated.glb``."""
    stem = _base_stem(base_output.stem)
    return base_output.with_name(f"{stem}_rigged_animated.glb")


def _intermediate_dir(mesh_final: Path) -> Path:
    """Pasta para artefactos descartáveis da pipeline (shape, clean, painted, rigged_hi).

    Convenção: ``<meshes_dir>/_intermediate/``. Não vai para o jogo.
    """
    return mesh_final.parent / "_intermediate"


def _shape_path(mesh_final: Path) -> Path:
    """``id_shape.glb`` ao lado da mesh canónica (compat: batch_cmd/resume_cmd).

    O orquestrador move este ficheiro para ``_intermediate/`` ao fim da
    pipeline via ``move_to_intermediate``.

    Round 2 fix: normaliza o stem via ``_base_stem`` para que a função seja
    idempotente — passar ``goblin.glb`` ou ``goblin_painted.glb`` devolve
    sempre ``goblin_shape.glb``.
    """
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_shape{mesh_final.suffix}")


def _painted_path(mesh_final: Path) -> Path:
    """``id_painted.glb`` ao lado da mesh canónica (compat).

    Idempotente em relação a sufixos canónicos (Round 2 fix).
    """
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_painted{mesh_final.suffix}")


def _clean_path(mesh_final: Path) -> Path:
    """``id_clean.glb`` em ``_intermediate/`` — output do Stage 2 (topology-fix).

    Sempre em ``_intermediate/`` desde a primeira escrita (artefacto novo).
    """
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_clean{mesh_final.suffix}"


def _rigged_hi_path(mesh_final: Path) -> Path:
    """``id_rigged_hi.glb`` em ``_intermediate/`` — Stage 7 (rig sobre _clean)."""
    base = _base_stem(mesh_final.stem)
    return _intermediate_dir(mesh_final) / f"{base}_rigged_hi{mesh_final.suffix}"


def _lod_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}.glb`` em ``meshes/`` (final, vai para o jogo)."""
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}{mesh_final.suffix}")


def _lod_rigged_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}_rigged.glb`` em ``meshes/``."""
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}_rigged{mesh_final.suffix}")


def _lod_animated_path(mesh_final: Path, level: int) -> Path:
    """``id_lod{level}_animated.glb`` em ``meshes/``."""
    base = _base_stem(mesh_final.stem)
    return mesh_final.with_name(f"{base}_lod{level}_animated{mesh_final.suffix}")


def move_to_intermediate(src: Path, mesh_final: Path) -> Path:
    """Move um intermediário (shape, painted, rigged_hi) para ``_intermediate/``.

    Idempotente: se o destino já existe, sobrescreve. Se ``src`` não existir,
    devolve sem erro.
    """
    if not src.is_file():
        return src
    dst_dir = _intermediate_dir(mesh_final)
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = _base_stem(src.stem)
    suffix = src.stem[len(_base_stem(src.stem)) :]  # ex: "_shape", "_painted"
    if not suffix:
        suffix = ""
    dst = dst_dir / f"{base}{suffix}{src.suffix}"
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    try:
        src.rename(dst)
    except OSError:
        # Cross-device fallback
        shutil.copy2(src, dst)
        try:
            src.unlink()
        except OSError:
            pass
    return dst


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
    wants_lod: bool = False,
    wants_collision: bool = False,
    lod0_path: Path | None = None,
    collision_path: Path | None = None,
) -> str:
    shape = _shape_path(mesh_final)
    painted = _painted_path(mesh_final)
    final_exists = (_valid_file(painted) or _valid_file(mesh_final)) if want_texture else _valid_file(shape)

    if final_exists:
        if wants_rig and not _valid_file(rig_out):
            return _ROW_NEED_RIG
        if wants_rig and wants_animate and not _valid_file(anim_out):
            return _ROW_NEED_ANIMATE
        if wants_lod and lod0_path and not _valid_file(lod0_path):
            return _ROW_NEED_LOD
        if wants_collision and collision_path and not _valid_file(collision_path):
            return _ROW_NEED_COLLISION
        return _ROW_DONE
    if _valid_file(shape):
        return _ROW_NEED_PAINT if want_texture else _ROW_DONE
    if _valid_file(img_final):
        return _ROW_NEED_SHAPE
    return _ROW_NEED_IMAGE


def _classify_row_state_master(
    *,
    img_final: Path,
    mesh_final: Path,
    want_texture: bool,
    wants_rig: bool,
    wants_animate: bool,
    wants_lod: bool = True,
    wants_collision: bool = True,
) -> str:
    """Classifica estado da row para o master pipeline (Round 2 DAG novo).

    Ordem de detecção espelha o DAG: image → shape → topology-fix (clean) →
    paint → bake-master (lod0) → lod gen → rig_hi → transfer → animate →
    validate. Devolve o primeiro estágio que ainda falta.
    """
    shape = _shape_path(mesh_final)
    clean = _clean_path(mesh_final)
    painted = _painted_path(mesh_final)
    lod0 = _lod_path(mesh_final, 0)
    lod1 = _lod_path(mesh_final, 1)
    lod2 = _lod_path(mesh_final, 2)
    rigged_hi = _rigged_hi_path(mesh_final)

    if not _valid_file(img_final) and not _valid_file(shape):
        return _ROW_NEED_IMAGE
    if not _valid_file(shape):
        return _ROW_NEED_SHAPE
    if not _valid_file(clean):
        return _ROW_NEED_TOPOLOGY_FIX
    if want_texture and not _valid_file(painted):
        return _ROW_NEED_PAINT
    if not _valid_file(lod0):
        return _ROW_NEED_BAKE_MASTER
    if wants_lod and (not _valid_file(lod1) or not _valid_file(lod2)):
        return _ROW_NEED_LOD_GEN
    if wants_rig and not _valid_file(rigged_hi):
        return _ROW_NEED_RIG_HI
    if wants_rig:
        # checa transfers
        targets = [lod0]
        if wants_lod:
            targets.extend([lod1, lod2])
        rig_outs = [_lod_rigged_path(mesh_final, i) for i in range(len(targets))]
        if any(not _valid_file(p) for p in rig_outs):
            return _ROW_NEED_TRANSFER
        if wants_animate:
            anim_outs = [_lod_animated_path(mesh_final, i) for i in range(len(targets))]
            if any(not _valid_file(p) for p in anim_outs):
                return _ROW_NEED_ANIMATE_LOD
    return _ROW_DONE


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
