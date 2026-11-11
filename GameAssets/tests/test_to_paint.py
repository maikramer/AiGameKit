"""Testes do stage ``_to_paint`` (orçamento pré-paint)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import aigamekit_shared.paint_budget as _pb
from gameassets.paths import _to_paint_path
from gameassets.pipeline import _resolve_paint_texture_size, _resolve_to_paint_faces, ensure_to_paint_for_paint
from gameassets.profile import GameProfile, Paint3DProfile


class TestResolveToPaintFaces:
    def test_default_medium_2048(self, monkeypatch) -> None:
        from aigamekit_shared.paint_budget import PAINT_FACES_MAX

        # GPU grande explícita: numa 6 GiB o tecto por VRAM reduz o alvo.
        monkeypatch.setattr(_pb, "_VRAM_TOTAL_MIB", 16384)
        p = GameProfile(title="t", genre="g", tone="t", style_preset="s", output_dir=".")
        assert _resolve_paint_texture_size(p) == 2048
        assert _resolve_to_paint_faces(p) == PAINT_FACES_MAX

    def test_vram_cap_reduces_target(self, monkeypatch) -> None:
        # 4050 6 GB (6053 MiB): âncora medida 6 GiB → ~282k (era ~236k na
        # interpolação linear 4→8 GiB).
        monkeypatch.setattr(_pb, "_VRAM_TOTAL_MIB", 6053)
        p = GameProfile(title="t", genre="g", tone="t", style_preset="s", output_dir=".")
        faces = _resolve_to_paint_faces(p)
        assert 275_000 <= faces <= 290_000

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
        assert _resolve_to_paint_faces(p) == 115_343

    def test_target_faces_override_used_in_simplify(self, tmp_path: Path) -> None:
        """Recovery do paint: degradação 0.6x entra no --target-faces do simplify."""
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
            Path(argv[argv.index("-o") + 1]).write_bytes(b"glTF")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("gameassets.pipeline.ensure_clean_for_paint", return_value=clean),
            patch(
                "gameassets.pipeline._count_faces_glb",
                side_effect=lambda p: 2_000_000 if "clean" in p.name else 100_000,
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
                target_faces=192_000,
            )
        assert out == to_paint
        simplify_argv = next(c.args[0] for c in run_cmd.call_args_list if c.args[0][:2] == ["text3d", "simplify"])
        assert simplify_argv[simplify_argv.index("--target-faces") + 1] == "192000"


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
        # Caminho ``_clean``: só simplify. O re-fix (topology-fix) com
        # --morph-close é exclusivo do caminho ``_refined``; repeti-lo aqui
        # parte a malha sã em ilhas (ver `pipeline.ensure_to_paint_for_paint`).
        cmds = [c.args[0][:2] for c in run_cmd.call_args_list]
        assert ["text3d", "simplify"] in cmds
        assert ["text3d", "topology-fix"] not in cmds
        simplify_argv = next(c.args[0] for c in run_cmd.call_args_list if c.args[0][:2] == ["text3d", "simplify"])
        assert "--target-faces" in simplify_argv
        assert "--no-repair" in simplify_argv

    def test_refined_runs_topology_fix_before_simplify(self, tmp_path: Path) -> None:
        """O caminho ``_refined`` refixa (morph-close > 0) antes de decimar."""
        mesh_final = tmp_path / "meshes" / "chapel.glb"
        mesh_final.parent.mkdir(parents=True)
        refined = tmp_path / "meshes" / "_intermediate" / "chapel_refined.glb"
        refined.parent.mkdir(parents=True)
        refined.write_bytes(b"glTF")
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
            Path(argv[argv.index("-o") + 1]).write_bytes(b"glTF")
            return MagicMock(returncode=0, stdout="", stderr="")

        def _faces(path: Path) -> int:
            return 2_000_000 if "refined" in path.name or "refixsrc" in path.name else 79_000

        with (
            patch("gameassets.pipeline.ensure_clean_for_paint", return_value=refined),
            patch("gameassets.pipeline._count_faces_glb", side_effect=_faces),
            patch("gameassets.pipeline._glb_has_geometry", return_value=True),
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
        cmds = [c.args[0][:2] for c in run_cmd.call_args_list]
        assert ["text3d", "topology-fix"] in cmds
        assert ["text3d", "simplify"] in cmds
        assert cmds.index(["text3d", "topology-fix"]) < cmds.index(["text3d", "simplify"])
        refix_argv = next(c.args[0] for c in run_cmd.call_args_list if c.args[0][:2] == ["text3d", "topology-fix"])
        assert "--morph-close" in refix_argv
        assert float(refix_argv[refix_argv.index("--morph-close") + 1]) > 0
