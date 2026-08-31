"""Tests for scripts/sync_assets.py (mirror world assets from the shared pool).

The meshopt decompression is always injected (a stub) — no test calls npx.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("sync_assets", _HERE / "sync_assets.py")
sync = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = sync  # needed by dataclasses on module lookup
_SPEC.loader.exec_module(sync)

GLB_MAGIC = 0x46546C67  # "glTF"
GLB_CHUNK_JSON = 0x4E4F534A  # "JSON"


def make_glb(doc: dict) -> bytes:
    """Minimal GLB container: 12-byte header + single JSON chunk."""
    payload = json.dumps(doc).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)  # chunks are 4-byte aligned
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + 8 + len(payload))
    chunk = struct.pack("<II", len(payload), GLB_CHUNK_JSON) + payload
    return header + chunk


def make_pool(pool: Path) -> None:
    (pool / "assets" / "meshes").mkdir(parents=True)
    (pool / "assets" / "textures").mkdir(parents=True)
    (pool / "assets" / "audio").mkdir(parents=True)
    (pool / "assets" / "meshes" / "plain.glb").write_bytes(make_glb({"asset": {"version": "2.0"}, "scene": 0}))
    (pool / "assets" / "meshes" / "mesh.glb").write_bytes(
        make_glb({"asset": {"version": "2.0"}, "extensionsUsed": ["EXT_meshopt_compression"]})
    )
    (pool / "assets" / "textures" / "rock.png").write_bytes(b"\x89PNG fake image bytes")
    (pool / "assets" / "audio" / "bgm.mp3").write_bytes(b"fake audio")


def make_world(world: Path) -> None:
    (world / "world").mkdir(parents=True)
    (world / "world.xml").write_text(
        '<world clear-color="#000000">'
        '  <GltfScene url="/assets/meshes/plain.glb" />'
        '  <GltfScene url="/assets/meshes/mesh.glb" />'
        '  <Entity><GltfScene url="/assets/textures/rock.png" /></Entity>'
        '  <PlayerGLTF model-url="/assets/audio/bgm.mp3" />'
        '  <GltfScene url="/assets/meshes/gone.glb" />'
        '  <GltfScene url="relative/no-leading-slash.glb" />'
        "</world>",
        encoding="utf-8",
    )


def fake_decompress_factory(calls: list[tuple[Path, Path]]):
    def fake(src: Path, dst: Path) -> None:
        calls.append((src, dst))
        dst.write_bytes(b"decompressed-bytes")

    return fake


class TestGlbInspection:
    def test_glb_with_meshopt_is_detected(self) -> None:
        doc = {"asset": {"version": "2.0"}, "extensionsUsed": ["EXT_meshopt_compression"]}
        assert sync.glb_has_meshopt(make_glb(doc)) is True

    def test_plain_glb_is_not_meshopt(self) -> None:
        doc = {"asset": {"version": "2.0"}, "extensionsUsed": ["KHR_materials_unlit"]}
        assert sync.glb_has_meshopt(make_glb(doc)) is False

    def test_glb_without_extensions_key(self) -> None:
        assert sync.glb_has_meshopt(make_glb({"asset": {"version": "2.0"}})) is False

    def test_non_glb_bytes_return_none(self) -> None:
        assert sync.glb_json_chunk(b"PK\x03\x04 not a glb") is None
        assert sync.glb_json_chunk(b"") is None


class TestUrlExtraction:
    def test_extracts_url_model_url_and_texture_url(self) -> None:
        text = (
            '<GltfScene url="/assets/a.glb" />'
            '<PlayerGLTF model-url="/assets/b.glb" />'
            '<Pad texture-url="/assets/c.png" other="x" />'
        )
        assert sync.extract_urls(text) == ["/assets/a.glb", "/assets/b.glb", "/assets/c.png"]

    def test_collect_urls_is_deduplicated_across_files(self, tmp_path: Path) -> None:
        (tmp_path / "world").mkdir()
        (tmp_path / "world.xml").write_text('<GltfScene url="/assets/a.glb" />', encoding="utf-8")
        (tmp_path / "world" / "sub.xml").write_text(
            '<GltfScene url="/assets/a.glb" /><GltfScene url="/assets/b.glb" />', encoding="utf-8"
        )
        assert sync.collect_urls(tmp_path) == ["/assets/a.glb", "/assets/b.glb"]


class TestSync:
    def test_sync_mirrors_links_and_decompresses(self, tmp_path: Path) -> None:
        pool, world = tmp_path / "pool", tmp_path / "world"
        make_pool(pool)
        make_world(world)
        calls: list[tuple[Path, Path]] = []

        stats = sync.sync_assets(world, pool, decompress=fake_decompress_factory(calls))

        # plain glb + png are hardlinked (same content, same inode)
        plain = world / "assets" / "meshes" / "plain.glb"
        png = world / "assets" / "textures" / "rock.png"
        assert plain.read_bytes() == (pool / "assets" / "meshes" / "plain.glb").read_bytes()
        assert plain.stat().st_ino == (pool / "assets" / "meshes" / "plain.glb").stat().st_ino
        assert png.is_file()
        # meshopt glb went through the injected decompressor
        mesh = world / "assets" / "meshes" / "mesh.glb"
        assert mesh.read_bytes() == b"decompressed-bytes"
        assert len(calls) == 1 and calls[0][0].name == "mesh.glb"
        # mp3 has an ignored extension; missing and relative urls counted
        assert not (world / "assets" / "audio" / "bgm.mp3").exists()
        assert (stats.linked, stats.decompressed) == (2, 1)
        assert stats.ignored == 2  # mp3 + relative url
        assert stats.missing == 1
        assert stats.skipped == 0
        assert stats.errors == 0

    def test_sync_is_idempotent(self, tmp_path: Path) -> None:
        pool, world = tmp_path / "pool", tmp_path / "world"
        make_pool(pool)
        make_world(world)
        calls: list[tuple[Path, Path]] = []
        sync.sync_assets(world, pool, decompress=fake_decompress_factory(calls))
        stats2 = sync.sync_assets(world, pool, decompress=fake_decompress_factory(calls))
        assert (stats2.linked, stats2.decompressed) == (0, 0)
        assert stats2.skipped == 3  # 2 links + 1 decompressed, all already present
        assert len(calls) == 1  # decompressor ran only on the first pass

    def test_sync_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        pool, world = tmp_path / "pool", tmp_path / "world"
        make_pool(pool)
        make_world(world)
        stats = sync.sync_assets(world, pool, dry_run=True, decompress=fake_decompress_factory([]))
        assert not (world / "assets").exists()
        assert (stats.linked, stats.decompressed) == (2, 1)

    def test_decompressor_failure_counts_as_error(self, tmp_path: Path) -> None:
        pool, world = tmp_path / "pool", tmp_path / "world"
        make_pool(pool)
        make_world(world)

        def failing(src: Path, dst: Path) -> None:
            raise RuntimeError("npx exploded")

        stats = sync.sync_assets(world, pool, decompress=failing)
        assert stats.errors == 1
        assert any("npx exploded" in detail for detail in stats.errors_detail)


class TestCli:
    def test_main_dry_run_reports_without_writing(self, tmp_path: Path, capsys) -> None:
        pool, world = tmp_path / "pool", tmp_path / "world"
        make_pool(pool)
        make_world(world)
        code = sync.main(["--world", str(world), "--pool", str(pool), "--dry-run"])
        assert code == 0
        assert not (world / "assets").exists()
        out = capsys.readouterr().out
        assert "1 decomprimidos, 2 ligados" in out
        assert "sem fonte no pool" in out
        assert "MB" in out

    def test_main_missing_pool_errors(self, tmp_path: Path) -> None:
        world = tmp_path / "world"
        world.mkdir()
        try:
            sync.main(["--world", str(world), "--pool", str(tmp_path / "nope")])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected parser.error SystemExit")
