"""Tests do sidecar ``{id}_precompute.json`` — emissão no fim do master
pipeline e merge no ``gameassets_handoff.json``.

O emissor é soft por contrato: sem ``aigamekit-lab`` no PATH, falha do
subprocess ou payload com ``error`` → warn + skip, nunca falha o batch.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gameassets.handoff_export import run_handoff
from gameassets.manifest import ManifestRow
from gameassets.paths import _precompute_path
from gameassets.pipeline import MasterPipelineResult, _emit_precompute
from gameassets.profile import GameProfile

PAYLOAD = {
    "version": 1,
    "asset_id": "pine_dark",
    "category": "vegetation",
    "aabb": {"min": [-1.0, 0.0, -1.0], "max": [1.0, 5.0, 1.0]},
    "collider": {"shape": "capsule", "radius": 0.21, "height": 5.0, "base_y": 0.0},
    "source": "trunk-slice",
    "collectible_hint": {"kind": "wood"},
}


def _row(**kwargs: object) -> ManifestRow:
    defaults: dict[str, object] = {
        "id": "pine_dark",
        "idea": "teste",
        "kind": "environment",
        "category": "vegetation",
        "generate_3d": True,
    }
    defaults.update(kwargs)
    return ManifestRow(**defaults)  # type: ignore[arg-type]


def _mres(tmp_path: Path) -> MasterPipelineResult:
    return MasterPipelineResult(
        asset_id="pine_dark",
        ok=True,
        lod0_path=tmp_path / "meshes" / "pine_dark_lod0.glb",
    )


def _fake_bin(rc: int = 0, payload: dict | None = None) -> SimpleNamespace:
    """run_cmd fake que escreve o payload no ``-o`` do argv."""

    def run(argv: list[str], extra_env: dict | None = None, cwd: Path | None = None) -> SimpleNamespace:
        if rc == 0 and payload is not None:
            out = Path(argv[argv.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=rc)

    return SimpleNamespace(run=run)


class TestPrecomputePath:
    """Mapeamento do sidecar junto do LOD0."""

    def test_precompute_path_from_lod0(self, tmp_path: Path) -> None:
        p = _precompute_path(tmp_path / "meshes" / "pine_dark_lod0.glb")
        assert p == tmp_path / "meshes" / "pine_dark_precompute.json"

    def test_precompute_path_from_painted_intermediate(self, tmp_path: Path) -> None:
        p = _precompute_path(tmp_path / "meshes" / "_intermediate" / "pine_dark_painted.glb")
        assert p == tmp_path / "meshes" / "pine_dark_precompute.json"


class TestEmitPrecompute:
    """Emissão do sidecar no hook do fim do master pipeline."""

    def test_emits_sidecar_and_records_path(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        rec: dict = {}
        fake = _fake_bin(payload=PAYLOAD)
        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=fake.run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), rec, tmp_path, {})
        out = meshes / "pine_dark_precompute.json"
        assert out.is_file()
        assert json.loads(out.read_text(encoding="utf-8"))["collider"]["shape"] == "capsule"
        assert rec["precompute_path"] == "meshes/pine_dark_precompute.json"

    def test_passes_category_and_asset_id(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        seen: list[str] = []
        fake = _fake_bin(payload=PAYLOAD)

        def run(argv: list[str], extra_env: dict | None = None, cwd: Path | None = None) -> SimpleNamespace:
            seen.extend(argv)
            return fake.run(argv, extra_env, cwd)

        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), {}, tmp_path, {})
        assert seen[0] == "aigamekit-lab"
        assert seen[1] == "precompute"
        assert "--category" in seen and seen[seen.index("--category") + 1] == "vegetation"
        assert "--asset-id" in seen and seen[seen.index("--asset-id") + 1] == "pine_dark"

    def test_prefers_collision_glb_as_source(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        (meshes / "pine_dark_collision.glb").write_bytes(b"coll")
        seen: list[str] = []
        fake = _fake_bin(payload=PAYLOAD)

        def run(argv: list[str], extra_env: dict | None = None, cwd: Path | None = None) -> SimpleNamespace:
            seen.extend(argv)
            return fake.run(argv, extra_env, cwd)

        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), {}, tmp_path, {})
        assert seen[2] == str(meshes / "pine_dark_collision.glb")

    def test_passes_stump_when_split_tree(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        (meshes / "pine_dark_stump_collision.glb").write_bytes(b"stump")
        seen: list[str] = []
        fake = _fake_bin(payload=PAYLOAD)

        def run(argv: list[str], extra_env: dict | None = None, cwd: Path | None = None) -> SimpleNamespace:
            seen.extend(argv)
            return fake.run(argv, extra_env, cwd)

        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), {}, tmp_path, {})
        assert "--stump" in seen
        assert seen[seen.index("--stump") + 1] == str(meshes / "pine_dark_stump_collision.glb")

    def test_skips_when_aigamekit_lab_missing(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        rec: dict = {}
        with patch("gameassets.pipeline._bin_or_none", return_value=None):
            _emit_precompute(_row(), _mres(tmp_path), rec, tmp_path, {})
        assert not (meshes / "pine_dark_precompute.json").exists()
        assert "precompute_path" not in rec

    def test_skips_when_lod0_missing(self, tmp_path: Path) -> None:
        rec: dict = {}
        with patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"):
            _emit_precompute(_row(), _mres(tmp_path), rec, tmp_path, {})
        assert "precompute_path" not in rec

    def test_skips_on_subprocess_failure(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        fake = _fake_bin(rc=3)
        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=fake.run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), {}, tmp_path, {})
        assert not (meshes / "pine_dark_precompute.json").exists()

    def test_unlinks_sidecar_when_payload_has_error(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        fake = _fake_bin(payload={"error": "GLB sem POSITION"})
        with (
            patch("gameassets.pipeline._bin_or_none", return_value="aigamekit-lab"),
            patch("gameassets.pipeline.run_cmd", side_effect=fake.run),
        ):
            _emit_precompute(_row(), _mres(tmp_path), {}, tmp_path, {})
        assert not (meshes / "pine_dark_precompute.json").exists()


class TestHandoffPrecomputeMerge:
    """``run_handoff`` inline o sidecar no ``gameassets_handoff.json``."""

    def _setup(
        self, tmp_path: Path, *, with_sidecar: bool, payload: dict | None = None
    ) -> tuple[GameProfile, Path, Path]:
        profile = GameProfile(
            title="T",
            genre="G",
            tone="t",
            style_preset="lowpoly",
            output_dir="out",
        )
        manifest_dir = tmp_path
        meshes = manifest_dir / "out" / "meshes"
        meshes.mkdir(parents=True)
        (meshes / "pine_dark_lod0.glb").write_bytes(b"lod0")
        if with_sidecar:
            (meshes / "pine_dark_precompute.json").write_text(json.dumps(payload or PAYLOAD), encoding="utf-8")
        public_dir = tmp_path / "public"
        return profile, manifest_dir, public_dir

    def test_handoff_inlines_precompute_block(self, tmp_path: Path) -> None:
        profile, manifest_dir, public_dir = self._setup(tmp_path, with_sidecar=True)
        out = run_handoff(
            profile,
            [_row()],
            manifest_dir,
            public_dir,
            copy=True,
            prefer_animated=False,
            prefer_rigged=False,
            with_textures=False,
            dry_run=False,
        )
        entry = out["rows"][0]
        assert entry["precompute"]["asset_id"] == "pine_dark"
        assert entry["precompute"]["collider"]["shape"] == "capsule"
        assert entry["model"]["url"] == "/assets/models/pine_dark_lod0.glb"

    def test_handoff_omits_block_without_sidecar(self, tmp_path: Path) -> None:
        profile, manifest_dir, public_dir = self._setup(tmp_path, with_sidecar=False)
        out = run_handoff(
            profile,
            [_row()],
            manifest_dir,
            public_dir,
            copy=True,
            prefer_animated=False,
            prefer_rigged=False,
            with_textures=False,
            dry_run=False,
        )
        assert "precompute" not in out["rows"][0]

    def test_handoff_tolerates_corrupt_sidecar(self, tmp_path: Path) -> None:
        profile, manifest_dir, public_dir = self._setup(tmp_path, with_sidecar=True)
        sidecar = manifest_dir / "out" / "meshes" / "pine_dark_precompute.json"
        sidecar.write_text("{not json", encoding="utf-8")
        out = run_handoff(
            profile,
            [_row()],
            manifest_dir,
            public_dir,
            copy=True,
            prefer_animated=False,
            prefer_rigged=False,
            with_textures=False,
            dry_run=False,
        )
        assert "precompute" not in out["rows"][0]
