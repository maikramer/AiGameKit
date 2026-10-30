"""ctypes bridge: struct layout mirrors the C header; options and clips behave."""

from __future__ import annotations

import ctypes as C
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from motion3d.sprong import GenerateOptions, Model, SprongError, load_library
from motion3d.sprong.bindings import SmBlock, SmModel, SmMotion, SmOptions, SmText, _array, last_error
from motion3d.sprong.runtime import VENDOR_DIR, ensure_runtime


def header_text() -> str:
    return (VENDOR_DIR / "include" / "sprongmotion.h").read_text(encoding="utf-8")


def c_fields(name: str, *, typedef: bool) -> list[str]:
    text = header_text()
    pattern = rf"typedef struct {name} \{{(.*?)\}} {name};" if typedef else rf"struct {name} \{{(.*?)\}};"
    match = re.search(pattern, text, flags=re.S)
    assert match is not None, f"{name} not found in the vendored header"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    fields: list[str] = []
    for raw in body.split(";"):
        line = raw.split("//")[0].strip()
        if not line:
            continue
        function_pointer = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", line)
        if function_pointer:
            fields.append(function_pointer.group(1))
            continue
        identifier = re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$", line)
        if identifier:
            fields.append(identifier[-1])
    return fields


class TestStructLayout:
    @pytest.mark.parametrize(
        ("structure", "name", "typedef"),
        [
            (SmModel, "sm_model", True),
            (SmMotion, "sm_motion", False),
            (SmText, "sm_text", False),
            (SmOptions, "sm_options", True),
            (SmBlock, "sm_block", True),
        ],
    )
    def test_field_names_and_order(self, structure: type, name: str, typedef: bool) -> None:
        assert [field[0] for field in structure._fields_] == c_fields(name, typedef=typedef)

    def test_model_exposes_skeleton_metadata(self) -> None:
        names = [field[0] for field in SmModel._fields_]
        for key in ("joint_count", "joint_names", "joint_parents", "rest_offsets", "fps"):
            assert key in names

    def test_motion_exposes_arrays(self) -> None:
        names = [field[0] for field in SmMotion._fields_]
        for key in ("joints", "rotations", "offsets", "root", "contacts", "codes", "features"):
            assert key in names

    def test_options_cover_every_runtime_knob(self) -> None:
        names = [field[0] for field in SmOptions._fields_]
        for key in ("seconds", "seed", "steps", "residual_layers", "foot_lock", "in_place", "loop", "top_k"):
            assert key in names


class TestGenerateOptions:
    def test_only_non_none_fields_are_applied(self) -> None:
        options = SmOptions()
        options.steps = 10
        GenerateOptions(steps=4, loop=True).apply_to(options)
        assert options.steps == 4
        assert options.loop is True
        assert options.residual_layers == 0  # untouched struct default

    def test_false_is_applied(self) -> None:
        options = SmOptions()
        options.foot_lock = True
        GenerateOptions(foot_lock=False).apply_to(options)
        assert options.foot_lock is False

    def test_describe_lists_only_set_fields(self) -> None:
        described = GenerateOptions(seconds=2.0, seed=7).describe()
        assert described == {"seconds": 2.0, "seed": 7}

    def test_empty_describe(self) -> None:
        assert GenerateOptions().describe() == {}


class TestClip:
    def make(self, frames: int = 6) -> object:
        from motion3d.sprong import Clip

        return Clip(
            prompt="walk",
            joints=np.zeros((frames, 22, 3), dtype=np.float32),
            rotations=np.zeros((frames, 22, 4), dtype=np.float32),
            offsets=np.zeros((22, 3), dtype=np.float32),
            root=np.zeros((frames, 3), dtype=np.float32),
            speed=np.zeros(frames, dtype=np.float32),
            contacts=np.zeros((frames, 4), dtype=np.uint8),
            fps=20,
            seed=5,
            model_id="m",
            token_count=3,
            path_error=-1.0,
        )

    def test_properties(self) -> None:
        clip = self.make(10)
        assert clip.frame_count == 10
        assert clip.joint_count == 22
        assert clip.seconds == pytest.approx(0.5)

    def test_joints64_is_float64(self) -> None:
        assert self.make().joints64().dtype == np.float64

    def test_zero_fps_seconds_is_zero(self) -> None:
        from motion3d.sprong import Clip

        clip = Clip(
            prompt="x",
            joints=np.zeros((0, 22, 3), np.float32),
            rotations=np.zeros((0, 22, 4), np.float32),
            offsets=np.zeros((22, 3), np.float32),
            root=np.zeros((0, 3), np.float32),
            speed=np.zeros((0,), np.float32),
            contacts=np.zeros((0, 4), np.uint8),
            fps=0,
            seed=0,
            model_id="m",
            token_count=0,
            path_error=-1.0,
        )
        assert clip.seconds == 0.0


