"""Testes do CLI (sem executar inferência real)."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rigging3d import __version__
from rigging3d.cli import cli


def _mock_tree(p: Path) -> None:
    (p / "configs").mkdir(parents=True)
    (p / "src").mkdir()


def _bash_write_output_fbx(
    root: Path,
    script: str,
    args: list[str],
    *,
    python_bin: str | None = None,
    propagate_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> int:
    """Simula generate_skeleton / generate_skin: escreve o ficheiro indicado em --output (GLB/FBX)."""
    if "--output" in args:
        i = args.index("--output")
        out = Path(args[i + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mockfbx")
    return 0


def _module_write_glb(root: Path, py: str, module: str, args: list[str], **_kwargs) -> int:
    """Simula merge: cria o GLB em --output=."""
    for a in args:
        if a.startswith("--output="):
            p = Path(a.split("=", 1)[1])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"mockglb")
            return 0
    return 1


def _skeleton_inprocess_mock(root: Path, **kwargs) -> None:
    """Simula run_skeleton_inprocess: escreve o ficheiro indicado em output_path."""
    output_path = kwargs.get("output_path")
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"mockfbx")


def _skin_inprocess_mock(root: Path, **kwargs) -> None:
    """Simula run_skin_inprocess: escreve o ficheiro indicado em output_path."""
    output_path = kwargs.get("output_path")
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"mockfbx")


# ── Help / version ─────────────────────────────────────────────────────


class TestHelp:
    def test_root_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "rigging3d" in result.output.lower()

    def test_version(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_skeleton_help(self) -> None:
        result = CliRunner().invoke(cli, ["skeleton", "--help"])
        assert result.exit_code == 0
        assert "--input" in result.output
        assert "--output" in result.output

    def test_skin_help(self) -> None:
        result = CliRunner().invoke(cli, ["skin", "--help"])
        assert result.exit_code == 0
        assert "--data-name" in result.output

    def test_merge_help(self) -> None:
        result = CliRunner().invoke(cli, ["merge", "--help"])
        assert result.exit_code == 0
        assert "--source" in result.output
        assert "--target" in result.output

    def test_pipeline_help(self) -> None:
        result = CliRunner().invoke(cli, ["pipeline", "--help"])
        assert result.exit_code == 0
        assert "--keep-temp" in result.output


# ── skeleton ───────────────────────────────────────────────────────────


class TestSkeleton:
    def test_requires_bash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with patch("rigging3d.cli._find_bash", return_value=None):
            result = CliRunner().invoke(
                cli,
                ["skeleton", "-i", "mesh.glb", "-o", "skel.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "bash" in result.output.lower()

    def test_requires_io(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with patch("rigging3d.cli._find_bash", return_value="/bin/bash"):
            result = CliRunner().invoke(cli, ["skeleton"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "--input" in result.output

    def test_mock_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", return_value=0),
        ):
            result = CliRunner().invoke(
                cli,
                ["skeleton", "-i", "mesh.glb", "-o", "skel.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "concluído" in result.output.lower()

    def test_nonzero_exit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", return_value=1),
        ):
            result = CliRunner().invoke(
                cli,
                ["skeleton", "-i", "mesh.glb", "-o", "skel.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "código 1" in result.output


# ── skin ───────────────────────────────────────────────────────────────


class TestSkin:
    def test_mock_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", return_value=0),
        ):
            result = CliRunner().invoke(
                cli,
                ["skin", "-i", "skel.glb", "-o", "skin.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "skinning concluído" in result.output.lower()

    def test_nonzero_exit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", return_value=2),
        ):
            result = CliRunner().invoke(
                cli,
                ["skin", "-i", "skel.glb", "-o", "skin.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code != 0

    def test_custom_data_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        captured_args: list[str] = []

        def fake_run_bash(_root: Path, _script: str, args: list[str], *, python_bin: str | None = None, **_kwargs: object) -> int:
            captured_args.extend(args)
            return 0

        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", side_effect=fake_run_bash),
        ):
            CliRunner().invoke(
                cli,
                ["skin", "-i", "skel.glb", "-o", "skin.glb", "--data-name", "custom.npz"],
                catch_exceptions=False,
            )
        assert "custom.npz" in captured_args


# ── merge ──────────────────────────────────────────────────────────────


class TestMerge:
    def test_mock_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with patch("rigging3d.cli._run_module", return_value=0):
            result = CliRunner().invoke(
                cli,
                ["merge", "-s", "skin.glb", "-t", "mesh.glb", "-o", "out.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "merge concluído" in result.output.lower()

    def test_nonzero_exit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        with patch("rigging3d.cli._run_module", return_value=3):
            result = CliRunner().invoke(
                cli,
                ["merge", "-s", "skin.glb", "-t", "mesh.glb", "-o", "out.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code != 0

    def test_calls_src_inference_merge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        captured: list[str] = []

        def fake_run_module(_root: Path, _py: str, module: str, _args: list[str], **_kw) -> int:
            captured.append(module)
            return 0

        with patch("rigging3d.cli._run_module", side_effect=fake_run_module):
            CliRunner().invoke(
                cli,
                ["merge", "-s", "skin.glb", "-t", "mesh.glb", "-o", "out.glb"],
                catch_exceptions=False,
            )
        assert captured == ["src.inference.merge"]

    def test_missing_required_args(self) -> None:
        result = CliRunner().invoke(cli, ["merge"])
        assert result.exit_code != 0


# ── pipeline ───────────────────────────────────────────────────────────


class TestPipeline:
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        ur = tmp_path / "r"
        _mock_tree(ur)
        monkeypatch.setenv("RIGGING3D_ROOT", str(ur))
        mesh = tmp_path / "m.glb"
        mesh.write_bytes(b"x")
        return ur, mesh

    def test_no_bash_required_for_pipeline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o.glb"
        with (
            patch("rigging3d.cli._find_bash", return_value=None),
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", side_effect=_module_write_glb),
        ):
            result = CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(out)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

    def test_full_mock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o.glb"
        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", side_effect=_module_write_glb),
        ):
            result = CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(out)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "concluído" in result.output.lower()

    def test_skeleton_failure_aborts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)

        def _skeleton_fail(root: Path, **kwargs) -> None:
            raise RuntimeError("skeleton mock failure")

        with patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_fail):
            result = CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb")],
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "skeleton falhou" in result.output.lower()

    def test_skin_failure_aborts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)

        def _skin_fail(root: Path, **kwargs) -> None:
            raise RuntimeError("skin mock failure")

        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_fail),
        ):
            result = CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb")],
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "skin falhou" in result.output.lower()

    def test_merge_failure_aborts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", return_value=1),
        ):
            result = CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb")],
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "merge falhou" in result.output.lower()

    def test_work_dir_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        wd = tmp_path / "work"
        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", side_effect=_module_write_glb),
        ):
            CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb"), "--work-dir", str(wd)],
                catch_exceptions=False,
            )
        assert wd.is_dir()

    def test_keep_temp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        import tempfile

        created_dirs: list[Path] = []
        orig_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = orig_mkdtemp(**kwargs)
            created_dirs.append(Path(d))
            return d

        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", side_effect=_module_write_glb),
            patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        ):
            CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb"), "--keep-temp"],
                catch_exceptions=False,
            )
        assert len(created_dirs) == 1
        assert created_dirs[0].is_dir()

    def test_temp_cleaned_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ur, mesh = self._setup(tmp_path, monkeypatch)
        import tempfile

        created_dirs: list[Path] = []
        orig_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = orig_mkdtemp(**kwargs)
            created_dirs.append(Path(d))
            return d

        with (
            patch("rigging3d.oneshot.run_skeleton_inprocess", side_effect=_skeleton_inprocess_mock),
            patch("rigging3d.oneshot.run_skin_inprocess", side_effect=_skin_inprocess_mock),
            patch("rigging3d.cli._run_module", side_effect=_module_write_glb),
            patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        ):
            CliRunner().invoke(
                cli,
                ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "o.glb")],
                catch_exceptions=False,
            )
        assert len(created_dirs) == 1
        assert not created_dirs[0].exists()


# ── root option ────────────────────────────────────────────────────────


class TestRootOption:
    def test_bad_root_errors(self, tmp_path: Path) -> None:
        bad = tmp_path / "nope"
        bad.mkdir()
        result = CliRunner().invoke(
            cli,
            ["--root", str(bad), "skeleton", "--help"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_explicit_root_used(self, tmp_path: Path) -> None:
        ur = tmp_path / "r"
        _mock_tree(ur)
        with (
            patch("rigging3d.cli._find_bash", return_value="/bin/bash"),
            patch("rigging3d.cli._run_bash", return_value=0),
        ):
            result = CliRunner().invoke(
                cli,
                ["--root", str(ur), "skeleton", "-i", "mesh.glb", "-o", "skel.glb"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0


# ── _rename_generic_bones ──────────────────────────────────────────────

# NPZ parents for a 28-bone humanoid (model output with cls_none).
_HUMANOID_PARENTS = [None, 0, 1, 2, 3, 4, 3, 6, 7, 8, 9, 10, 11, 3, 13, 14, 15, 16, 17, 18, 0, 20, 21, 22, 0, 24, 25, 26]


def _build_glb_with_bones(tmp_path: Path, parents: list[int | None], prefix: str = "bone_") -> Path:
    """Create a minimal GLB file with bone nodes following *parents*."""
    n = len(parents)
    # Build node hierarchy
    nodes: list[dict[str, object]] = []
    children_of: dict[int, list[int]] = {}
    for i in range(n):
        children_of.setdefault(parents[i], []).append(i) if parents[i] is not None else None
    for i in range(n):
        node: dict[str, object] = {"name": f"{prefix}{i}"}
        kids = children_of.get(i)
        if kids:
            node["children"] = kids
        nodes.append(node)
    # Add mesh + armature wrapper nodes
    mesh_node = {"name": "geometry_0", "mesh": 0, "skin": 0}
    armature_node = {"name": "Armature", "children": [n, len(nodes)]}  # mesh_idx, bone_root
    world_node = {"name": "world", "children": [len(nodes) + 1]}
    # Find root bone index
    root_bi = parents.index(None)
    armature_node["children"] = [len(nodes), root_bi]
    nodes.append(mesh_node)
    nodes.append(armature_node)
    nodes.append(world_node)

    glb_json = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": len(nodes) - 1,
        "scenes": [{"nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": [{"primitives": []}],
        "skins": [{"joints": list(range(n))}],
    }
    json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad
    # Minimal BIN chunk
    bin_data = b"\x00" * 4
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    glb_path = tmp_path / "test.glb"
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_data), 0x004E4942))
        f.write(bin_data)
    return glb_path


def _read_glb_node_names(glb_path: Path) -> list[str]:
    """Read node names from GLB."""
    with open(glb_path, "rb") as f:
        f.read(12)
        c_len, _ = struct.unpack("<II", f.read(8))
        data = json.loads(f.read(c_len))
    return [n.get("name", "") for n in data.get("nodes", [])]


class TestRenameGenericBones:
    """Tests for ``_rename_generic_bones`` tree-based classification."""

    def test_humanoid_28_bones(self, tmp_path: Path) -> None:
        from rigging3d.cli import _rename_generic_bones

        glb = _build_glb_with_bones(tmp_path, _HUMANOID_PARENTS)
        root = tmp_path  # root unused by new implementation
        count = _rename_generic_bones(glb, root)
        names = _read_glb_node_names(glb)

        # All 28 bones should be renamed (body + finger chains)
        assert count == 28

        # Verify key bone names
        bone_names = {n for n in names if not n.startswith("_")}
        expected = {
            "Hips", "Spine", "Chest", "UpperChest",
            "Neck", "Head",
            "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
            "RightShoulder", "RightArm", "RightForeArm", "RightHand",
            "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase",
            "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase",
            # Finger bones (3 per hand, beyond the 4-arm template)
            "LeftHandFinger1", "LeftHandFinger2", "LeftHandFinger3",
            "RightHandFinger1", "RightHandFinger2", "RightHandFinger3",
        }
        assert expected <= bone_names

        # No generic bone_* names should remain
        generic = {n for n in names if n.startswith("bone_")}
        assert len(generic) == 0

    def test_no_bones_no_change(self, tmp_path: Path) -> None:
        from rigging3d.cli import _rename_generic_bones

        # GLB with no bone_ nodes
        nodes = [{"name": "mesh"}, {"name": "root", "children": [0]}]
        glb_json = {
            "asset": {"version": "2.0"},
            "scene": 1,
            "scenes": [{"nodes": [1]}],
            "nodes": nodes,
        }
        json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
        pad = (4 - len(json_bytes) % 4) % 4
        json_bytes += b" " * pad
        bin_data = b"\x00" * 4
        total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
        glb = tmp_path / "empty.glb"
        with open(glb, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
            f.write(json_bytes)
            f.write(struct.pack("<II", len(bin_data), 0x004E4942))
            f.write(bin_data)

        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_already_named_no_change(self, tmp_path: Path) -> None:
        """Bones with non-generic names should not be touched."""
        from rigging3d.cli import _rename_generic_bones

        # Same topology but bones already have semantic names
        parents = [None, 0, 1, 2, 3, 4, 3, 6, 7, 8, 9, 10, 11, 3, 13, 14, 15, 16, 17, 18, 0, 20, 21, 22, 0, 24, 25, 26]
        glb = _build_glb_with_bones(tmp_path, parents, prefix="J_Bip_")
        assert _rename_generic_bones(glb, tmp_path) == 0
