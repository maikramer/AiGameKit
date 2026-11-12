"""Worker remoto e delegação vramd do rigging3d (sem inferência real)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from rigging3d import skintokens_runner, worker_serve_adapter
from rigging3d.cli import cli


class TestAdapter:
    def test_load_runs_from_checkpoint_root(self, monkeypatch, tmp_path: Path) -> None:
        """O config ckpt resolve o VAE por caminho relativo ao cwd."""
        import os

        root = tmp_path / "skintokens"
        root.mkdir()
        seen: dict[str, object] = {}

        def fake_load(ckpt: str, device: str = "cuda") -> tuple[object, object, object]:
            seen["cwd"] = os.getcwd()
            seen["ckpt"] = ckpt
            return object(), object(), object()

        monkeypatch.setattr(skintokens_runner, "ensure_checkpoints", lambda _home=None: root)
        monkeypatch.setattr(skintokens_runner, "_load_model", fake_load)

        state = worker_serve_adapter.Adapter().load()

        assert seen["cwd"] == str(root.resolve())
        assert seen["ckpt"] == str(root / skintokens_runner.DEFAULT_TOKENRIG_CKPT)
        assert state["root"] == str(root)
        assert os.getcwd() != str(root.resolve())

    def test_generate_maps_request_to_runner(self, monkeypatch, tmp_path: Path) -> None:
        calls: dict[str, object] = {}

        def fake_run(input_path: str, output_path: str, **kwargs: object) -> None:
            calls.update(input=input_path, output=output_path, **kwargs)
            Path(output_path).write_bytes(b"glb")

        monkeypatch.setattr(skintokens_runner, "run_rig_inprocess", fake_run)
        adapter = worker_serve_adapter.Adapter()
        response = adapter.generate(
            {},
            {
                "input": str(tmp_path / "in.glb"),
                "output": str(tmp_path / "out.glb"),
                "seed": 7,
                "groups_per_vertex": 6,
                "num_beams": 3,
                "use_transfer": False,
            },
        )
        assert response["status"] == "ok"
        assert response["output"] == str(tmp_path / "out.glb")
        assert calls["input"] == str(tmp_path / "in.glb")
        assert calls["seed"] == 7
        assert calls["group_per_vertex"] == 6
        assert calls["num_beams"] == 3
        assert calls["use_transfer"] is False

    def test_generate_requires_input_and_output(self) -> None:
        adapter = worker_serve_adapter.Adapter()
        response = adapter.generate({}, {"input": "x.glb"})
        assert response["status"] == "error"
        assert "output" in response["error"]

    def test_generate_honours_abort(self, monkeypatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("não devia correr")

        monkeypatch.setattr(skintokens_runner, "run_rig_inprocess", boom)
        adapter = worker_serve_adapter.Adapter()
        response = adapter.generate(
            {},
            {
                "input": "in.glb",
                "output": "out.glb",
                "_abort": lambda: True,
            },
        )
        assert response["status"] == "error"
        assert response.get("error_code") == "CANCELLED"

    def test_unload_clears_cached_model(self, monkeypatch) -> None:
        monkeypatch.setattr(skintokens_runner, "_model", object())
        monkeypatch.setattr(skintokens_runner, "_loaded_ckpt", "x")
        worker_serve_adapter.Adapter().unload({})
        assert skintokens_runner._model is None
        assert skintokens_runner._loaded_ckpt is None


class TestCliDelegation:
    def _post_fixes(self, monkeypatch) -> None:
        import rigging3d.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_rename_generic_bones", lambda _path: 0)
        monkeypatch.setattr(cli_mod, "_validate_and_fix_origin", lambda _path: None)
        monkeypatch.setattr(cli_mod, "_fix_bone_orientation_or_warn", lambda _path: None)

    def test_pipeline_delegates_to_vramd(self, monkeypatch, tmp_path: Path) -> None:
        import rigging3d.cli as cli_mod

        source = tmp_path / "in.glb"
        source.write_bytes(b"glb")
        output = tmp_path / "out.glb"
        seen: dict[str, object] = {}
        # O --gpu escreve VRAMD_GPU_MODE; o monkeypatch repõe no teardown.
        monkeypatch.setenv("VRAMD_GPU_MODE", "auto")

        def fake_delegate(tool: str, *, payload: dict, **kwargs: object) -> bool:
            seen.update(tool=tool, payload=payload, kwargs=kwargs)
            Path(payload["output"]).write_bytes(b"rigged")
            return True

        def in_process(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("o caminho in-process não devia correr")

        monkeypatch.setattr(cli_mod, "delegate_or_prepare", fake_delegate)
        monkeypatch.setattr(skintokens_runner, "run_rig_inprocess", in_process)
        self._post_fixes(monkeypatch)

        result = CliRunner().invoke(
            cli,
            ["pipeline", "--input", str(source), "--output", str(output), "--gpu", "remote"],
        )

        assert result.exit_code == 0, result.output
        assert seen["tool"] == "rigging3d"
        assert seen["payload"]["input"] == str(source)
        assert seen["kwargs"]["enabled"] is True
        assert output.read_bytes() == b"rigged"

    def test_pipeline_no_vramd_runs_in_process(self, monkeypatch, tmp_path: Path) -> None:
        import rigging3d.cli as cli_mod

        source = tmp_path / "in.glb"
        source.write_bytes(b"glb")
        output = tmp_path / "out.glb"
        ran: list[bool] = []

        def fake_delegate(*_args: object, **kwargs: object) -> bool:
            ran.append(bool(kwargs.get("enabled")))
            return False

        def in_process(_input: str, out: str, **_kwargs: object) -> None:
            Path(out).write_bytes(b"local")

        monkeypatch.setattr(cli_mod, "delegate_or_prepare", fake_delegate)
        monkeypatch.setattr(skintokens_runner, "run_rig_inprocess", in_process)
        self._post_fixes(monkeypatch)

        result = CliRunner().invoke(
            cli,
            ["pipeline", "--input", str(source), "--output", str(output), "--no-vramd"],
        )

        assert result.exit_code == 0, result.output
        assert ran == [False]
        assert output.read_bytes() == b"local"


def test_serve_worker_command_is_registered() -> None:
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--ums-worker" in result.output
