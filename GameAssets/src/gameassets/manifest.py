"""Leitura do manifest YAML."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .profile import GameProfile

# Chaves top-level que o manifest pode declarar (antes de ``assets:``) para
# definir a pasta de saída do grupo. Ganham do game.yaml (profile) para este
# manifest — ver ManifestConfig.
_MANIFEST_CONFIG_KEYS = frozenset(
    {"output_dir", "path_layout", "images_subdir", "meshes_subdir", "audio_subdir", "image_ext"}
)


@dataclass(frozen=True)
class ManifestConfig:
    """Overrides de pastas/layout declarados no topo do manifest.

    O manifest define a pasta para onde os assets do grupo são gravados
    (ex. ``output_dir: ../public/assets`` + ``meshes_subdir: meshes/characters``).
    Campos ``None`` = herdar do game.yaml (profile).
    """

    output_dir: str | None = None
    path_layout: str | None = None
    images_subdir: str | None = None
    meshes_subdir: str | None = None
    audio_subdir: str | None = None
    image_ext: str | None = None


def _manifest_config_from_dict(doc: dict[str, Any], path: Path) -> ManifestConfig:
    """Parse das chaves top-level do manifest (ignora chaves desconhecidas)."""
    raw = doc.get("output_dir")
    output_dir: str | None = None
    if raw is not None:
        output_dir = str(raw).strip()
        if not output_dir:
            raise ValueError(f"manifest {path}: output_dir não pode ser vazio")

    pl_raw = doc.get("path_layout")
    path_layout: str | None = None
    if pl_raw is not None:
        path_layout = str(pl_raw).strip().lower()
        if path_layout not in ("split", "flat"):
            raise ValueError(f"manifest {path}: path_layout deve ser split ou flat")

    subdirs: dict[str, str | None] = {}
    for key in ("images_subdir", "meshes_subdir", "audio_subdir"):
        raw_sd = doc.get(key)
        if raw_sd is None:
            subdirs[key] = None
            continue
        sd = str(raw_sd).strip().strip("/")
        if not sd:
            raise ValueError(f"manifest {path}: {key} não pode ser vazio")
        subdirs[key] = sd

    ext_raw = doc.get("image_ext")
    image_ext: str | None = None
    if ext_raw is not None:
        image_ext = str(ext_raw).lower().lstrip(".")
        if not image_ext:
            raise ValueError(f"manifest {path}: image_ext não pode ser vazio")

    return ManifestConfig(
        output_dir=output_dir,
        path_layout=path_layout,
        images_subdir=subdirs["images_subdir"],
        meshes_subdir=subdirs["meshes_subdir"],
        audio_subdir=subdirs["audio_subdir"],
        image_ext=image_ext,
    )


@dataclass(frozen=True)
class RowText3D:
    """Overrides Text3D por asset (bloco ``text3d:`` do manifest).

    Ganham do ``text3d:`` profile (game.yaml) e do optimize_for_target.
    ``mc_level`` entra no fingerprint Omni (invalida o shape ao mudar);
    ``steps``/``octree_resolution`` são knobs de qualidade/custo (não fingerprint).
    Uso típico (hero lab): mãos com dedos separados = octree 384 + mc_level 0
    (o auto negativo engorda ~1 voxel e funde os gaps).

    ``morph_close_voxels`` / alias ``voxel_merge``: N do fecho morfológico
    (default 0.18; terrain/rock=0.54). ``morph_close`` = metros absolutos.
    """

    steps: int | None = None
    octree_resolution: int | None = None
    mc_level: float | str | None = None
    morph_close: float | None = None
    morph_close_voxels: float | None = None
    # Override do profile ``text3d.split_at_height`` (cogumelos baixos, etc.)
    split_at_height: bool | None = None


_ROW_TEXT3D_KEYS = frozenset(
    {
        "steps",
        "octree_resolution",
        "mc_level",
        "morph_close",
        "morph_close_voxels",
        "voxel_merge",
        "split_at_height",
    }
)


@dataclass(frozen=True)
class RowCollision:
    """Overrides de collision por asset (bloco ``collision:`` no manifest)."""

    mode: str | None = None
    max_faces: int | None = None
    voxel_size: float | None = None
    inflate: float | None = None
    convex_hull: bool | None = None


_ROW_COLLISION_KEYS = frozenset({"mode", "max_faces", "voxel_size", "inflate", "convex_hull"})


def _row_collision_from_dict(raw: Any, asset_id: str) -> RowCollision | None:
    """Parse bloco ``collision:`` por asset; ``None`` se ausente."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"asset {asset_id!r}: collision deve ser um mapeamento")
    unknown = set(raw) - _ROW_COLLISION_KEYS
    if unknown:
        raise ValueError(
            f"asset {asset_id!r} collision: chaves desconhecidas {sorted(unknown)} "
            f"(válidas: {sorted(_ROW_COLLISION_KEYS)})"
        )
    mode_raw = raw.get("mode")
    mf = raw.get("max_faces")
    vs = raw.get("voxel_size")
    inf = raw.get("inflate")
    ch = raw.get("convex_hull")
    mode_s: str | None = None
    if mode_raw is not None:
        mode_s = str(mode_raw).strip().lower()
        if mode_s not in ("hull", "envelope", "mesh"):
            raise ValueError(f"asset {asset_id!r} collision.mode deve ser hull|envelope|mesh")
    mf_i: int | None = None
    if mf is not None:
        try:
            mf_i = int(mf)
        except (TypeError, ValueError) as e:
            raise ValueError(f"asset {asset_id!r} collision.max_faces inválido") from e
        if mf_i < 4:
            raise ValueError(f"asset {asset_id!r} collision.max_faces deve ser ≥ 4")
    vs_f: float | None = None
    if vs is not None:
        try:
            vs_f = float(vs)
        except (TypeError, ValueError) as e:
            raise ValueError(f"asset {asset_id!r} collision.voxel_size inválido") from e
        if vs_f <= 0:
            raise ValueError(f"asset {asset_id!r} collision.voxel_size deve ser > 0")
    inf_f: float | None = None
    if inf is not None:
        try:
            inf_f = float(inf)
        except (TypeError, ValueError) as e:
            raise ValueError(f"asset {asset_id!r} collision.inflate inválido") from e
        if inf_f < 0:
            raise ValueError(f"asset {asset_id!r} collision.inflate deve ser ≥ 0")
    ch_b: bool | None = None
    if ch is not None:
        if not isinstance(ch, bool):
            raise ValueError(f"asset {asset_id!r} collision.convex_hull deve ser bool")
        ch_b = ch
    return RowCollision(mode=mode_s, max_faces=mf_i, voxel_size=vs_f, inflate=inf_f, convex_hull=ch_b)


