"""Multiview Hi3D: config, prompts, paths, sidecars e invalidação."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from glb_fixtures import write_min_glb

from gameassets import multiview as MV
from gameassets.manifest import ManifestRow, RowRefine, _row_refine_from_dict
from gameassets.profile import GameProfile, RefineProfile, _parse_refine_profile
from gameassets.prompt_builder import build_prompt, build_view_prompt
from gameassets.views import VIEW_ORDER, parse_views

_MIN_YAML = {"title": "A", "genre": "B", "tone": "C", "style_preset": "lowpoly"}
_PRESET = {"prompt_prefix": "stylized game asset", "negative_suffix": "blurry"}


def _profile(**extra: object) -> GameProfile:
    return GameProfile.from_dict({**_MIN_YAML, **extra})


def _row(**kw: object) -> ManifestRow:
    base: dict[str, object] = {
        "id": "crate",
        "idea": "wooden crate",
        "kind": "prop",
        "category": "prop",
        "generate_3d": True,
    }
    base.update(kw)
    return ManifestRow(**base)  # type: ignore[arg-type]


class TestViewsConfig:
    def test_int_expands_canonical_prefix(self) -> None:
        assert parse_views(6) == VIEW_ORDER
        assert parse_views(4) == ("front", "back", "left", "right")
        assert parse_views(1) == ("front",)

    def test_list_defines_order(self) -> None:
        assert parse_views(["front", "top", "back"]) == ("front", "top", "back")

    def test_csv_string(self) -> None:
        assert parse_views("front, back") == ("front", "back")

    @pytest.mark.parametrize("bad", [0, 7, -1])
    def test_int_out_of_range_raises(self, bad: int) -> None:
        with pytest.raises(ValueError, match="entre 1 e 6"):
            parse_views(bad)

    def test_unknown_view_raises(self) -> None:
        with pytest.raises(ValueError, match="desconhecidas"):
            parse_views(["front", "diagonal"])

    def test_duplicate_raises(self) -> None:
        with pytest.raises(ValueError, match="repetida"):
            parse_views(["front", "back", "back"])

    def test_front_required(self) -> None:
        with pytest.raises(ValueError, match="'front' é obrigatória"):
            parse_views(["back", "left"])

    def test_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="bool"):
            parse_views(True)

    def test_profile_carries_views(self) -> None:
        rp = _parse_refine_profile({"views": 6})
        assert rp is not None and rp.views == VIEW_ORDER

    def test_profile_views_list(self) -> None:
        rp = _parse_refine_profile({"views": ["front", "back"]})
        assert rp is not None and rp.views == ("front", "back")

    def test_profile_invalid_views_raises(self) -> None:
        with pytest.raises(ValueError, match=r"refine\.views"):
            _parse_refine_profile({"views": ["front", "ceiling"]})

    def test_default_views_none(self) -> None:
        assert RefineProfile().views is None
        assert _parse_refine_profile({}) == RefineProfile()

    def test_row_refine_views(self) -> None:
        rr = _row_refine_from_dict({"enabled": True, "views": 4}, "crate")
        assert rr is not None and rr.enabled is True and rr.views == ("front", "back", "left", "right")

    def test_row_refine_views_one_opts_out_of_multiview(self) -> None:
        rr = _row_refine_from_dict({"views": 1}, "crate")
        assert rr is not None and rr.views == ("front",)

    def test_row_refine_unknown_key_still_raises(self) -> None:
        with pytest.raises(ValueError, match="chaves desconhecidas"):
            _row_refine_from_dict({"views": 2, "nope": 1}, "crate")


class TestViewPrompts:
    def test_front_keeps_the_normal_prompt(self) -> None:
        prof = _profile()
        row = _row()
        assert build_view_prompt(prof, _PRESET, row, "front") == build_prompt(prof, _PRESET, row, for_3d=False)

    def test_back_drops_front_hint_and_asks_for_back(self) -> None:
        prompt = build_view_prompt(_profile(), _PRESET, _row(), "back")
        assert "strict back view" in prompt
        assert "front view" not in prompt.lower()

    @pytest.mark.parametrize(
        ("view", "needle"),
        [
            ("left", "strict left profile view"),
            ("right", "strict right profile view"),
            ("top", "strict top-down plan view"),
            ("bottom", "strict bottom-up view"),
        ],
    )
    def test_each_extra_view_has_its_instruction(self, view: str, needle: str) -> None:
        assert needle in build_view_prompt(_profile(), _PRESET, _row(), view)

    def test_view_prompt_is_a_short_caption(self) -> None:
        prompt = build_view_prompt(_profile(), _PRESET, _row(), "back")
        assert len(prompt.split()) < 100
        assert "game prop, front view" not in prompt
        assert "Avoid:" not in prompt
        assert "hand-painted" not in prompt

    def test_unknown_view_raises(self) -> None:
        with pytest.raises(ValueError, match="vista desconhecida"):
            build_view_prompt(_profile(), _PRESET, _row(), "diagonal")


class TestViewGates:
    def test_prop_gets_five_extra_views(self) -> None:
        prof = _profile(refine={"views": 6})
        assert MV.views_to_generate(prof, _row()) == ("back", "left", "right", "top", "bottom")

    def test_humanoid_is_skipped(self) -> None:
        prof = _profile(refine={"views": 6})
        row = _row(category="humanoid", kind="character")
        assert MV.views_to_generate(prof, row) == ()

    def test_rigged_is_skipped(self) -> None:
        prof = _profile(refine={"views": 6})
        assert MV.views_to_generate(prof, _row(generate_rig=True)) == ()

    def test_row_disabled_is_skipped(self) -> None:
        prof = _profile(refine={"views": 6})
        assert MV.views_to_generate(prof, _row(refine=RowRefine(enabled=False))) == ()

    def test_row_views_one_opts_out(self) -> None:
        prof = _profile(refine={"views": 6})
        assert MV.views_to_generate(prof, _row(refine=RowRefine(views=("front",)))) == ()

    def test_profile_views_one_keeps_mono(self) -> None:
        prof = _profile(refine={"views": 1})
        assert MV.views_to_generate(prof, _row()) == ()


class TestViewPaths:
    def test_write_path_is_next_to_front(self, tmp_path: Path) -> None:
        mesh = tmp_path / "meshes" / "crate.glb"
        front = tmp_path / "images" / "crate.png"
        prof = _profile(images_subdir="images", meshes_subdir="meshes")
        assert MV.view_image_path(mesh, prof, "back", front=front) == tmp_path / "images" / "crate_back.png"

    def test_resolve_finds_images_root(self, tmp_path: Path) -> None:
        mesh = tmp_path / "meshes" / "crate.glb"
        mesh.parent.mkdir(parents=True)
        img = tmp_path / "images" / "crate_left.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        prof = _profile(images_subdir="images", meshes_subdir="meshes")
        assert MV.resolve_view_image(mesh, prof, "left") == img

    def test_resolve_returns_none_when_missing(self, tmp_path: Path) -> None:
        mesh = tmp_path / "meshes" / "crate.glb"
        assert MV.resolve_view_image(mesh, _profile(), "back") is None


class TestRefineSidecar:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
        mesh = tmp_path / "meshes" / "crate.glb"
        mesh.parent.mkdir(parents=True)
        write_min_glb(mesh)
        inter = mesh.parent / "_intermediate"
        clean = inter / "crate_clean.glb"
        clean.parent.mkdir(parents=True)
        write_min_glb(clean)
        images = tmp_path / "images"
        images.mkdir(parents=True)
        front = images / "crate.png"
        front.write_bytes(b"front")
        back = images / "crate_back.png"
        back.write_bytes(b"back")
        left = images / "crate_left.png"
        left.write_bytes(b"left")
        return mesh, clean, front, back, left

    def _params(self, prof: GameProfile) -> dict[str, object]:
        return MV._expected_refine_params(prof, _row())

    def test_stale_without_sidecar(self, tmp_path: Path) -> None:
        mesh, _clean, _front, _back, _left = self._setup(tmp_path)
        prof = _profile(refine={"views": 3})
        assert MV.refine_stale(prof, _row(), mesh) is True

    def test_fresh_sidecar_is_not_stale(self, tmp_path: Path) -> None:
        mesh, clean, front, back, left = self._setup(tmp_path)
        prof = _profile(refine={"views": 3})
        MV.write_refine_sidecar(
            mesh,
            views=[("front", front), ("back", back), ("left", left)],
            clean_path=clean,
            input_path=clean,
            params=self._params(prof),
        )
        assert MV.refine_stale(prof, _row(), mesh) is False

    def test_changed_view_is_stale(self, tmp_path: Path) -> None:
        mesh, clean, front, back, left = self._setup(tmp_path)
        prof = _profile(refine={"views": 3})
        MV.write_refine_sidecar(
            mesh,
            views=[("front", front), ("back", back), ("left", left)],
            clean_path=clean,
            input_path=clean,
            params=self._params(prof),
        )
        time.sleep(0.002)
        back.write_bytes(b"back-v2")
        assert MV.refine_stale(prof, _row(), mesh) is True

    def test_mono_view_is_never_stale(self, tmp_path: Path) -> None:
        mesh, _clean, _front, _back, _left = self._setup(tmp_path)
        assert MV.refine_stale(_profile(), _row(), mesh) is False

    def test_skip_category_is_never_stale(self, tmp_path: Path) -> None:
        mesh, _clean, _front, _back, _left = self._setup(tmp_path)
        prof = _profile(refine={"views": 6})
        row = _row(category="humanoid", kind="character")
        assert MV.refine_stale(prof, row, mesh) is False


class TestEnsureViewImages:
    def test_builds_refs_and_writes_sidecar(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh = tmp_path / "meshes" / "crate.glb"
        mesh.parent.mkdir(parents=True)
        write_min_glb(mesh)
        front = tmp_path / "images" / "crate.png"
        front.parent.mkdir(parents=True)
        front.write_bytes(b"front")
        prof = _profile(refine={"views": 3})
        captured: list[dict[str, object]] = []

        def _fake_wave(items, **_kw):
            captured.extend(items)
            for item in items:
                Path(item["output"]).parent.mkdir(parents=True, exist_ok=True)
                Path(item["output"]).write_bytes(b"view")
            return [{"id": item["id"], "status": "ok"} for item in items]

        import gameassets.vramd_batch as VB

        monkeypatch.setattr(VB, "run_text2d_wave_or_fallback", _fake_wave)
        res = MV.ensure_multiview_images(
            [{"row": _row(), "mesh_final": mesh, "front": front}],
            profile=prof,
            preset=_PRESET,
            manifest_dir=tmp_path,
            text2d_bin="/bin/text2d",
            width=768,
            height=768,
            steps=8,
            guidance=1.0,
        )
        assert res["generated"] == 2
        assert [i["id"] for i in captured] == ["crate__back", "crate__left"]
        assert all(i["refs"] == [str(front)] for i in captured)
        assert all("as the first image" in str(i["prompt"]) for i in captured)
        sidecar = json.loads(MV.views_sidecar_path(mesh).read_text(encoding="utf-8"))
        assert set(sidecar["views"]) == {"back", "left"}
        assert (tmp_path / "images" / "crate_back.png").is_file()
        assert (tmp_path / "images" / "crate_left.png").is_file()

    def test_reuses_current_views(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mesh = tmp_path / "meshes" / "crate.glb"
        mesh.parent.mkdir(parents=True)
        write_min_glb(mesh)
        front = tmp_path / "images" / "crate.png"
        front.parent.mkdir(parents=True)
        front.write_bytes(b"front")
        prof = _profile(refine={"views": 3})
        captured: list[dict[str, object]] = []

        def _fake_wave(items, **_kw):
            captured.extend(items)
            for item in items:
                Path(item["output"]).write_bytes(b"view")
            return [{"id": item["id"], "status": "ok"} for item in items]

        import gameassets.vramd_batch as VB

        monkeypatch.setattr(VB, "run_text2d_wave_or_fallback", _fake_wave)
        entries = [{"row": _row(), "mesh_final": mesh, "front": front}]
        MV.ensure_multiview_images(
            entries,
            profile=prof,
            preset=_PRESET,
            manifest_dir=tmp_path,
            text2d_bin="/bin/text2d",
            width=768,
            height=768,
            steps=8,
            guidance=1.0,
        )
        captured.clear()
        res = MV.ensure_multiview_images(
            entries,
            profile=prof,
            preset=_PRESET,
            manifest_dir=tmp_path,
            text2d_bin="/bin/text2d",
            width=768,
            height=768,
            steps=8,
            guidance=1.0,
        )
        assert captured == []
        assert res["generated"] == 0 and res["reused"] == 2

    def test_failed_force_view_is_removed_from_the_sidecar(self, tmp_path: Path, monkeypatch) -> None:
        """Vista apagada para regenerar e falhada não pode ficar no sidecar."""
        import gameassets.vramd_batch as VB

        mesh = tmp_path / "meshes" / "crate.glb"
        mesh.parent.mkdir(parents=True)
        write_min_glb(mesh)
        front = tmp_path / "images" / "crate.png"
        front.parent.mkdir(parents=True)
        front.write_bytes(b"front")
        prof = _profile(refine={"views": 3})
        entry = [{"row": _row(), "mesh_final": mesh, "front": front}]

        def ok_wave(items, **_kw):
            for item in items:
                Path(item["output"]).write_bytes(b"view")
            return [{"id": item["id"], "status": "ok"} for item in items]

        def fail_wave(items, **_kw):
            return [{"id": item["id"], "status": "error", "error": "boom"} for item in items]

        kw = dict(
            profile=prof,
            preset=_PRESET,
            manifest_dir=tmp_path,
            text2d_bin="/bin/text2d",
            width=768,
            height=768,
            steps=8,
            guidance=1.0,
        )
        monkeypatch.setattr(VB, "run_text2d_wave_or_fallback", ok_wave)
        MV.ensure_multiview_images(entry, **kw)
        assert set(MV.load_views_sidecar(mesh)["views"]) == {"back", "left"}

        monkeypatch.setattr(VB, "run_text2d_wave_or_fallback", fail_wave)
        res = MV.ensure_multiview_images(entry, force=True, **kw)
        assert res["failed"] == 2
        assert MV.load_views_sidecar(mesh)["views"] == {}


class TestAtomicSidecarWrite:
    def test_tmp_paths_are_unique_and_cleaned(self, tmp_path: Path) -> None:
        """Dois escritores no mesmo sidecar não podem partilhar ``<nome>.tmp``."""
        target = tmp_path / "crate_views.json"
        first = MV._atomic_tmp_path(target)
        second = MV._atomic_tmp_path(target)
        assert first != second
        assert first.parent == target.parent and first.name.endswith(".tmp")

        MV._write_json_atomic(target, {"version": 1})
        assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