class TestArrayHelper:
    def test_copies_the_buffer(self) -> None:
        source = (C.c_float * 6)(1, 2, 3, 4, 5, 6)
        view = _array(source, (2, 3), np.float32)
        source[0] = 99
        assert view[0, 0] == pytest.approx(1.0)

    def test_null_pointer_is_zeroed(self) -> None:
        view = _array(None, (2, 3), np.float32)
        assert view.shape == (2, 3) and not view.any()

    def test_empty_shape(self) -> None:
        assert _array(None, (0, 6), np.int16).shape == (0, 6)


class TestErrors:
    def test_last_error_reads_the_c_string(self) -> None:
        class FakeLib:
            def sm_get_error(self) -> bytes:
                return b"seconds = 12.5 is out of range 0.5 .. 10"

        assert "out of range" in last_error(FakeLib())

    def test_last_error_empty(self) -> None:
        class FakeLib:
            def sm_get_error(self) -> bytes:
                return b""

        assert last_error(FakeLib()) == ""

    def test_model_without_file_raises_before_touching_c(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Model(tmp_path / "missing.gguf", library=object())

    def test_sprong_error_is_a_runtime_error(self) -> None:
        assert issubclass(SprongError, RuntimeError)


@pytest.mark.skipif(shutil.which("cc") is None, reason="sem compilador C")
class TestRealLibrary:
    def test_load_and_version(self) -> None:
        lib = load_library(ensure_runtime())
        assert (lib.sm_get_version() or b"").decode() == "1.1.0"

    def test_build_info_mentions_the_build(self) -> None:
        lib = load_library()
        assert "1.1.0" in (lib.sm_get_build_info() or b"").decode()


class TestRealModel:
    def test_metadata_matches_the_pinned_skeleton(self, sprong_model: Path) -> None:
        from motion3d.skeleton import REFERENCE_OFFSETS, check_skeleton

        with Model(sprong_model) as model:
            assert model.joint_count == 22
            assert model.fps == 20
            check_skeleton(model.joint_names, model.joint_parents)
            assert np.allclose(model.rest_offsets, REFERENCE_OFFSETS, atol=5e-5)

    def test_generate_returns_a_valid_clip(self, sprong_model: Path) -> None:
        with Model(sprong_model) as model:
            clip = model.generate("a person walks forward", seconds=1.0, seed=7)
        assert clip.frame_count == 20
        assert clip.fps == 20
        assert clip.joints.shape == (20, 22, 3)
        assert np.isfinite(clip.joints).all()
        assert clip.offsets.shape == (22, 3)
        assert clip.codes.shape[1] == model.code_layers

    def test_same_seed_is_deterministic(self, sprong_model: Path) -> None:
        with Model(sprong_model) as model:
            first = model.generate("a person walks forward", seconds=1.0, seed=11)
            second = model.generate("a person walks forward", seconds=1.0, seed=11)
        assert np.array_equal(first.joints, second.joints)

    def test_empty_prompt_rejected(self, sprong_model: Path) -> None:
        with Model(sprong_model) as model, pytest.raises(ValueError, match="prompt"):
            model.generate("   ", seconds=1.0)

    def test_out_of_range_seconds_reports_the_field(self, sprong_model: Path) -> None:
        with Model(sprong_model) as model, pytest.raises(SprongError, match="seconds"):
            model.generate("a person walks forward", seconds=99.0)

    def test_bvh_export(self, sprong_model: Path, tmp_path: Path) -> None:
        target = tmp_path / "walk.bvh"
        with Model(sprong_model) as model:
            model.generate("a person walks forward", seconds=1.0, seed=1, bvh_path=target)
        text = target.read_text(encoding="utf-8")
        assert text.startswith("HIERARCHY")
        assert "JOINT l_wrist" in text

    def test_timeline(self, sprong_model: Path) -> None:
        with Model(sprong_model) as model:
            clip = model.generate_timeline([("a person walks forward", 1.0), ("a person turns around", 1.0)], seed=2)
        assert clip.frame_count >= 38  # 2 x 1 s @ 20 fps, seams pinned
        assert "|" in clip.prompt
