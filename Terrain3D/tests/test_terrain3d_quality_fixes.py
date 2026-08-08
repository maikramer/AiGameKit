"""Testes das correções de qualidade do Terrain3D (2026-07):

- export_ahgt: formato binário uint16+deflate compatível com o parseAhgt do VibeGame;
- vramd_payload: payload vramd transporta TODOS os params de geração/pós-processamento;
- worker adapter: overrides por request aplicados mesmo com worker quente;
- CLI: delegação vramd não descarta flags; coarse_window deriva size; --format ahgt;
- generator: guard-rail de escala horizontal (check_scale_coherence).

Sem GPU — todo o pipeline pesado é mockado.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import numpy as np
import pytest
from click.testing import CliRunner

from terrain3d.export import AHGT_MAGIC, AHGT_VERSION, export_ahgt
from terrain3d.generator import TerrainConfig, TerrainResult, check_scale_coherence
from terrain3d.vramd_payload import build_generate_request


def _parse_ahgt(path: Path) -> dict:
    """Parser mínimo do .ahgt (espelha ahgt-format.ts do VibeGame)."""
    blob = Path(path).read_bytes()
    magic, version, width, height, _flags, _reserved = struct.unpack_from("<IHHHHI", blob, 0)
    assert magic == AHGT_MAGIC
    assert version == AHGT_VERSION
    (meta_len,) = struct.unpack_from("<I", blob, 16)
    meta = json.loads(blob[20 : 20 + meta_len].decode("utf-8"))
    raw = zlib.decompress(blob[20 + meta_len :])
    grid = np.frombuffer(raw, dtype="<u2").reshape(height, width)
    return {"width": width, "height": height, "meta": meta, "grid": grid}


class TestExportAhgt:
    def test_header_layout(self, tmp_path: Path) -> None:
        h = np.zeros((8, 16), dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=640.0, max_height=50.0)
        parsed = _parse_ahgt(out)
        assert parsed["width"] == 16
        assert parsed["height"] == 8

    def test_suffix_enforced(self, tmp_path: Path) -> None:
        h = np.zeros((4, 4), dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.png", world_size=100.0, max_height=10.0)
        assert out.suffix == ".ahgt"
        assert out.is_file()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        h = np.zeros((4, 4), dtype=np.float64)
        out = export_ahgt(h, tmp_path / "a" / "b" / "t.ahgt", world_size=100.0, max_height=10.0)
        assert out.is_file()

    def test_metadata_block(self, tmp_path: Path) -> None:
        h = np.zeros((4, 4), dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=640.0, max_height=123.5)
        meta = _parse_ahgt(out)["meta"]
        assert meta["worldSize"] == pytest.approx(640.0)
        assert meta["maxHeight"] == pytest.approx(123.5)
        assert meta["originX"] == 0
        assert meta["originZ"] == 0

    def test_quantization_roundtrip(self, tmp_path: Path) -> None:
        h = np.array([[0.0, 0.5, 1.0], [0.25, 0.75, 1.0 / 3.0]], dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=100.0, max_height=10.0)
        grid = _parse_ahgt(out)["grid"]
        assert grid[0, 0] == 0
        assert grid[0, 2] == 65535
        assert grid[0, 1] == pytest.approx(32768, abs=1)
        np.testing.assert_allclose(grid / 65535.0, h, atol=1e-3)

    def test_clips_out_of_range(self, tmp_path: Path) -> None:
        h = np.array([[-1.0, 2.0]], dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=100.0, max_height=10.0)
        grid = _parse_ahgt(out)["grid"]
        assert grid[0, 0] == 0
        assert grid[0, 1] == 65535

    def test_flat_heightmap(self, tmp_path: Path) -> None:
        h = np.full((16, 16), 0.42, dtype=np.float64)
        out = export_ahgt(h, tmp_path / "flat.ahgt", world_size=100.0, max_height=10.0)
        grid = _parse_ahgt(out)["grid"]
        assert grid.min() == grid.max()

    def test_precision_beats_png(self, tmp_path: Path) -> None:
        """Erro de quantização uint16 ≪ uint8 (3mm vs 0.78m a 200m)."""
        rng = np.random.default_rng(0)
        h = rng.random((32, 32), dtype=np.float64)
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=100.0, max_height=200.0)
        grid = _parse_ahgt(out)["grid"]
        err_ahgt_m = float(np.abs(grid / 65535.0 - h).max()) * 200.0
        err_png_m = float(np.abs(np.rint(h * 255.0) / 255.0 - h).max()) * 200.0
        assert err_ahgt_m < 0.005
        assert err_ahgt_m < err_png_m / 100.0

    def test_oversized_grid_rejected(self, tmp_path: Path) -> None:
        h = np.zeros((70000, 2), dtype=np.float64)
        with pytest.raises(ValueError, match="65535"):
            export_ahgt(h, tmp_path / "big.ahgt", world_size=1.0, max_height=1.0)

    def test_row_major_order_matches_sampler(self, tmp_path: Path) -> None:
        """data[row*width+col] — mesma convenção do HeightSampler (linha 0 = topo)."""
        h = np.arange(12, dtype=np.float64).reshape(3, 4) / 11.0
        out = export_ahgt(h, tmp_path / "t.ahgt", world_size=100.0, max_height=10.0)
        grid = _parse_ahgt(out)["grid"]
        np.testing.assert_allclose(grid / 65535.0, h, atol=1e-3)


class TestUmsPayloadCompleto:
    """O payload vramd tem de transportar TODOS os knobs de geração/pós-processamento."""

    ALL_FIELDS: ClassVar[dict] = {
        "seed": 7,
        "size": 1024,
        "world_size": 1536.0,
        "max_height": 80.0,
        "mode": "continental",
        "device": "cuda",
        "prompt": "vales suaves",
        "dtype": "bf16",
        "cache_size": "1G",
        "coarse_window": 4,
        "num_inference_steps": 24,
        "offset_i": 256,
        "offset_j": -128,
        "island_falloff": 0.4,
        "island_noise_scale": 0.2,
        "island_noise_freq": 4.0,
        "smooth_iterations": 0,
        "elevation_gamma": 2.0,
        "elevation_contrast": 0.3,
        "format": "ahgt",
    }

    def test_todos_os_campos_presentes(self) -> None:
        req = build_generate_request(output="/tmp/h.png", **self.ALL_FIELDS)
        for key, value in self.ALL_FIELDS.items():
            assert key in req, f"campo {key} perdido no payload vramd"
            assert req[key] == value

    def test_campos_omitidos_ausentes(self) -> None:
        req = build_generate_request(output="x.png")
        for key in self.ALL_FIELDS:
            assert key not in req

    def test_tipos_coagidos(self) -> None:
        req = build_generate_request(
            output="x.png",
            num_inference_steps=24.0,
            offset_i=256.0,
            smooth_iterations=3.0,
            elevation_gamma=2,
        )
        assert isinstance(req["num_inference_steps"], int)
        assert isinstance(req["offset_i"], int)
        assert isinstance(req["smooth_iterations"], int)
        assert isinstance(req["elevation_gamma"], float)


class TestAdapterOverrides:
    def _adapter(self):
        from terrain3d.worker_serve_adapter import Adapter

        return Adapter()

    def test_load_aceita_offsets_e_steps(self) -> None:
        cfg = self._adapter().load(seed=1, num_inference_steps=24, offset_i=10, offset_j=-5)
        assert cfg.num_inference_steps == 24
        assert cfg.offset_i == 10
        assert cfg.offset_j == -5

    def test_generate_aplica_todos_os_overrides(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        model = adapter.load(seed=1, size=64)
        fake = TerrainResult(
            heightmap=np.full((8, 8), 0.5),
            config=model,
            stats={"generation_time_seconds": 0.1},
        )
        request = {
            "output": str(tmp_path / "h.png"),
            "seed": 99,
            "offset_i": 512,
            "offset_j": -64,
            "num_inference_steps": 28,
            "island_falloff": 0.45,
            "smooth_iterations": 0,
            "elevation_gamma": 2.5,
            "elevation_contrast": 0.33,
            "mode": "continental",
        }
        with (
            patch("terrain3d.generator.generate_terrain", return_value=fake) as mock_gen,
            patch("terrain3d.export.export_heightmap", side_effect=lambda h, o, size: Path(o)),
        ):
            resp = adapter.generate(model, request)
        assert resp["status"] == "ok"
        mock_gen.assert_called_once()
        for key, value in request.items():
            if key == "output":
                continue
            assert getattr(model, key) == value, f"override {key} não aplicado"

    def test_generate_formato_ahgt(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        model = adapter.load(seed=1, size=64, world_size=640.0, max_height=50.0)
        fake = TerrainResult(heightmap=np.full((8, 8), 0.5), config=model, stats={})
        out = tmp_path / "h.png"
        request = {"output": str(out), "format": "ahgt"}
        with patch("terrain3d.generator.generate_terrain", return_value=fake):
            resp = adapter.generate(model, request)
        assert resp["status"] == "ok"
        saved = Path(resp["output"])
        assert saved.suffix == ".ahgt"
        parsed = _parse_ahgt(saved)
        assert parsed["meta"]["worldSize"] == pytest.approx(640.0)

    def test_generate_sem_output_erro(self) -> None:
        adapter = self._adapter()
        model = adapter.load(seed=1)
        resp = adapter.generate(model, {})
        assert resp["status"] == "error"


class TestScaleCoherence:
    def test_ratio_saudavel_sem_aviso(self) -> None:
        # 4096px x 30m / 4000m ≈ 30.7x — dentro da banda
        assert check_scale_coherence(4096, 4000.0, 30.0) is None

    def test_ratio_extremo_avisa(self) -> None:
        # Perfil medium antigo: 2048x30 / 512 = 120x
        warn = check_scale_coherence(2048, 512.0, 30.0)
        assert warn is not None
        assert "íngremes" in warn or "comprimida" in warn

    def test_ratio_baixo_avisa(self) -> None:
        warn = check_scale_coherence(512, 20000.0, 30.0)
        assert warn is not None
        assert "esticado" in warn or "suave" in warn

    def test_world_size_zero_sem_aviso(self) -> None:
        assert check_scale_coherence(512, 0.0, 30.0) is None

    def test_limite_32x_sem_aviso(self) -> None:
        # Exatamente 32x ainda é aceite
        assert check_scale_coherence(1024, 1024 * 30.0 / 32.0, 30.0) is None


class TestCliPayloadUMS:
    def _invoke_capture(self, argv: list[str]) -> dict:
        from terrain3d.cli import cli

        captured: dict = {}

        def _fake_delegate(backend: str, **kwargs: object) -> bool:
            captured["backend"] = backend
            captured["payload"] = kwargs.get("payload", {})
            return True

        runner = CliRunner()
        with patch("terrain3d.cli.delegate_or_prepare", side_effect=_fake_delegate):
            result = runner.invoke(cli, argv)
        assert result.exit_code == 0, result.output
        return captured

    def test_delegacao_nao_descarta_flags(self, tmp_path: Path) -> None:
        out = tmp_path / "h.png"
        captured = self._invoke_capture(
            [
                "generate",
                "--output",
                str(out),
                "--seed",
                "7",
                "--size",
                "1024",
                "--world-size",
                "1536",
                "--num-inference-steps",
                "24",
                "--offset-i",
                "256",
                "--offset-j",
                "-128",
                "--island-falloff",
                "0.42",
                "--smooth-iterations",
                "0",
                "--elevation-gamma",
                "2.0",
                "--elevation-contrast",
                "0.3",
                "--mode",
                "continental",
                "--format",
                "ahgt",
            ]
        )
        p = captured["payload"]
        assert p["num_inference_steps"] == 24
        assert p["offset_i"] == 256
        assert p["offset_j"] == -128
        assert p["island_falloff"] == pytest.approx(0.42)
        assert p["smooth_iterations"] == 0
        assert p["elevation_gamma"] == pytest.approx(2.0)
        assert p["elevation_contrast"] == pytest.approx(0.3)
        assert p["mode"] == "continental"
        assert p["format"] == "ahgt"

    def test_coarse_window_deriva_size(self, tmp_path: Path) -> None:
        captured = self._invoke_capture(
            ["generate", "--output", str(tmp_path / "h.png"), "--seed", "1", "--coarse-window", "8"]
        )
        assert captured["payload"]["size"] == 8 * 256

    def test_size_explicito_ganha_do_coarse_window(self, tmp_path: Path) -> None:
        captured = self._invoke_capture(
            [
                "generate",
                "--output",
                str(tmp_path / "h.png"),
                "--seed",
                "1",
                "--size",
                "512",
                "--coarse-window",
                "8",
            ]
        )
        assert captured["payload"]["size"] == 512


class TestCliExportInProcess:
    def test_format_ahgt_escreve_ficheiro(self, tmp_path: Path) -> None:
        from terrain3d.cli import cli

        fake = TerrainResult(
            heightmap=np.full((16, 16), 0.5),
            config=TerrainConfig(seed=1, size=64),
            stats={"generation_time_seconds": 0.1},
        )
        runner = CliRunner()
        with (
            patch("terrain3d.cli.delegate_or_prepare", return_value=False),
            patch("terrain3d.cli.prepare_gpu_exclusive", return_value=None),
            patch("terrain3d.cli.generate_terrain", return_value=fake),
        ):
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "--output",
                    str(tmp_path / "h.png"),
                    "--metadata",
                    str(tmp_path / "terrain.json"),
                    "--seed",
                    "1",
                    "--size",
                    "64",
                    "--world-size",
                    "640",
                    "--no-vramd",
                    "--format",
                    "ahgt",
                    "--quiet",
                ],
            )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "h.ahgt").is_file()
        parsed = _parse_ahgt(tmp_path / "h.ahgt")
        assert parsed["meta"]["maxHeight"] == pytest.approx(50.0)
        assert (tmp_path / "terrain.json").is_file()

    def test_png_default_mantido(self, tmp_path: Path) -> None:
        from terrain3d.cli import cli

        fake = TerrainResult(
            heightmap=np.full((16, 16), 0.5),
            config=TerrainConfig(seed=1, size=64),
            stats={"generation_time_seconds": 0.1},
        )
        runner = CliRunner()
        with (
            patch("terrain3d.cli.delegate_or_prepare", return_value=False),
            patch("terrain3d.cli.prepare_gpu_exclusive", return_value=None),
            patch("terrain3d.cli.generate_terrain", return_value=fake),
        ):
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "--output",
                    str(tmp_path / "h.png"),
                    "--metadata",
                    str(tmp_path / "terrain.json"),
                    "--seed",
                    "1",
                    "--size",
                    "64",
                    "--world-size",
                    "640",
                    "--no-vramd",
                    "--quiet",
                ],
            )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "h.png").is_file()
        assert not (tmp_path / "h.ahgt").exists()

    def test_help_lista_novos_flags(self) -> None:
        from terrain3d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "--help"])
        assert result.exit_code == 0
        for flag in ("--num-inference-steps", "--offset-i", "--offset-j", "--format"):
            assert flag in result.output


class TestQualityProfilesTerrain3d:
    def _profiles(self) -> dict:
        import yaml

        data_path = (
            Path(__file__).resolve().parents[2]
            / "Shared"
            / "src"
            / "aigamekit_shared"
            / "data"
            / "quality-profiles.yaml"
        )
        return yaml.safe_load(data_path.read_text(encoding="utf-8"))["profiles"]

    def test_sem_coarse_window_morto(self) -> None:
        profiles = self._profiles()
        for tier, block in profiles.items():
            ter = block.get("terrain3d")
            assert ter is not None, f"tier {tier} sem secção terrain3d"
            assert "coarse_window" not in ter, f"tier {tier} ainda carrega coarse_window (knob morto)"

    def test_num_inference_steps_por_tier(self) -> None:
        profiles = self._profiles()
        for tier, block in profiles.items():
            steps = block["terrain3d"]["num_inference_steps"]
            assert 1 <= steps <= 64, f"tier {tier}: steps fora de gama"

    def test_world_size_ratio_saudavel(self) -> None:
        """size x 30m / world_size fica dentro da banda do guard-rail (1.5-32x)."""
        profiles = self._profiles()
        for tier, block in profiles.items():
            ter = block["terrain3d"]
            ratio = ter["size"] * 30.0 / ter["world_size"]
            assert 1.5 <= ratio <= 32.0, f"tier {tier}: ratio {ratio:.1f}x fora da banda"

    def test_tiers_mais_altos_menos_compressao(self) -> None:
        profiles = self._profiles()
        ratios = []
        for tier in ("fast", "low", "medium", "high", "highest"):
            ter = profiles[tier]["terrain3d"]
            ratios.append(ter["size"] * 30.0 / ter["world_size"])
        assert ratios == sorted(ratios, reverse=True)


class TestTerrainConfigNovosCampos:
    def test_offsets_default_zero(self) -> None:
        cfg = TerrainConfig()
        assert cfg.offset_i == 0
        assert cfg.offset_j == 0

    def test_offsets_custom(self) -> None:
        cfg = TerrainConfig(offset_i=256, offset_j=-128)
        assert cfg.offset_i == 256
        assert cfg.offset_j == -128
