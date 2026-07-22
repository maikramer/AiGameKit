"""Tests for handoff_export helpers."""

from __future__ import annotations

from pathlib import Path

from gameassets.handoff_export import _install_file, resolve_handoff_mesh
from gameassets.manifest import ManifestRow


def test_install_file_noop_when_src_and_dst_identical(tmp_path: Path) -> None:
    """When output_dir is under public/, audio source and handoff dest can be the same path."""
    p = tmp_path / "clip.wav"
    p.write_bytes(b"wavdata")
    _install_file(p, p, copy=True)
    assert p.read_bytes() == b"wavdata"


def _row(**kwargs: object) -> ManifestRow:
    defaults: dict[str, object] = {
        "id": "hero",
        "idea": "test",
        "kind": "character",
        "generate_3d": True,
        "generate_animate": True,
        "generate_rig": True,
    }
    defaults.update(kwargs)
    return ManifestRow(**defaults)  # type: ignore[arg-type]


def test_resolve_handoff_prefers_lod0_over_rigged_animated_alias(tmp_path: Path) -> None:
    mesh = tmp_path / "hero.glb"
    lod0 = tmp_path / "hero_lod0.glb"
    alias = tmp_path / "hero_rigged_animated.glb"
    lod0.write_bytes(b"lod0")
    alias.write_bytes(b"alias")
    chosen, kind = resolve_handoff_mesh(mesh, _row(), prefer_animated=True, prefer_rigged=True)
    assert chosen == lod0
    assert kind == "animated"


def test_resolve_handoff_falls_back_to_promoted_lod0(tmp_path: Path) -> None:
    """Master promote puts clips in lod0; handoff must not require *_animated.glb legacy name."""
    mesh = tmp_path / "hero.glb"
    lod0 = tmp_path / "hero_lod0.glb"
    lod0.write_bytes(b"animated-lod0")
    chosen, kind = resolve_handoff_mesh(mesh, _row(), prefer_animated=True, prefer_rigged=True)
    assert chosen == lod0
    assert kind == "animated"


def test_resolve_handoff_static_uses_lod0_not_missing_base(tmp_path: Path) -> None:
    mesh = tmp_path / "crate.glb"
    lod0 = tmp_path / "crate_lod0.glb"
    lod0.write_bytes(b"static")
    chosen, kind = resolve_handoff_mesh(
        mesh,
        _row(id="crate", generate_animate=False, generate_rig=False),
        prefer_animated=True,
        prefer_rigged=True,
    )
    assert chosen == lod0
    assert kind == "lod0"
