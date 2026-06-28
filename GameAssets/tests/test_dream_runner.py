"""Testes unitários para gameassets.dream.runner (_safe_name, scaffold, run_dream dry-run)."""

from __future__ import annotations

from pathlib import Path

from gameassets.dream.planner import AssetEntry, DreamPlan, Placement, SceneLayout
from gameassets.dream.runner import _safe_name, _scaffold_project, run_dream


def _simple_plan(title: str = "My Cool Game") -> DreamPlan:
    return DreamPlan(
        title=title,
        genre="platformer",
        tone="bright and colorful",
        style_preset="lowpoly",
        assets=[AssetEntry(id="ground", idea="large ground platform", kind="environment", generate_3d=True)],
        scene=SceneLayout(
            ground_size=50,
            spawn_y=5,
            placements=[Placement(asset_id="ground", pos="0 0 0", scale="10 1 10")],
        ),
    )


class TestSafeName:
    def test_lowercases(self) -> None:
        assert _safe_name("Crystal Clouds") == "crystal-clouds"

    def test_spaces_and_underscores_to_dashes(self) -> None:
        assert _safe_name("My_Cool Game") == "my-cool-game"

    def test_caps_length_at_40(self) -> None:
        out = _safe_name("a" * 120)
        assert len(out) == 40
        assert out == "a" * 40

    def test_empty_falls_back(self) -> None:
        assert _safe_name("") == "dream-game"

    def test_deterministic(self) -> None:
        assert _safe_name("Same Title") == _safe_name("Same Title")

    def test_preserves_other_chars(self) -> None:
        # Só espaços e underscores são substituídos; outros caracteres mantêm-se.
        assert _safe_name("Game: Rise!") == "game:-rise!"


class TestScaffoldProject:
    def test_creates_project_files(self, tmp_path: Path) -> None:
        plan = _simple_plan()
        project_dir = tmp_path / "proj"
        batch_dir = tmp_path / "batch"
        src_dir = project_dir / "src"
        public_dir = project_dir / "public"
        batch_dir.mkdir()
        (batch_dir / "main.ts").write_text("// entry\n", encoding="utf-8")
        (batch_dir / "index.html").write_text("<html></html>", encoding="utf-8")

        _scaffold_project(plan, project_dir, batch_dir, src_dir, public_dir, with_sky=True)

        assert (project_dir / "package.json").is_file()
        assert (project_dir / "vite.config.ts").is_file()
        assert (src_dir / "main.ts").is_file()
        assert (project_dir / "index.html").is_file()

    def test_package_json_uses_safe_name(self, tmp_path: Path) -> None:
        plan = _simple_plan(title="Crystal Quest")
        project_dir = tmp_path / "proj"
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()
        _scaffold_project(
            plan,
            project_dir,
            batch_dir,
            project_dir / "src",
            project_dir / "public",
            with_sky=False,
        )
        pkg = (project_dir / "package.json").read_text(encoding="utf-8")
        assert '"name": "crystal-quest"' in pkg
        assert "vibegame" in pkg

    def test_does_not_overwrite_existing_package_json(self, tmp_path: Path) -> None:
        plan = _simple_plan()
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "package.json").write_text('{"existing": true}', encoding="utf-8")
        _scaffold_project(
            plan,
            project_dir,
            tmp_path / "batch",
            project_dir / "src",
            project_dir / "public",
            with_sky=False,
        )
        assert (project_dir / "package.json").read_text(encoding="utf-8") == '{"existing": true}'


class TestRunDreamDryRun:
    def test_dry_run_emits_files_and_scaffolds(self, tmp_path: Path) -> None:
        plan = _simple_plan()
        report = run_dream(plan, tmp_path, with_sky=False, with_audio=True, dry_run=True)

        assert report["dry_run"] is True
        project_dir = Path(report["project_dir"])
        assert project_dir.name == _safe_name(plan.title)
        assert project_dir.is_dir()

        batch_dir = project_dir / "_batch"
        assert (batch_dir / "game.yaml").is_file()
        assert (batch_dir / "manifest.yaml").is_file()
        assert (batch_dir / "dream_plan.json").is_file()
        assert (project_dir / "package.json").is_file()
        assert (project_dir / "vite.config.ts").is_file()
        assert (project_dir / "src" / "main.ts").is_file()
        assert (project_dir / "index.html").is_file()

    def test_dry_run_records_emit_step(self, tmp_path: Path) -> None:
        report = run_dream(_simple_plan(), tmp_path, with_sky=False, dry_run=True)
        step_names = [s["name"] for s in report["steps"]]
        assert "emit batch files" in step_names
        assert "save dream_plan.json" in step_names
        assert "scaffold project (dry-run)" in step_names

    def test_dry_run_no_subprocess_steps(self, tmp_path: Path) -> None:
        report = run_dream(_simple_plan(), tmp_path, with_sky=False, dry_run=True)
        step_names = [s["name"] for s in report["steps"]]
        # Em dry-run não há batch/skymap/handoff reais.
        assert "gameassets batch" not in step_names
        assert "skymap2d generate" not in step_names
        assert "gameassets handoff" not in step_names

    def test_dry_run_creates_public_dir(self, tmp_path: Path) -> None:
        plan = _simple_plan()
        run_dream(plan, tmp_path, with_sky=False, dry_run=True)
        project_dir = tmp_path / _safe_name(plan.title)
        assert (project_dir / "public").is_dir()
