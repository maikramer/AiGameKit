"""Rig por nível da ladder: LOD distante entrega mesh estático.

Um LOD que carrega o seu próprio esqueleto custa no runtime mesmo quando está
escondido — o VibeGame percorre e recompõe todos os nós da cena por frame, e no
``simple-rpg`` isso eram ~11k ossos (12.3k de 15.4k nós da cena) em níveis de
LOD que ninguém vê animar. ``rig_max_level`` corta os níveis acima do limite.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest


def _synthetic_glb(path: Path, *, tri_count: int = 1200, skinned: bool = True) -> Path:
    doc: dict = {
        "asset": {"version": "2.0"},
        "accessors": [
            {"componentType": 5125, "count": tri_count * 3, "type": "SCALAR"},
            {"componentType": 5126, "count": tri_count * 3, "type": "VEC3"},
        ],
        "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 1}}]}],
        "materials": [{"name": "m"}],
    }
    if skinned:
        doc["skins"] = [{"joints": [0]}]
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw))
    path.write_bytes(header + struct.pack("<II", len(raw), 0x4E4F534A) + raw)
    return path


class TestDefaults:
    def test_default_keeps_rig_through_lod1(self) -> None:
        from text3d.utils.mesh_lod import DEFAULT_RIG_MAX_LEVEL

        assert DEFAULT_RIG_MAX_LEVEL == 1

    def test_gameassets_matches_text3d_default(self) -> None:
        pytest.importorskip("gameassets")
        from gameassets.pipeline import RIG_MAX_LEVEL
        from text3d.utils.mesh_lod import DEFAULT_RIG_MAX_LEVEL

        assert RIG_MAX_LEVEL == DEFAULT_RIG_MAX_LEVEL


class TestLodLevelsWithRig:
    """Selecção de níveis usada pelo rebind de skin no caminho texturizado."""

    def test_default_covers_lod0_and_lod1(self) -> None:
        from text3d.utils.mesh_lod import lod_levels_with_rig

        assert lod_levels_with_rig() == (0, 1)

    def test_zero_leaves_only_lod0_animated(self) -> None:
        from text3d.utils.mesh_lod import lod_levels_with_rig

        assert lod_levels_with_rig(0) == (0,)

    def test_two_keeps_the_whole_ladder_rigged(self) -> None:
        from text3d.utils.mesh_lod import lod_levels_with_rig

        assert lod_levels_with_rig(2) == (0, 1, 2)

    def test_negative_strips_every_level(self) -> None:
        from text3d.utils.mesh_lod import lod_levels_with_rig

        assert lod_levels_with_rig(-1) == ()

    def test_textured_ladder_uses_the_helper(self) -> None:
        """O laço de rebind tem de decidir por ``lod_levels_with_rig``."""
        import inspect

        from text3d.utils import mesh_lod

        source = inspect.getsource(mesh_lod.generate_lod_textured_glb_triplet)
        assert "lod_levels_with_rig(rig_max_level)" in source
        assert "if level not in rigged_levels" in source


class TestGeometricLadderKeepRig:
    """No caminho geométrico o corte é ``_finalize_geometric_lod(keep_rig=...)``."""

    def test_levels_above_limit_are_finalized_without_rig(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import mesh_lod

        src = _synthetic_glb(tmp_path / "asset.glb", tri_count=40_000)
        out = tmp_path / "out"
        out.mkdir()

        monkeypatch.setattr(mesh_lod, "_glb_has_skins", lambda p: True, raising=False)

        def fake_simplify(src_path, dst, target, *, weld=True):
            Path(dst).write_bytes(Path(src_path).read_bytes())
            return int(target)

        monkeypatch.setattr(mesh_lod, "_meshopt_simplify_level", fake_simplify)

        seen: dict[str, bool] = {}

        def fake_finalize(raw, dst, *, texture_size, meshfix, keep_rig=True):
            Path(dst).write_bytes(b"glb")
            seen[Path(dst).stem] = keep_rig

        monkeypatch.setattr(mesh_lod, "_finalize_geometric_lod", fake_finalize)

        mesh_lod.generate_lod_glb_triplet(src, out, "asset")

        assert seen["asset_lod1"] is True
        assert seen["asset_lod2"] is False

    def test_rig_max_level_two_keeps_every_level_rigged(self, tmp_path: Path, monkeypatch) -> None:
        from text3d.utils import mesh_lod

        src = _synthetic_glb(tmp_path / "asset.glb", tri_count=40_000)
        out = tmp_path / "out"
        out.mkdir()

        def fake_simplify(src_path, dst, target, *, weld=True):
            Path(dst).write_bytes(Path(src_path).read_bytes())
            return int(target)

        monkeypatch.setattr(mesh_lod, "_meshopt_simplify_level", fake_simplify)

        seen: dict[str, bool] = {}

        def fake_finalize(raw, dst, *, texture_size, meshfix, keep_rig=True):
            Path(dst).write_bytes(b"glb")
            seen[Path(dst).stem] = keep_rig

        monkeypatch.setattr(mesh_lod, "_finalize_geometric_lod", fake_finalize)

        mesh_lod.generate_lod_glb_triplet(src, out, "asset", rig_max_level=2)

        assert seen["asset_lod1"] is True
        assert seen["asset_lod2"] is True


class TestCli:
    def test_lod_cmd_exposes_rig_max_level(self) -> None:
        from text3d.cli import lod_cmd

        names = {p.name for p in lod_cmd.params}
        assert "rig_max_level" in names

    def test_flag_is_bounded_to_the_three_levels(self) -> None:
        from text3d.cli import lod_cmd

        param = next(p for p in lod_cmd.params if p.name == "rig_max_level")
        assert param.type.min == 0
        assert param.type.max == 2
