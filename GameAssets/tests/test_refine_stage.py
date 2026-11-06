"""Refinamento Hi3D no GameAssets (config, descoberta da imagem e stage)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gameassets import pipeline as P
from gameassets.paths import _intermediate_dir, _refined_existing, _refined_path
from gameassets.profile import GameProfile, RefineProfile, _parse_refine_profile

_MIN_YAML = {"title": "A", "genre": "B", "tone": "C", "style_preset": "lowpoly"}


def _profile(**extra: object) -> GameProfile:
    return GameProfile.from_dict({**_MIN_YAML, **extra})


class TestRefineProfileParsing:
    def test_absent_means_defaults_on(self) -> None:
        assert _profile().refine is None

    def test_none_is_none(self) -> None:
        assert _parse_refine_profile(None) is None

    def test_true_enables(self) -> None:
        rp = _parse_refine_profile(True)
        assert rp is not None and rp.enabled is True

    def test_false_disables(self) -> None:
        rp = _parse_refine_profile(False)
        assert rp is not None and rp.enabled is False

    def test_empty_dict_uses_defaults(self) -> None:
        rp = _parse_refine_profile({})
        assert rp == RefineProfile()

    def test_defaults_are_on(self) -> None:
        rp = RefineProfile()
        assert rp.enabled is True
        assert rp.resolution == "auto"
        assert rp.steps == 25
        assert rp.max_faces_scale == 4.0
        assert rp.model is None

    @pytest.mark.parametrize("res", ["auto", "512", "1024", "1536", "AUTO", " 1024 "])
    def test_valid_resolutions(self, res: str) -> None:
        rp = _parse_refine_profile({"resolution": res})
        assert rp is not None
        assert rp.resolution == res.strip().lower()

    @pytest.mark.parametrize("res", ["256", "2048", "high", ""])
    def test_invalid_resolution_raises(self, res: str) -> None:
        with pytest.raises(ValueError, match=r"refine\.resolution"):
            _parse_refine_profile({"resolution": res})

    def test_steps_override(self) -> None:
        rp = _parse_refine_profile({"steps": 10})
        assert rp is not None and rp.steps == 10

    def test_invalid_steps_raises(self) -> None:
        with pytest.raises(ValueError, match=r"steps"):
            _parse_refine_profile({"steps": "muitos"})

    def test_zero_steps_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            _parse_refine_profile({"steps": 0})

    def test_negative_scale_raises(self) -> None:
        with pytest.raises(ValueError, match="max_faces_scale"):
            _parse_refine_profile({"max_faces_scale": -1})

    def test_scale_zero_is_allowed(self) -> None:
        rp = _parse_refine_profile({"max_faces_scale": 0})
        assert rp is not None and rp.max_faces_scale == 0

    def test_seed_and_model(self) -> None:
        rp = _parse_refine_profile({"seed": 7, "model": "/models/hi3d"})
        assert rp is not None
        assert rp.seed == 7
        assert rp.model == "/models/hi3d"

    def test_blank_model_is_none(self) -> None:
        rp = _parse_refine_profile({"model": "   "})
        assert rp is not None and rp.model is None

    def test_remove_background(self) -> None:
        rp = _parse_refine_profile({"remove_background": True})
        assert rp is not None and rp.remove_background is True

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="mapa YAML"):
            _parse_refine_profile(["nope"])

    def test_profile_carries_refine(self) -> None:
        prof = _profile(refine={"enabled": False})
        assert prof.refine is not None and prof.refine.enabled is False


class TestRefinedPaths:
    def test_refined_path_is_intermediate(self, tmp_path: Path) -> None:
        mesh = tmp_path / "meshes" / "goblin.glb"
        assert _refined_path(mesh) == _intermediate_dir(mesh) / "goblin_refined.glb"

    def test_refined_existing_none_when_absent(self, tmp_path: Path) -> None:
        mesh = tmp_path / "meshes" / "goblin.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        assert _refined_existing(mesh) is None


class TestReferenceImageDiscovery:
    def _mesh(self, tmp_path: Path) -> Path:
        mesh = tmp_path / "meshes" / "goblin.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        return mesh

    def test_sidecar_text2d(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = _intermediate_dir(mesh) / "goblin_text2d.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_sidecar_input(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = _intermediate_dir(mesh) / "goblin_input.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_split_layout_images_subdir(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = tmp_path / "images" / "goblin.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_split_layout_nested_subdirs(self, tmp_path: Path) -> None:
        # Manifest por pack: meshes_subdir=meshes/characters, images_subdir=images/characters.
        mesh = tmp_path / "meshes" / "characters" / "hero.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        img = tmp_path / "images" / "characters" / "hero.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        prof = _profile(meshes_subdir="meshes/characters", images_subdir="images/characters")
        assert P._reference_image_for(mesh, prof) == img

    def test_flat_layout_next_to_mesh(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = mesh.parent / "goblin.png"
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_jpg_fallback(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = tmp_path / "images" / "goblin.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"jpg")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert P._reference_image_for(self._mesh(tmp_path), _profile()) is None

    def test_empty_file_is_ignored(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = tmp_path / "images" / "goblin.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"")
        assert P._reference_image_for(mesh, _profile()) is None

    def test_prefixed_sidecar_fallback(self, tmp_path: Path) -> None:
        mesh = self._mesh(tmp_path)
        img = _intermediate_dir(mesh) / "goblin_v2_text2d.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img

    def test_shape_input_sidecar_fallback(self, tmp_path: Path) -> None:
        # ``text3d generate --save-reference-image`` escreve
        # ``<stem-do-output>_input.png`` — com shape em _intermediate fica
        # ``<base>_shape_input.png``.
        mesh = self._mesh(tmp_path)
        img = _intermediate_dir(mesh) / "goblin_shape_input.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert P._reference_image_for(mesh, _profile()) == img


class _FakeRun:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestRunRefineStage:
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
        mesh = tmp_path / "meshes" / "goblin.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        clean = _intermediate_dir(mesh) / "goblin_clean.glb"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"glb")
        img = tmp_path / "images" / "goblin.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        return mesh, clean, img

    def _call(self, mesh: Path, clean: Path, profile: GameProfile, **kw: object):
        return P._run_refine_stage(
            clean,
            mesh,
            text3d_bin="/bin/text3d",
            profile=profile,
            child_env={},
            manifest_dir=mesh.parent.parent,
            force=bool(kw.get("force", False)),
            row=None,
        )

    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        mesh, clean, _img = self._setup(tmp_path, pytest.MonkeyPatch())
        prof = _profile(refine={"enabled": False})
        assert self._call(mesh, clean, prof) is None

    def test_missing_hi3d_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: None)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        assert self._call(mesh, clean, _profile()) is None

    def test_missing_image_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, img = self._setup(tmp_path, monkeypatch)
        img.unlink()
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        assert self._call(mesh, clean, _profile()) is None

    def test_unavailable_exit_3_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", lambda *a, **k: _FakeRun(3, stderr="sem VRAM"))
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        assert self._call(mesh, clean, _profile()) is None

    def test_failure_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", lambda *a, **k: _FakeRun(1, stderr="boom"))
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        assert self._call(mesh, clean, _profile()) is None

    def test_success_writes_and_returns_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        out = _refined_path(mesh)

        def _fake_run(argv: list[str], **_kw: object) -> _FakeRun:
            out.write_bytes(b"glb-refined")
            return _FakeRun(0)

        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", _fake_run)
        monkeypatch.setattr(P, "_count_faces_glb", lambda _p: 1234)
        monkeypatch.setattr(P, "_glb_has_geometry", lambda _p: True)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        assert self._call(mesh, clean, _profile()) == out

    def test_argv_contains_expected_flags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, img = self._setup(tmp_path, monkeypatch)
        out = _refined_path(mesh)
        captured: list[list[str]] = []

        def _fake_run(argv: list[str], **_kw: object) -> _FakeRun:
            captured.append(argv)
            out.write_bytes(b"glb")
            return _FakeRun(0)

        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", _fake_run)
        monkeypatch.setattr(P, "_count_faces_glb", lambda _p: 10)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        prof = _profile(refine={"steps": 7, "resolution": "1024", "max_faces_scale": 2.0, "seed": 5})
        self._call(mesh, clean, prof)
        argv = captured[0]
        assert argv[:3] == ["/bin/text3d", "refine", str(clean)]
        assert "-i" in argv and str(img) in argv
        assert "--steps" in argv and argv[argv.index("--steps") + 1] == "7"
        assert "--resolution" in argv and argv[argv.index("--resolution") + 1] == "1024"
        assert "--seed" in argv and argv[argv.index("--seed") + 1] == "5"
        assert "--max-faces" in argv

    def test_existing_refined_is_reused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        out = _refined_path(mesh)
        out.write_bytes(b"glb")
        called: list[int] = []
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", lambda *a, **k: called.append(1))
        monkeypatch.setattr(P, "_glb_has_geometry", lambda _p: True)
        assert self._call(mesh, clean, _profile(), force=False) == out
        assert called == []

    def test_force_reruns_refine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh, clean, _img = self._setup(tmp_path, monkeypatch)
        out = _refined_path(mesh)
        out.write_bytes(b"glb")
        called: list[int] = []
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", lambda *a, **k: (called.append(1), _FakeRun(0))[1])
        monkeypatch.setattr(P, "_count_faces_glb", lambda _p: 10)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        self._call(mesh, clean, _profile(), force=True)
        assert called == [1]


class TestEnsureCleanReturnsRefined:
    def test_clean_passthrough_when_refine_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh = tmp_path / "meshes" / "goblin.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        clean = _intermediate_dir(mesh) / "goblin_clean.glb"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"glb")
        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: None)
        from gameassets import paths as paths_mod

        monkeypatch.setattr(paths_mod, "_glb_has_geometry", lambda _p: True)
        monkeypatch.setattr(P, "_glb_has_geometry", lambda _p: True)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        out = P.ensure_clean_for_paint(
            mesh,
            text3d_bin="/bin/text3d",
            profile=_profile(),
            child_env={},
            manifest_dir=tmp_path,
        )
        assert out == clean

    def test_returns_refined_when_stage_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh = tmp_path / "meshes" / "goblin.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        clean = _intermediate_dir(mesh) / "goblin_clean.glb"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"glb")
        img = tmp_path / "images" / "goblin.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        refined = _refined_path(mesh)

        def _fake_run(argv: list[str], **_kw: object) -> _FakeRun:
            refined.write_bytes(b"glb")
            return _FakeRun(0)

        monkeypatch.setattr(P, "_bin_or_none", lambda *_a: "/bin/hi3d")
        monkeypatch.setattr(P, "run_cmd", _fake_run)
        monkeypatch.setattr(P, "_count_faces_glb", lambda _p: 10)
        from gameassets import paths as paths_mod

        monkeypatch.setattr(paths_mod, "_glb_has_geometry", lambda _p: True)
        monkeypatch.setattr(P, "_glb_has_geometry", lambda _p: True)
        monkeypatch.setattr(P.console, "print", lambda *a, **k: None)
        out = P.ensure_clean_for_paint(
            mesh,
            text3d_bin="/bin/text3d",
            profile=_profile(),
            child_env={},
            manifest_dir=tmp_path,
        )
        assert out == refined


class TestText3dGenerateSavesReference:
    def test_generate_argv_includes_save_reference_image(self, tmp_path: Path) -> None:
        from gameassets.pipeline import _text3d_argv

        prof = _profile()
        argv = _text3d_argv("/bin/text3d", prof, tmp_path / "i.png", tmp_path / "m.glb")
        assert "--save-reference-image" in argv

    def test_generate_argv_without_text3d_block_also_saves(self, tmp_path: Path) -> None:
        from gameassets.pipeline import _text3d_argv

        prof = _profile(text3d=None)
        argv = _text3d_argv("/bin/text3d", prof, tmp_path / "i.png", tmp_path / "m.glb")
        assert "--save-reference-image" in argv


class TestCheckGlbFailures:
    def test_extracts_failures_from_json(self) -> None:
        out = '{\n  "ok": false,\n  "failures": ["mesh_totals.v_per_tri: 1.78 > max (1.6)"]\n}\nlinha extra'
        assert P._check_glb_failures(out) == ["mesh_totals.v_per_tri: 1.78 > max (1.6)"]

    def test_non_json_returns_empty(self) -> None:
        assert P._check_glb_failures("Traceback: boom") == []
