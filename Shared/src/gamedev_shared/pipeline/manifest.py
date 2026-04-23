"""GameAssets manifest — machine-readable asset index for engine consumption.

NOTE: This module is not currently used by any downstream package.
Candidate for removal in a future cleanup pass.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ManifestAsset:
    """A single asset entry in the manifest."""

    model: str | None = None
    textures: list[str] = field(default_factory=list)
    pbr_textures: list[str] = field(default_factory=list)
    animations: list[str] = field(default_factory=list)
    audio: str | None = None
    bounds: dict[str, list[float]] | None = None
    source_pipeline: str | None = None


@dataclass
class GameAssetsManifest:
    """Machine-readable asset index for VibeGame engine consumption."""

    version: int = 1
    generated: str = ""
    assets: dict[str, ManifestAsset] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def build_manifest(output_dir: Path) -> GameAssetsManifest:
    """Scan output_dir for GLBs, textures, audio and build manifest."""
    manifest = GameAssetsManifest(
        generated=datetime.now(timezone.utc).isoformat(),
    )

    if not output_dir.is_dir():
        return manifest

    # Scan for GLB files
    for glb in sorted(output_dir.rglob("*.glb")):
        name = glb.stem
        asset = ManifestAsset(model=f"assets/models/{glb.name}")

        # Check for metadata sidecar
        meta_path = glb.with_suffix(glb.suffix + ".metadata.json")
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                asset.animations = meta.get("animations", [])
                asset.bounds = meta.get("bounds")
                asset.pbr_textures = meta.get("pbr_textures", [])
                asset.source_pipeline = meta.get("source_pipeline")
            except (json.JSONDecodeError, OSError):
                pass

        manifest.assets[name] = asset

    # Scan for audio files
    for audio in sorted(output_dir.rglob("*")):
        if audio.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
            name = audio.stem
            if name in manifest.assets:
                manifest.assets[name].audio = f"assets/audio/{audio.name}"
            else:
                manifest.assets[name] = ManifestAsset(audio=f"assets/audio/{audio.name}")

    return manifest


def write_manifest(manifest: GameAssetsManifest, output_path: Path) -> None:
    """Write manifest as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(manifest.to_json(), encoding="utf-8")


def merge_manifest(base: GameAssetsManifest, overlay: GameAssetsManifest) -> GameAssetsManifest:
    """Merge two manifests (overlay wins on conflicts)."""
    merged = GameAssetsManifest(
        version=max(base.version, overlay.version),
        generated=overlay.generated or base.generated,
    )
    merged.assets = {**base.assets}
    for key, asset in overlay.assets.items():
        if key in merged.assets:
            existing = merged.assets[key]
            if asset.model:
                existing.model = asset.model
            if asset.textures:
                existing.textures = asset.textures
            if asset.pbr_textures:
                existing.pbr_textures = asset.pbr_textures
            if asset.animations:
                existing.animations = asset.animations
            if asset.audio:
                existing.audio = asset.audio
            if asset.bounds:
                existing.bounds = asset.bounds
            if asset.source_pipeline:
                existing.source_pipeline = asset.source_pipeline
        else:
            merged.assets[key] = asset
    return merged


def main() -> None:
    """CLI: python -m gamedev_shared.pipeline.manifest <output_dir> [-o manifest.json]"""
    import argparse

    parser = argparse.ArgumentParser(description="Build GameAssets manifest from output directory")
    parser.add_argument("output_dir", type=Path, help="Directory containing generated assets")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output manifest path")
    args = parser.parse_args()

    manifest = build_manifest(args.output_dir)
    out = args.output or args.output_dir / "gameassets_manifest.json"
    write_manifest(manifest, out)
    print(f"✅ Manifest written to {out} ({len(manifest.assets)} assets)")
