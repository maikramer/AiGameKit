"""Leitura do manifest CSV."""

from __future__ import annotations

import csv
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
    # Asset category (e.g. humanoid, chest, weapon) — drives prompt hints and generation params
    category: str = ""
    # Part3D por linha: sobrepõe part3d.{steps,octree_resolution,segment_only} do perfil
    part3d_steps: int | None = None
    part3d_octree_resolution: int | None = None
    part3d_segment_only: bool | None = None


def effective_image_source(profile: GameProfile, row: ManifestRow) -> str:
    """Fonte 2D efectiva: coluna CSV ou defeito do perfil."""
    if row.image_source:
        return row.image_source
    return profile.image_source


def _parse_bool(value: str | None) -> bool:
    if value is None or str(value).strip() == "":
        return False
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "sim", "y", "on")


def _parse_image_source(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().lower()
    if s not in ("text2d", "texture2d", "skymap2d"):
        raise ValueError("Coluna image_source deve ser text2d, texture2d ou skymap2d (ou vazio para herdar o perfil)")
    return s


def _parse_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def load_manifest(path: Path) -> list[ManifestRow]:
    """Lê CSV: id, idea; opcionais kind, generate_3d, image_source, generate_audio,
    generate_rig, generate_animate, generate_parts, category, part3d_steps,
    part3d_octree_resolution, part3d_segment_only."""
    rows: list[ManifestRow] = []
    import io

    with path.open("r", encoding="utf-8", newline="") as f:
        # Skip comment lines (starting with #) and blank lines before the header
        lines = []
        header_found = False
        for line in f:
            stripped = line.lstrip()
            if not header_found and (stripped.startswith("#") or not stripped):
                continue
            header_found = True
            lines.append(line)
        reader = csv.DictReader(io.StringIO("".join(lines)))
        if not reader.fieldnames:
            raise ValueError("CSV vazio ou sem cabeçalhos")
        fields = {h.strip().lower(): h for h in reader.fieldnames if h}
        if "id" not in fields or "idea" not in fields:
            raise ValueError("CSV deve incluir colunas 'id' e 'idea'")
        id_key = fields["id"]
        idea_key = fields["idea"]
        kind_key = fields.get("kind")
        g3_key = fields.get("generate_3d")
        img_src_key = fields.get("image_source")
        ga_key = fields.get("generate_audio")
        gr_key = fields.get("generate_rig")
        gan_key = fields.get("generate_animate")
        gp_key = fields.get("generate_parts")
        cat_key = fields.get("category")
        # Part3D per-row overrides
        p3_steps_key = fields.get("part3d_steps")
        p3_oct_key = fields.get("part3d_octree_resolution") or fields.get("part3d_octree")
        p3_seg_key = fields.get("part3d_segment_only")
        for raw in reader:
            rid = (raw.get(id_key) or "").strip()
            idea = (raw.get(idea_key) or "").strip()
            if not rid or not idea:
                continue
            kind_val = None
            if kind_key:
                k = (raw.get(kind_key) or "").strip()
                kind_val = k if k else None
            g3 = False
            if g3_key:
                g3 = _parse_bool(raw.get(g3_key))
            img_src: str | None = None
            if img_src_key:
                img_src = _parse_image_source(raw.get(img_src_key))
            ga = False
            if ga_key:
                ga = _parse_bool(raw.get(ga_key))
            gr = False
            if gr_key:
                gr = _parse_bool(raw.get(gr_key))
            gan = False
            if gan_key:
                gan = _parse_bool(raw.get(gan_key))
            gp = False
            if gp_key:
                gp = _parse_bool(raw.get(gp_key))
            cat = ""
            if cat_key:
                c = (raw.get(cat_key) or "").strip().lower()
                cat = c if c else ""
            # Part3D per-row
            p3_steps: int | None = None
            if p3_steps_key:
                p3_steps = _parse_int(raw.get(p3_steps_key))
            p3_oct: int | None = None
            if p3_oct_key:
                p3_oct = _parse_int(raw.get(p3_oct_key))
            p3_seg: bool | None = None
            if p3_seg_key:
                p3_seg = _parse_bool(raw.get(p3_seg_key))
            rows.append(
                ManifestRow(
                    id=rid,
                    idea=idea,
                    kind=kind_val,
                    generate_3d=g3,
                    image_source=img_src,
                    generate_audio=ga,
                    generate_rig=gr,
                    generate_animate=gan,
                    generate_parts=gp,
                    category=cat,
                    part3d_steps=p3_steps,
                    part3d_octree_resolution=p3_oct,
                    part3d_segment_only=p3_seg,
                )
            )
    if not rows:
        raise ValueError("Nenhuma linha válida no manifest (id + idea obrigatórios)")
    return rows


def iter_manifest(path: Path) -> Iterator[ManifestRow]:
    yield from load_manifest(path)
