"""Tests for pure helpers in ``rigging3d.cli``.

Covers:
- Path/python resolution: ``_package_root``, ``_resolve_root``, ``_resolve_python``.
- Bash discovery: ``_find_bash``, ``_require_bash``.
- Subprocess env builder: ``_make_env``.
- IO arg helpers: ``_validate_io``, ``_io_args``, ``_shell_path``.
- Bone-rename logic: ``_rename_generic_bones`` (complementary to ``test_cli.py``).
- Argv builders: CLI commands propagate ``--seed``, ``--data-name``, env vars,
  and ``--gpu-ids`` (CUDA_VISIBLE_DEVICES) into their subprocess invocations.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rigging3d.cli import (
    _find_bash,
    _io_args,
    _make_env,
    _package_root,
    _rename_generic_bones,
    _require_bash,
    _resolve_python,
    _resolve_root,
    _shell_path,
    _validate_io,
    cli,
)
from rigging3d.cli_rich import click

_IS_WIN = sys.platform == "win32"


def _mock_tree(root: Path) -> None:
    """Create a minimal UniRig-like tree (configs/ + src/) for ``_resolve_root``."""
    (root / "configs" / "model").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "task").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "model" / "unirig_skin.yaml").write_text("num_train_vertex: 512\n")
    (root / "configs" / "task" / "quick_inference_unirig_skin.yaml").write_text("components:\n  model: unirig_skin\n")


# ── _package_root / _resolve_root ───────────────────────────────────────


class TestPackageRoot:
    def test_points_at_bundled_unirig(self) -> None:
        pr = _package_root()
        assert pr.name == "unirig"
        assert (pr / "configs").is_dir()
        assert (pr / "src").is_dir()


class TestResolveRoot:
    def test_explicit_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        explicit = tmp_path / "explicit"
        _mock_tree(explicit)
        other = tmp_path / "other"
        _mock_tree(other)
        monkeypatch.setenv("RIGGING3D_ROOT", str(other))
        assert _resolve_root(explicit) == explicit.resolve()

    def test_env_used_when_no_explicit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_root = tmp_path / "envroot"
        _mock_tree(env_root)
        monkeypatch.setenv("RIGGING3D_ROOT", str(env_root))
        assert _resolve_root(None) == env_root.resolve()

    def test_blank_env_falls_back_to_package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_ROOT", "   ")
        assert _resolve_root(None) == _package_root()

    def test_unset_env_falls_back_to_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RIGGING3D_ROOT", raising=False)
        assert _resolve_root(None) == _package_root()

    def test_missing_configs_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = tmp_path / "bad"
        (bad / "src").mkdir(parents=True)
        monkeypatch.delenv("RIGGING3D_ROOT", raising=False)
        with pytest.raises(FileNotFoundError, match="configs/"):
            _resolve_root(bad)

    def test_missing_src_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = tmp_path / "bad"
        (bad / "configs").mkdir(parents=True)
        monkeypatch.delenv("RIGGING3D_ROOT", raising=False)
        with pytest.raises(FileNotFoundError, match="src/"):
            _resolve_root(bad)

    def test_error_message_mentions_env_and_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = tmp_path / "bad"
        bad.mkdir()
        monkeypatch.delenv("RIGGING3D_ROOT", raising=False)
        with pytest.raises(FileNotFoundError) as exc_info:
            _resolve_root(bad)
        msg = str(exc_info.value)
        assert "RIGGING3D_ROOT" in msg
        assert "--root" in msg


# ── _resolve_python ─────────────────────────────────────────────────────


class TestResolvePython:
    def test_explicit_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_PYTHON", "/usr/bin/other")
        assert _resolve_python("/opt/venv/bin/python") == "/opt/venv/bin/python"

    def test_env_used_when_no_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_PYTHON", "/usr/bin/envpy")
        assert _resolve_python(None) == "/usr/bin/envpy"

    def test_blank_env_falls_back_to_sys_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_PYTHON", "   ")
        assert _resolve_python(None) == sys.executable

    def test_unset_env_falls_back_to_sys_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RIGGING3D_PYTHON", raising=False)
        assert _resolve_python(None) == sys.executable


# ── _shell_path ─────────────────────────────────────────────────────────


class TestShellPath:
    def test_returns_resolved_str(self, tmp_path: Path) -> None:
        p = tmp_path / "mesh.glb"
        p.write_bytes(b"x")
        assert _shell_path(p) == str(p.resolve())

    def test_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/test")
        assert _shell_path(Path("~")) == "/home/test"


# ── _find_bash / _require_bash ──────────────────────────────────────────


@pytest.mark.skipif(_IS_WIN, reason="POSIX-only bash discovery path")
class TestFindBashPosix:
    def test_returns_which_result(self) -> None:
        with patch("rigging3d.cli.shutil.which", return_value="/usr/bin/bash"):
            assert _find_bash() == "/usr/bin/bash"

    def test_returns_none_when_missing(self) -> None:
        with patch("rigging3d.cli.shutil.which", return_value=None):
            assert _find_bash() is None


class TestRequireBash:
    def test_raises_when_bash_missing(self) -> None:
        with patch("rigging3d.cli._find_bash", return_value=None):
            with pytest.raises(click.ClickException, match="bash"):
                _require_bash()

    def test_passes_when_bash_found(self) -> None:
        with patch("rigging3d.cli._find_bash", return_value="/bin/bash"):
            _require_bash()


# ── _make_env ───────────────────────────────────────────────────────────


class TestMakeEnv:
    def test_pythonpath_prepends_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYTHONPATH", "/existing/path")
        env = _make_env(tmp_path)
        assert env["PYTHONPATH"] == f"{tmp_path}{os.pathsep}/existing/path"

    def test_pythonpath_root_only_when_no_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
        env = _make_env(tmp_path)
        assert env["PYTHONPATH"] == str(tmp_path)

    def test_python_bin_prepends_bindir_and_sets_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _make_env(tmp_path, python_bin="/opt/venv/bin/python")
        assert env["PYTHON"] == "/opt/venv/bin/python"
        assert env["PATH"].split(os.pathsep)[0] == "/opt/venv/bin"

    def test_extra_merged(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path, extra={"FOO": "bar"})
        assert env["FOO"] == "bar"

    def test_gpu_ids_sets_cuda_visible_devices(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path, gpu_ids=[0, 1])
        assert env["CUDA_VISIBLE_DEVICES"] == "0,1"

    def test_no_gpu_ids_leaves_cuda_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        env = _make_env(tmp_path, gpu_ids=None)
        assert "CUDA_VISIBLE_DEVICES" not in env

    @pytest.mark.skipif(_IS_WIN, reason="POSIX-only GPU env defaults")
    def test_posix_gpu_env_defaults_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("PYOPENGL_PLATFORM", "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME"):
            monkeypatch.delenv(var, raising=False)
        env = _make_env(tmp_path)
        assert env["PYOPENGL_PLATFORM"] == "egl"
        assert env["__NV_PRIME_RENDER_OFFLOAD"] == "1"
        assert env["__GLX_VENDOR_LIBRARY_NAME"] == "nvidia"

    def test_propagate_profile_sets_gamedev_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GAMEDEV_PROFILE", raising=False)
        env = _make_env(tmp_path, propagate_profile=True)
        assert env["GAMEDEV_PROFILE"] == "1"

    def test_extra_does_not_override_pythonpath_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
        env = _make_env(tmp_path, extra={"PYTHONPATH": "/should/be/overwritten"})
        assert env["PYTHONPATH"].startswith(str(tmp_path))


# ── _validate_io ────────────────────────────────────────────────────────


class TestValidateIo:
    def test_input_and_output_ok(self, tmp_path: Path) -> None:
        _validate_io(tmp_path / "in.glb", tmp_path / "out.glb", None, None)

    def test_input_dir_and_output_dir_ok(self, tmp_path: Path) -> None:
        _validate_io(None, None, tmp_path / "in", tmp_path / "out")

    def test_input_dir_without_output_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(click.ClickException, match="--output-dir"):
            _validate_io(None, None, tmp_path / "in", None)

    def test_neither_input_nor_dirs_raises(self) -> None:
        with pytest.raises(click.ClickException, match="--input e --output"):
            _validate_io(None, None, None, None)

    def test_missing_output_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(click.ClickException):
            _validate_io(tmp_path / "in.glb", None, None, None)


# ── _io_args ────────────────────────────────────────────────────────────


class TestIoArgs:
    def test_file_mode_emits_input_output(self, tmp_path: Path) -> None:
        inp = tmp_path / "mesh.glb"
        out = tmp_path / "skel.glb"
        args = _io_args(inp, out, None, None)
        assert "--input" in args
        assert "--output" in args
        assert str(inp.resolve()) in args
        assert str(out.resolve()) in args
        assert "--output_dir" not in args

    def test_file_mode_with_output_dir_appends_output_dir(self, tmp_path: Path) -> None:
        inp = tmp_path / "mesh.glb"
        out = tmp_path / "skel.glb"
        odir = tmp_path / "results"
        args = _io_args(inp, out, None, odir)
        assert "--output_dir" in args
        assert str(odir.resolve()) in args

    def test_dir_mode_emits_input_dir_output_dir(self, tmp_path: Path) -> None:
        idir = tmp_path / "in"
        odir = tmp_path / "out"
        args = _io_args(None, None, idir, odir)
        assert "--input_dir" in args
        assert "--output_dir" in args
        assert str(idir.resolve()) in args
        assert str(odir.resolve()) in args
        assert "--input" not in args

    def test_dir_mode_args_ordering(self, tmp_path: Path) -> None:
        idir = tmp_path / "in"
        odir = tmp_path / "out"
        args = _io_args(None, None, idir, odir)
        assert args[0] == "--input_dir"
        assert args[2] == "--output_dir"


# ── _rename_generic_bones (complementary to test_cli.py) ────────────────


def _write_glb(tmp_path: Path, name: str, nodes: list[dict[str, object]]) -> Path:
    """Write a minimal GLB containing only the JSON node table."""
    glb_json = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
    }
    json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad
    bin_data = b"\x00" * 4
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    glb = tmp_path / name
    with open(glb, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_data), 0x004E4942))
        f.write(bin_data)
    return glb


class TestRenameGenericBonesExtra:
    """Extra cases for ``_rename_generic_bones`` beyond those in ``test_cli.py``."""

    def test_mixamo_prefixed_names_untouched(self, tmp_path: Path) -> None:
        nodes = [
            {"name": "mixamorig:Hips", "children": [1]},
            {"name": "mixamorig:Spine"},
        ]
        glb = _write_glb(tmp_path, "mixamo.glb", nodes)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_unirig_semantic_names_untouched(self, tmp_path: Path) -> None:
        nodes = [
            {"name": "root", "children": [1, 2]},
            {"name": "J_Bip_C_Hips"},
            {"name": "J_Bip_L_UpperArm"},
        ]
        glb = _write_glb(tmp_path, "unirig.glb", nodes)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_tiny_file_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "tiny.glb"
        glb.write_bytes(b"\x00\x00")
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_bad_magic_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "bad.glb"
        glb.write_bytes(b"\x00" * 64)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_malformed_json_chunk_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "malformed.glb"
        payload = b"{not valid json"
        pad = (4 - len(payload) % 4) % 4
        payload += b" " * pad
        bin_data = b"\x00" * 4
        total = 12 + 8 + len(payload) + 8 + len(bin_data)
        with open(glb, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(payload), 0x4E4F534A))
            f.write(payload)
            f.write(struct.pack("<II", len(bin_data), 0x004E4942))
            f.write(bin_data)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_generic_bones_get_renamed_and_file_grows_or_shrinks(self, tmp_path: Path) -> None:
        """A single-chain generic rig is renamed; the GLB stays parseable."""
        nodes = [
            {"name": "bone_0", "children": [1]},
            {"name": "bone_1"},
        ]
        glb = _write_glb(tmp_path, "gen.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        assert count == 2
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = [n["name"] for n in data["nodes"]]
        assert "bone_0" not in names
        assert "bone_1" not in names


# ── Argv builders (CLI commands → subprocess argv) ─────────────────────


class TestArgvPropagation:
    """CLI commands build the right argv/env for their subprocess calls."""

    def test_skeleton_seed_propagates(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: dict[str, object] = {}

        def fake_run_bash(_root: Path, _script: str, args: list[str], **kwargs: object) -> int:
            captured["args"] = list(args)
            captured["kwargs"] = kwargs
            return 0

        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", side_effect=fake_run_bash),
        ):
            result = CliRunner().invoke(
                cli,
                ["--root", str(root), "--no-hw-auto", "skeleton", "-i", "m.glb", "-o", "s.glb", "--seed", "42"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        args = captured["args"]
        assert "--seed" in args
        assert "42" in args
        assert "--skeleton_task" in args

    def test_skeleton_task_config_propagates(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: list[str] = []

        def fake_run_bash(_root: Path, _script: str, args: list[str], **_kw: object) -> int:
            captured.extend(args)
            return 0

        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", side_effect=fake_run_bash),
        ):
            CliRunner().invoke(
                cli,
                [
                    "--root",
                    str(root),
                    "--no-hw-auto",
                    "skeleton",
                    "-i",
                    "m.glb",
                    "-o",
                    "s.glb",
                    "--skeleton-task",
                    "custom.yaml",
                ],
                catch_exceptions=False,
            )
        assert "custom.yaml" in captured

    def test_skin_seed_and_data_name_propagate(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: list[str] = []

        def fake_run_bash(_root: Path, _script: str, args: list[str], **_kw: object) -> int:
            captured.extend(args)
            return 0

        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", side_effect=fake_run_bash),
        ):
            CliRunner().invoke(
                cli,
                [
                    "--root",
                    str(root),
                    "--no-hw-auto",
                    "skin",
                    "-i",
                    "s.glb",
                    "-o",
                    "sk.glb",
                    "--seed",
                    "7",
                    "--data-name",
                    "custom.npz",
                ],
                catch_exceptions=False,
            )
        assert "--seed" in captured and "7" in captured
        assert "--data_name" in captured and "custom.npz" in captured

    def test_global_gpu_ids_reach_run_bash(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: dict[str, object] = {}

        def fake_run_bash(_root: Path, _script: str, _args: list[str], **kwargs: object) -> int:
            captured.update(kwargs)
            return 0

        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", side_effect=fake_run_bash),
        ):
            CliRunner().invoke(
                cli,
                ["--root", str(root), "--no-hw-auto", "--gpu-ids", "0,1", "skeleton", "-i", "m.glb", "-o", "s.glb"],
                catch_exceptions=False,
            )
        assert captured["gpu_ids"] == [0, 1]

    def test_merge_env_vars_propagated(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: dict[str, object] = {}

        def fake_run_module(_root: Path, _py: str, module: str, args: list[str], **kwargs: object) -> int:
            captured["module"] = module
            captured["args"] = list(args)
            captured["env"] = kwargs.get("env")
            return 0

        with patch("rigging3d.cli._run_module", side_effect=fake_run_module):
            result = CliRunner().invoke(
                cli,
                [
                    "--root",
                    str(root),
                    "--no-hw-auto",
                    "merge",
                    "-s",
                    "skin.glb",
                    "-t",
                    "mesh.glb",
                    "-o",
                    "out.glb",
                    "--smooth-iterations",
                    "5",
                    "--groups-per-vertex",
                    "4",
                    "--draco",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert captured["module"] == "src.inference.merge"
        env = captured["env"]
        assert env["RIGGING3D_SMOOTH_ITERATIONS"] == "5"
        assert env["RIGGING3D_GROUPS_PER_VERTEX"] == "4"
        assert env["RIGGING3D_DRACO"] == "1"

    def test_merge_draco_off_env(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: dict[str, object] = {}

        def fake_run_module(_root: Path, _py: str, _module: str, _args: list[str], **kwargs: object) -> int:
            captured["env"] = kwargs.get("env")
            return 0

        with patch("rigging3d.cli._run_module", side_effect=fake_run_module):
            CliRunner().invoke(
                cli,
                [
                    "--root",
                    str(root),
                    "--no-hw-auto",
                    "merge",
                    "-s",
                    "s.glb",
                    "-t",
                    "m.glb",
                    "-o",
                    "o.glb",
                    "--no-draco",
                ],
                catch_exceptions=False,
            )
        assert captured["env"]["RIGGING3D_DRACO"] == "0"

    def test_merge_require_suffix_propagated(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        _mock_tree(root)
        captured: list[str] = []

        def fake_run_module(_root: Path, _py: str, _module: str, args: list[str], **_kw: object) -> int:
            captured.extend(args)
            return 0

        with patch("rigging3d.cli._run_module", side_effect=fake_run_module):
            CliRunner().invoke(
                cli,
                [
                    "--root",
                    str(root),
                    "--no-hw-auto",
                    "merge",
                    "-s",
                    "s.glb",
                    "-t",
                    "m.glb",
                    "-o",
                    "o.glb",
                    "--require-suffix",
                    "glb,fbx",
                ],
                catch_exceptions=False,
            )
        assert any(a.startswith("--require_suffix=") for a in captured)
        suffix_arg = next(a for a in captured if a.startswith("--require_suffix="))
        assert "glb,fbx" in suffix_arg

    def test_transfer_weights_outputs_mismatch_errors(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "transfer-weights",
                "-s",
                str(tmp_path / "src.glb"),
                "-t",
                str(tmp_path / "a.glb"),
                "-o",
                str(tmp_path / "o1.glb"),
                "-o",
                str(tmp_path / "o2.glb"),
            ],
        )
        assert result.exit_code != 0
