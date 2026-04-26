"""Leitura do manifest YAML."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .profile import GameProfile


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
    # Decomposição semântica (Part3D) após Text3D; requer --with-parts e generate_3d=true
    generate_parts: bool = False
    generate_lod: bool = False
    generate_collision: bool = False
    # Asset category (e.g. humanoid, chest, weapon) — drives prompt hints and generation params
    category: str = ""
    # Part3D por linha: sobrepõe part3d.{steps,octree_resolution,segment_only} do perfil
    part3d_steps: int | None = None
    part3d_octree_resolution: int | None = None
    part3d_segment_only: bool | None = None
    # Per-row audio config (from YAML only; CSV falls back to profile global)
    audio_duration: float | None = None
    audio_profile: str | None = None  # "music" or "effects"
    audio_trim: bool | None = None
    audio_preset: str | None = None
    audio_steps: int | None = None
    audio_cfg_scale: float | None = None
    generation: str | None = None


def effective_image_source(profile: GameProfile, row: ManifestRow) -> str:
    """Fonte 2D efectiva: campo do manifest ou defeito do perfil."""
    if row.image_source:
        return row.image_source
    return profile.image_source


def _load_manifest_yaml(path: Path) -> list[ManifestRow]:
    """Lê YAML: assets com pipeline, audio, part3d sub-configs."""
    import yaml

    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assets = doc if isinstance(doc, list) else doc.get("assets", [])
    rows: list[ManifestRow] = []
    for entry in assets:
        pipeline = entry.get("pipeline", [])
        pipeline_items = [p.strip().lower() for p in pipeline] if isinstance(pipeline, list) else []

        audio_cfg = entry.get("audio") or {}
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}

        part3d_cfg = entry.get("part3d") or {}
        if not isinstance(part3d_cfg, dict):
            part3d_cfg = {}

        rows.append(
            ManifestRow(
                id=entry["id"],
                idea=entry["idea"],
                kind=entry.get("kind"),
                generate_3d="3d" in pipeline_items,
                generate_audio="audio" in pipeline_items,
                generate_rig="rig" in pipeline_items,
                generate_animate="animate" in pipeline_items,
                generate_parts="parts" in pipeline_items,
                generate_lod="lod" in pipeline_items,
                generate_collision="collision" in pipeline_items,
                image_source=entry.get("image_source"),
                category=(entry.get("category") or "").lower(),
                part3d_steps=part3d_cfg.get("steps"),
                part3d_octree_resolution=part3d_cfg.get("octree_resolution"),
                part3d_segment_only=part3d_cfg.get("segment_only"),
                audio_duration=audio_cfg.get("duration"),
                audio_profile=audio_cfg.get("profile"),
                audio_trim=audio_cfg.get("trim"),
                audio_preset=audio_cfg.get("preset"),
                audio_steps=audio_cfg.get("steps"),
                audio_cfg_scale=audio_cfg.get("cfg_scale"),
                generation=entry.get("generation"),
            )
        )
    if not rows:
        raise ValueError("Nenhuma linha válida no manifest (id + idea obrigatórios)")
    return rows


def load_manifest(path: Path) -> list[ManifestRow]:
    """Lê manifest YAML."""
    return _load_manifest_yaml(path)


def iter_manifest(path: Path) -> Iterator[ManifestRow]:
    yield from load_manifest(path)
