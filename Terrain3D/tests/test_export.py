from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from terrain3d.export import export_heightmap, export_metadata
from terrain3d.generator import TerrainConfig, TerrainResult


@pytest.fixture
def sample_heightmap() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.random((256, 256), dtype=np.float64)


@pytest.fixture
def sample_config() -> TerrainConfig:
    return TerrainConfig(
        model_id="xandergos/terrain-diffusion-30m",
        seed=42,
        size=256,
        world_size=128.0,
        max_height=30.0,
        prompt="test terrain",
    )


@pytest.fixture
def sample_result(sample_heightmap: np.ndarray, sample_config: TerrainConfig) -> TerrainResult:
    return TerrainResult(
        heightmap=sample_heightmap,
        config=sample_config,
        stats={"generation_time_seconds": 1.5},
    )


class TestExportHeightmap:
    def test_creates_png(self, sample_heightmap: np.ndarray, tmp_path: Path) -> None:
        out = tmp_path / "hmap.png"
        result = export_heightmap(sample_heightmap, str(out), size=256)
        assert result.exists()
        assert result.suffix == ".png"

    def test_creates_parent_dirs(self, sample_heightmap: np.ndarray, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "dir" / "hmap.png"
        result = export_heightmap(sample_heightmap, str(out), size=256)
        assert result.exists()

    def test_output_size(self, sample_heightmap: np.ndarray, tmp_path: Path) -> None:
        from PIL import Image

        out = tmp_path / "hmap.png"
        export_heightmap(sample_heightmap, str(out), size=128)
        img = Image.open(out)
        assert img.size == (128, 128)
        assert img.mode == "L"

    def test_flat_heightmap(self, tmp_path: Path) -> None:
        flat = np.full((64, 64), 0.5, dtype=np.float64)
        out = tmp_path / "flat.png"
        result = export_heightmap(flat, str(out), size=64)
        assert result.exists()


class TestExportMetadata:
    def test_creates_json(self, sample_result: TerrainResult, tmp_path: Path) -> None:
        out = tmp_path / "terrain.json"
        result = export_metadata(sample_result, str(out))
        assert result.exists()
        with open(result) as f:
            data = json.load(f)
        assert data["version"] == "2.0"
        assert data["generator"] == "terrain3d"
        assert data["model_id"] == "xandergos/terrain-diffusion-30m"

    def test_empty_rivers_lakes(self, sample_result: TerrainResult, tmp_path: Path) -> None:
        out = tmp_path / "terrain.json"
        export_metadata(sample_result, str(out))
        with open(out) as f:
            data = json.load(f)
        assert data["rivers"] == []
        assert data["lakes"] == []
        assert data["lake_planes"] == []

    def test_includes_prompt(self, sample_result: TerrainResult, tmp_path: Path) -> None:
        out = tmp_path / "terrain.json"
        export_metadata(sample_result, str(out))
        with open(out) as f:
            data = json.load(f)
        assert data["prompt"] == "test terrain"

    def test_no_prompt_when_none(self, sample_heightmap: np.ndarray, tmp_path: Path) -> None:
        config = TerrainConfig(size=64)
        result = TerrainResult(heightmap=sample_heightmap, config=config, stats={})
        out = tmp_path / "terrain.json"
        export_metadata(result, str(out))
        with open(out) as f:
            data = json.load(f)
        assert "prompt" not in data

    def test_terrain_stats(self, sample_result: TerrainResult, tmp_path: Path) -> None:
        out = tmp_path / "terrain.json"
        export_metadata(sample_result, str(out))
        with open(out) as f:
            data = json.load(f)
        t = data["terrain"]
        assert t["size"] == 256
        assert t["world_size"] == 128.0
        assert t["max_height"] == 30.0


class TestNativeResolution:
    def test_30m_model(self) -> None:
        from terrain3d.generator import _native_resolution_from_model

        assert _native_resolution_from_model("xandergos/terrain-diffusion-30m") == 30.0

    def test_90m_model(self) -> None:
        from terrain3d.generator import _native_resolution_from_model

        assert _native_resolution_from_model("xandergos/terrain-diffusion-90m") == 90.0

    def test_default_30m(self) -> None:
        from terrain3d.generator import _native_resolution_from_model

        assert _native_resolution_from_model("some/other-model") == 30.0
