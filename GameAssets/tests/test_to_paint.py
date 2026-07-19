"""Testes do stage ``_to_paint`` (orçamento pré-paint)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from gameassets.paths import _to_paint_path
from gameassets.pipeline import _resolve_paint_texture_size, _resolve_to_paint_faces, ensure_to_paint_for_paint
from gameassets.profile import GameProfile, Paint3DProfile


class TestResolveToPaintFaces:
    def test_default_medium_2048(self) -> None:
        from gamedev_shared.paint_budget import PAINT_FACES_MAX

        p = GameProfile(title="t", genre="g", tone="t", style_preset="s", output_dir=".")
        assert _resolve_paint_texture_size(p) == 2048
        assert _resolve_to_paint_faces(p) == PAINT_FACES_MAX

    def test_override_to_paint_faces(self) -> None:
        p = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="s",
            output_dir=".",
            paint3d=Paint3DProfile(texture_size=2048, to_paint_faces=40_000),
        )
        assert _resolve_to_paint_faces(p) == 40_000

    def test_texture_1024_band(self) -> None:
        p = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="s",
            output_dir=".",
            paint3d=Paint3DProfile(texture_size=1024),
        )
        assert _resolve_to_paint_faces(p) == 57_671


class TestEnsureToPaint:
    def test_skip_when_clean_small(self, tmp_path: Path) -> None:
        mesh_final = tmp_path / "meshes" / "chapel.glb"
        mesh_final.parent.mkdir(parents=True)
        clean = tmp_path / "meshes" / "_intermediate" / "chapel_clean.glb"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"glTF")
        profile = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="s",
            output_dir=str(tmp_path / "meshes"),
            paint3d=Paint3DProfile(texture_size=2048, to_paint_faces=80_000),
        )
        with (
            patch("gameassets.pipeline.ensure_clean_for_paint", return_value=clean),
            patch("gameassets.pipeline._count_faces_glb", return_value=50_000),
            patch("gameassets.pipeline.run_cmd") as run_cmd,
        ):
            out = ensure_to_paint_for_paint(
                mesh_final,
                text3d_bin="text3d",
                profile=profile,
                child_env={},
                manifest_dir=tmp_path,
            )
        assert out == clean
        run_cmd.assert_not_called()

    def test_simplify_when_clean_huge(self, tmp_path: Path) -> None:
        mesh_final = tmp_path / "meshes" / "chapel.glb"
        mesh_final.parent.mkdir(parents=True)
        clean = tmp_path / "meshes" / "_intermediate" / "chapel_clean.glb"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"glTF")
        to_paint = _to_paint_path(mesh_final)
        profile = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="s",
            output_dir=str(tmp_path / "meshes"),
            paint3d=Paint3DProfile(texture_size=2048),
        )

        def _fake_run(argv, **_kw):
            # text3d simplify … -o to_paint
            Path(argv[argv.index("-o") + 1]).write_bytes(b"glTF")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("gameassets.pipeline.ensure_clean_for_paint", return_value=clean),
            patch(
                "gameassets.pipeline._count_faces_glb",
                side_effect=lambda p: 2_000_000 if "clean" in p.name else 79_000,
            ),
            patch("gameassets.pipeline.run_cmd", side_effect=_fake_run) as run_cmd,
        ):
            out = ensure_to_paint_for_paint(
                mesh_final,
                text3d_bin="text3d",
                profile=profile,
                child_env={},
                manifest_dir=tmp_path,
                force=True,
            )
        assert out == to_paint
        assert to_paint.is_file()
        assert run_cmd.call_args[0][0][:2] == ["text3d", "simplify"]
        assert "--target-faces" in run_cmd.call_args[0][0]