def _row_text3d_from_dict(raw: Any, asset_id: str) -> RowText3D | None:
    """Parse bloco ``text3d:`` por asset; ``None`` se ausente."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"asset {asset_id!r}: text3d deve ser um mapeamento")
    unknown = set(raw) - _ROW_TEXT3D_KEYS
    if unknown:
        raise ValueError(
            f"asset {asset_id!r} text3d: chaves desconhecidas {sorted(unknown)} (válidas: {sorted(_ROW_TEXT3D_KEYS)})"
        )
    steps = raw.get("steps")
    octree = raw.get("octree_resolution")
    mc = raw.get("mc_level")
    mc_m = raw.get("morph_close")
    # voxel_merge = alias authoring de morph_close_voxels
    mcv = raw.get("morph_close_voxels", raw.get("voxel_merge"))
    sah = raw.get("split_at_height")
    try:
        steps_i = int(steps) if steps is not None else None
        octree_i = int(octree) if octree is not None else None
        mc_v: float | str | None = str(mc) if isinstance(mc, str) else (float(mc) if mc is not None else None)
        morph_m = float(mc_m) if mc_m is not None else None
        morph_n = float(mcv) if mcv is not None else None
    except (TypeError, ValueError) as e:
        raise ValueError(f"asset {asset_id!r} text3d: valores inválidos ({e})") from None
    sah_b: bool | None = None
    if sah is not None:
        if not isinstance(sah, bool):
            raise ValueError(f"asset {asset_id!r} text3d.split_at_height deve ser bool")
        sah_b = sah
    return RowText3D(
        steps=steps_i,
        octree_resolution=octree_i,
        mc_level=mc_v,
        morph_close=morph_m,
        morph_close_voxels=morph_n,
        split_at_height=sah_b,
    )


@dataclass(frozen=True)
class ManifestRow:
    id: str
    idea: str
    kind: str | None
    generate_3d: bool
    # Sobrepõe game.yaml image_source para esta linha (text2d | texture2d)
    image_source: str | None = None
    # Gera clip de áudio com Text2Sound (requer bloco text2sound no perfil ou defaults)
    generate_audio: bool = False
    # Auto-rig do GLB (Rigging3D) após Text3D; requer --with-rig e generate_3d=true
    generate_rig: bool = False
    # Animator3D game-pack após rig; requer --with-animate e GLB rigado (ou só --with-rig+generate_rig)
    generate_animate: bool = False
    generate_lod: bool = False
    generate_collision: bool = False
    # Textura 3D (paint3d quick ou Hunyuan) após shape; requer paint no pipeline e opções em paint3d no perfil
    generate_paint: bool = False
    lod_levels: int = 3
    # Asset category (e.g. humanoid, chest, weapon) — drives prompt hints and generation params
    category: str = ""
    # Per-row audio config (from YAML only; CSV falls back to profile global)
    audio_duration: float | None = None
    audio_profile: str | None = None  # "music" or "effects"
    audio_trim: bool | None = None
    audio_preset: str | None = None
    audio_steps: int | None = None
    audio_cfg_scale: float | None = None
    generation: str | None = None
    # Per-row animation overrides (from YAML ``animate:`` sub-dict).
    # When None, fall back to the global ``animator3d:`` profile settings.
    animate_clips: str | None = None
    animate_preset: str | None = None  # humanoid | creature | flying
    animate_procedural: bool | None = None
    animate_force_preset: bool | None = None
    animate_anim_pack: str | None = None  # quaternius | quaternius2 | villager | both | all | lista (ex.: both,villager)
    # Controlos Omni por asset (override de ``text3d.omni`` no game.yaml).
    omni: Any | None = None
    # Seed absoluto por asset (re-roll cirúrgico). Omitido → determinístico
    # ``seed_base + adler32(id)``. Entra no fingerprint Omni do shape: mudar
    # ``seed:`` invalida só esse ``*_shape.glb`` no resume.
    seed: int | None = None
    # Overrides Text3D por asset (bloco ``text3d:`` — ver RowText3D).
    text3d: RowText3D | None = None
    # Overrides collision por asset (bloco ``collision:`` — ver RowCollision).
    collision: RowCollision | None = None


def effective_image_source(profile: GameProfile, row: ManifestRow) -> str:
    """Fonte 2D efectiva: campo do manifest ou defeito do perfil."""
    if row.image_source:
        return row.image_source
    return profile.image_source


def apply_row_text3d_overrides(item: dict[str, Any], row: ManifestRow) -> dict[str, Any]:
    """Aplica overrides ``text3d:`` do asset ao shape item (ganham sempre).

    Corre DEPOIS do optimize_for_target/hw-auto: authoring explícito no manifest
    tem prioridade. ``steps``/``octree_resolution``/``mc_level`` são consumidos
    por ``vramd_batch.shape_specs_from_items`` e ``text3d generate-batch``.
    """
    rt3 = row.text3d
    if rt3 is None:
        return item
    if rt3.steps is not None:
        item["steps"] = rt3.steps
    if rt3.octree_resolution is not None:
        item["octree_resolution"] = rt3.octree_resolution
    if rt3.mc_level is not None:
        item["mc_level"] = rt3.mc_level
    return item


def row_mc_level(row: ManifestRow, profile_mc_level: float | str | None) -> float | str | None:
    """``mc_level`` efectivo do asset: override do manifest > profile game.yaml."""
    rt3 = row.text3d
    if rt3 is not None and rt3.mc_level is not None:
        return rt3.mc_level
    return profile_mc_level


def effective_collision_args(profile: GameProfile, row: ManifestRow) -> dict[str, Any]:
    """Resolve argv knobs de ``text3d collision``: perfil global + override por asset."""
    from .profile import CollisionProfile

    cp = profile.collision or CollisionProfile()
    rc = row.collision
    mode = cp.mode
    max_faces = cp.max_faces
    voxel_size = cp.voxel_size
    inflate = cp.inflate
    if rc is not None:
        if rc.mode is not None:
            mode = rc.mode
        elif rc.convex_hull is not None:
            mode = "hull" if rc.convex_hull else "mesh"
        if rc.max_faces is not None:
            max_faces = rc.max_faces
        if rc.voxel_size is not None:
            voxel_size = rc.voxel_size
        if rc.inflate is not None:
            inflate = rc.inflate
    return {"mode": mode, "max_faces": max_faces, "voxel_size": voxel_size, "inflate": inflate}


def _rows_from_doc(doc: Any, path: Path) -> list[ManifestRow]:
    """Constrói rows a partir do documento YAML (lista ou mapa com ``assets:``)."""
    assets = doc if isinstance(doc, list) else doc.get("assets", [])
    rows: list[ManifestRow] = []
    for entry in assets:
        pipeline = entry.get("pipeline", [])
        pipeline_items = [p.strip().lower() for p in pipeline] if isinstance(pipeline, list) else []

        audio_cfg = entry.get("audio") or {}
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}

        animate_cfg = entry.get("animate") or {}
        if not isinstance(animate_cfg, dict):
            animate_cfg = {}

        from .omni_ctrl import omni_from_dict

        try:
            row_omni = omni_from_dict(entry.get("omni"))
        except ValueError as e:
            raise ValueError(f"asset {entry.get('id')!r} omni: {e}") from e

        raw_seed = entry.get("seed")
        row_seed: int | None = None
        if raw_seed is not None:
            try:
                row_seed = int(raw_seed)
            except (TypeError, ValueError):
                raise ValueError(f"asset {entry.get('id')!r}: seed inválido ({raw_seed!r}) — esperado int") from None

        row_text3d = _row_text3d_from_dict(entry.get("text3d"), str(entry.get("id")))
        row_collision = _row_collision_from_dict(entry.get("collision"), str(entry.get("id")))

        rows.append(
            ManifestRow(
                id=entry["id"],
                idea=entry["idea"],
                kind=entry.get("kind"),
                generate_3d="3d" in pipeline_items,
                generate_audio="audio" in pipeline_items,
                generate_rig="rig" in pipeline_items,
                generate_animate="animate" in pipeline_items,
                generate_lod="lod" in pipeline_items,
                generate_collision="collision" in pipeline_items,
                generate_paint="paint" in pipeline_items,
                lod_levels=int(entry.get("lod_levels", 3)),
                image_source=entry.get("image_source"),
                category=(entry.get("category") or "").lower(),
                audio_duration=audio_cfg.get("duration"),
                audio_profile=audio_cfg.get("profile"),
                audio_trim=audio_cfg.get("trim"),
                audio_preset=audio_cfg.get("preset"),
                audio_steps=audio_cfg.get("steps"),
                audio_cfg_scale=audio_cfg.get("cfg_scale"),
                generation=entry.get("generation"),
                animate_clips=animate_cfg.get("clips"),
                animate_preset=animate_cfg.get("preset"),
                animate_procedural=animate_cfg.get("procedural"),
                animate_force_preset=animate_cfg.get("force_preset"),
                animate_anim_pack=animate_cfg.get("anim_pack"),
                omni=row_omni,
                seed=row_seed,
                text3d=row_text3d,
                collision=row_collision,
            )
        )
    if not rows:
        raise ValueError("Nenhuma linha válida no manifest (id + idea obrigatórios)")
    return rows


def _load_manifest_bundle(path: Path) -> tuple[list[ManifestRow], ManifestConfig]:
    """Lê YAML: config de pasta top-level + assets com pipeline e audio sub-configs."""
    import yaml

    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        config = ManifestConfig()
    else:
        doc = doc or {}
        config = _manifest_config_from_dict(doc, path)
    return _rows_from_doc(doc, path), config


def load_manifest_bundle(path: Path) -> tuple[list[ManifestRow], ManifestConfig]:
    """Lê manifest YAML: (rows, config de pasta).

    ``ManifestConfig`` carrega as chaves top-level (``output_dir``, subdirs,
    ``path_layout``, ``image_ext``) que o manifest pode declarar para definir
    a pasta de saída do grupo — aplicadas ao profile em ``_build_context``.
    """
    return _load_manifest_bundle(path)


def load_manifest(path: Path) -> list[ManifestRow]:
    """Lê manifest YAML (só rows)."""
    return _load_manifest_bundle(path)[0]


def iter_manifest(path: Path) -> Iterator[ManifestRow]:
    yield from load_manifest(path)
