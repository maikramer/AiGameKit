"""Tests for ``rigging3d.transfer_weights``.

Coverage focus (pure-logic + mocked bpy):
- ``TransferResult`` dataclass shape.
- ``_decompress_glb``: npx availability, subprocess outcomes, exceptions.
- ``_bpy_readable_glb``: context-manager branches + tmp cleanup.
- ``transfer_weights`` orchestrator: output-path computation, ``targets_out``
  validation, error propagation, and the ``apply_finish`` round (importable /
  ImportError / finish-exception).
- ``_transfer_one`` error path (source without armature); the happy path
  delegates weight transfer entirely to ``bpy.ops.object.data_transfer`` and
  is therefore not unit-testable without a real Blender scene.

Note: ``transfer_weights`` does **not** perform ``--groups-per-vertex``
limiting or weight renormalisation itself — those happen inside
``bpy.ops.object.data_transfer``. Per "source is truth", we do not assert
behaviour the module does not implement.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rigging3d.transfer_weights import (
    TransferResult,
    _bpy_readable_glb,
    _decompress_glb,
    transfer_weights,
)

# ── TransferResult ──────────────────────────────────────────────────────


class TestTransferResult:
    """``TransferResult`` is a 4-field dataclass (target_in/out, bones, vgroups)."""

    def test_construction_and_fields(self, tmp_path: Path) -> None:
        tin = tmp_path / "in.glb"
        tout = tmp_path / "out.glb"
        r = TransferResult(target_in=tin, target_out=tout, bones=42, vertex_groups=7)
        assert r.target_in == tin
        assert r.target_out == tout
        assert r.bones == 42
        assert r.vertex_groups == 7

    def test_fields_mutable(self, tmp_path: Path) -> None:
        r = TransferResult(target_in=tmp_path / "a", target_out=tmp_path / "b", bones=1, vertex_groups=1)
        r.bones = 99
        r.vertex_groups = 0
        assert r.bones == 99
        assert r.vertex_groups == 0

    def test_equality(self, tmp_path: Path) -> None:
        kwargs = {"target_in": tmp_path / "a", "target_out": tmp_path / "b", "bones": 2, "vertex_groups": 3}
        assert TransferResult(**kwargs) == TransferResult(**kwargs)

    def test_inequality(self, tmp_path: Path) -> None:
        a = TransferResult(target_in=tmp_path / "a", target_out=tmp_path / "b", bones=2, vertex_groups=3)
        b = TransferResult(target_in=tmp_path / "a", target_out=tmp_path / "b", bones=5, vertex_groups=3)
        assert a != b

    def test_repr_contains_fields(self, tmp_path: Path) -> None:
        r = TransferResult(target_in=tmp_path / "in.glb", target_out=tmp_path / "out.glb", bones=4, vertex_groups=2)
        rep = repr(r)
        assert "TransferResult" in rep
        assert "bones=4" in rep


# ── _decompress_glb ─────────────────────────────────────────────────────


class TestDecompressGlb:
    """``_decompress_glb`` wraps ``npx @gltf-transform/cli copy``."""

    def test_npx_missing_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        with patch("rigging3d.transfer_weights.shutil.which", return_value=None):
            assert _decompress_glb(src, dst) is False

    def test_returncode_nonzero_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        dst.write_bytes(b"y")
        mock_proc = MagicMock(returncode=1, stderr="some error text")
        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch("rigging3d.transfer_weights.subprocess.run", return_value=mock_proc),
        ):
            assert _decompress_glb(src, dst) is False

    def test_returncode_zero_dst_missing_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        mock_proc = MagicMock(returncode=0, stderr="")
        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch("rigging3d.transfer_weights.subprocess.run", return_value=mock_proc),
        ):
            assert _decompress_glb(src, dst) is False

    def test_returncode_zero_dst_present_returns_true(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        dst.write_bytes(b"decompressed")
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **_kwargs: object) -> object:
            captured["cmd"] = cmd
            return MagicMock(returncode=0, stderr="")

        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch("rigging3d.transfer_weights.subprocess.run", side_effect=fake_run),
        ):
            assert _decompress_glb(src, dst) is True
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert cmd[0] == "npx"
        assert "@gltf-transform/cli" in cmd
        assert "copy" in cmd
        assert str(src) in cmd
        assert str(dst) in cmd

    def test_timeout_is_300s(self, tmp_path: Path) -> None:
        """The subprocess call must use a 300s timeout."""
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        dst.write_bytes(b"ok")
        captured: dict[str, object] = {}

        def fake_run(_cmd: list[str], **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch("rigging3d.transfer_weights.subprocess.run", side_effect=fake_run),
        ):
            assert _decompress_glb(src, dst) is True
        assert captured["timeout"] == 300
        assert captured["capture_output"] is True
        assert captured["text"] is True

    def test_filenotfound_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch("rigging3d.transfer_weights.subprocess.run", side_effect=FileNotFoundError("no npx")),
        ):
            assert _decompress_glb(src, dst) is False

    def test_timeout_expired_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "a.glb"
        src.write_bytes(b"x")
        dst = tmp_path / "b.glb"
        with (
            patch("rigging3d.transfer_weights.shutil.which", return_value="/usr/bin/npx"),
            patch(
                "rigging3d.transfer_weights.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=300),
            ),
        ):
            assert _decompress_glb(src, dst) is False


# ── _bpy_readable_glb ───────────────────────────────────────────────────


class TestBpyReadableGlb:
    """Context manager that yields a bpy-importable GLB path."""

    def test_decompress_succeeds_yields_tmp_and_cleans_up(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        src.write_bytes(b"x")
        yielded: list[Path] = []
        with patch("rigging3d.transfer_weights._decompress_glb", return_value=True) as mock_dec:
            with _bpy_readable_glb(src) as p:
                yielded.append(p)
                assert p != src.resolve()
                assert p.suffix == ".glb"
            mock_dec.assert_called_once()
        assert not yielded[0].exists()

    def test_decompress_fails_yields_resolved_src(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        src.write_bytes(b"x")
        with patch("rigging3d.transfer_weights._decompress_glb", return_value=False):
            with _bpy_readable_glb(src) as p:
                assert p == src.resolve()
        assert src.exists()

    def test_tmp_cleaned_up_even_on_exception(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        src.write_bytes(b"x")
        seen: list[Path] = []
        with patch("rigging3d.transfer_weights._decompress_glb", return_value=True):
            with pytest.raises(RuntimeError, match="boom"):
                with _bpy_readable_glb(src) as p:
                    seen.append(p)
                    raise RuntimeError("boom")
        assert seen and not seen[0].exists()


# ── transfer_weights orchestrator ───────────────────────────────────────


def _fake_transfer_one_factory(sink: list[Path]) -> object:
    """Build a ``_transfer_one`` replacement that records (target, output)."""

    def fake_one(_source: Path, target: Path, output: Path) -> TransferResult:
        sink.append(output)
        return TransferResult(target_in=target, target_out=output, bones=1, vertex_groups=2)

    return fake_one


class TestTransferWeightsPaths:
    """Output-path computation and orchestration in ``transfer_weights``."""

    def test_targets_out_length_mismatch_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        with pytest.raises(ValueError, match="mesmo tamanho"):
            transfer_weights(
                src,
                [tmp_path / "a.glb"],
                targets_out=[tmp_path / "o1.glb", tmp_path / "o2.glb"],
                apply_finish=False,
            )

    def test_default_suffix_in_target_parent(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        tgt = tmp_path / "mesh.glb"
        outputs: list[Path] = []
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)):
            res = transfer_weights(src, [tgt], apply_finish=False)
        assert outputs == [tmp_path / "mesh_rigged.glb"]
        assert len(res) == 1
        assert res[0].target_out == tmp_path / "mesh_rigged.glb"

    def test_custom_suffix(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        tgt = tmp_path / "hero.glb"
        outputs: list[Path] = []
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)):
            transfer_weights(src, [tgt], output_suffix="_skinned", apply_finish=False)
        assert outputs == [tmp_path / "hero_skinned.glb"]

    def test_output_dir_overrides_target_parent(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        tgt = tmp_path / "sub" / "mesh.glb"
        tgt.parent.mkdir()
        out_dir = tmp_path / "out"
        outputs: list[Path] = []
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)):
            transfer_weights(src, [tgt], output_dir=out_dir, apply_finish=False)
        assert outputs == [out_dir / "mesh_rigged.glb"]

    def test_output_dir_with_custom_suffix(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        tgt = tmp_path / "mesh.glb"
        out_dir = tmp_path / "out"
        outputs: list[Path] = []
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)):
            transfer_weights(src, [tgt], output_dir=out_dir, output_suffix="_LOD0", apply_finish=False)
        assert outputs == [out_dir / "mesh_LOD0.glb"]

    def test_explicit_targets_out_ignores_dir_and_suffix(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        explicit = tmp_path / "explicit_rig.glb"
        outputs: list[Path] = []
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)):
            transfer_weights(
                src,
                [tmp_path / "t.glb"],
                output_dir=tmp_path / "ignored",
                output_suffix="_ignored",
                targets_out=[explicit],
                apply_finish=False,
            )
        assert outputs == [explicit]

    def test_multiple_targets_each_transferred_in_order(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        tgts = [tmp_path / "lod0.glb", tmp_path / "lod1.glb", tmp_path / "lod2.glb"]
        seen_targets: list[Path] = []
        seen_outputs: list[Path] = []

        def fake_one(_s: Path, t: Path, o: Path) -> TransferResult:
            seen_targets.append(t)
            seen_outputs.append(o)
            return TransferResult(target_in=t, target_out=o, bones=0, vertex_groups=0)

        with patch("rigging3d.transfer_weights._transfer_one", side_effect=fake_one):
            res = transfer_weights(src, tgts, apply_finish=False)
        assert seen_targets == tgts
        assert seen_outputs == [
            tmp_path / "lod0_rigged.glb",
            tmp_path / "lod1_rigged.glb",
            tmp_path / "lod2_rigged.glb",
        ]
        assert [r.target_in for r in res] == tgts

    def test_transfer_one_error_propagates(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        with patch("rigging3d.transfer_weights._transfer_one", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                transfer_weights(src, [tmp_path / "t.glb"], apply_finish=False)


class TestTransferWeightsFinish:
    """``apply_finish`` round (gltf_transform_finish import + invocation)."""

    def test_finish_false_never_imports_gltf_finish(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        fake_mod = MagicMock()
        outputs: list[Path] = []
        with (
            patch.dict(sys.modules, {"text3d.utils.gltf_finish": fake_mod}),
            patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)),
        ):
            transfer_weights(src, [tmp_path / "t.glb"], apply_finish=False)
        fake_mod.gltf_transform_finish.assert_not_called()

    def test_finish_true_calls_gltf_finish_per_result(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        fake_mod = MagicMock()
        outputs: list[Path] = []
        with (
            patch.dict(sys.modules, {"text3d.utils.gltf_finish": fake_mod}),
            patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)),
        ):
            res = transfer_weights(
                src,
                [tmp_path / "a.glb", tmp_path / "b.glb"],
                apply_finish=True,
            )
        assert len(res) == 2
        assert fake_mod.gltf_transform_finish.call_count == 2
        expected_calls = [(r.target_out, r.target_out) for r in res]
        actual_calls = [(c.args[0], c.args[1]) for c in fake_mod.gltf_transform_finish.call_args_list]
        assert actual_calls == expected_calls

    def test_finish_true_importerror_is_warned_not_raised(self, tmp_path: Path) -> None:
        src = tmp_path / "src.glb"
        outputs: list[Path] = []
        # A None entry in sys.modules forces ImportError on the from-import.
        with (
            patch.dict(sys.modules, {"text3d.utils.gltf_finish": None}),
            patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)),
        ):
            res = transfer_weights(src, [tmp_path / "a.glb"], apply_finish=True)
        assert len(res) == 1

    def test_finish_exception_is_warned_not_raised(self, tmp_path: Path) -> None:
        """A failing gltf_transform_finish must not abort the whole batch."""
        src = tmp_path / "src.glb"
        fake_mod = MagicMock()
        fake_mod.gltf_transform_finish.side_effect = RuntimeError("finish boom")
        outputs: list[Path] = []
        with (
            patch.dict(sys.modules, {"text3d.utils.gltf_finish": fake_mod}),
            patch("rigging3d.transfer_weights._transfer_one", side_effect=_fake_transfer_one_factory(outputs)),
        ):
            res = transfer_weights(src, [tmp_path / "a.glb", tmp_path / "b.glb"], apply_finish=True)
        assert len(res) == 2
        assert fake_mod.gltf_transform_finish.call_count == 2


# ── _transfer_one error path ────────────────────────────────────────────


class TestTransferOneErrorPaths:
    """``_transfer_one`` is deeply coupled to ``bpy.ops``; only the early
    validation error paths are unit-testable without a real Blender scene.

    The happy path delegates weight transfer entirely to
    ``bpy.ops.object.data_transfer`` (POLYINTERP_NEAREST → NEAREST fallback)
    and the GLTF exporter, which require genuine mesh/armature data.
    """

    def test_source_without_armature_raises(self, tmp_path: Path) -> None:
        from rigging3d import transfer_weights as tw

        src = tmp_path / "src.glb"
        src.write_bytes(b"x")
        tgt = tmp_path / "tgt.glb"
        tgt.write_bytes(b"x")
        out = tmp_path / "out.glb"
        empty_mesh = MagicMock()

        @contextmanager
        def fake_readable(path: Path):
            yield path

        with (
            patch("gamedev_shared.bpy_mesh.clear_scene"),
            patch.object(tw, "_bpy_readable_glb", side_effect=fake_readable),
            patch.object(tw, "_import_glb", return_value=(empty_mesh, [])),
        ):
            with pytest.raises(ValueError, match="Source GLB sem armature"):
                tw._transfer_one(src, tgt, out)
