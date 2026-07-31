"""Testes do prólogo/epílogo canónico dos adapters 2D (WorkerAdapter).

``begin_generate`` / ``finish_response`` padronizam o generate() dos
adapters de worker subprocesso (text2d/text2icon/texture2d/skymap2d/
text2sound) — validação, abort pre-check, hooks e resposta de sucesso.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class _ProbeAdapter(WorkerAdapter):
    name = "probe"

    def load(self, **kwargs: Any) -> Any:
        return object()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=20)
        if error:
            return error
        return {"status": "ok", "steps": steps, "has_abort": should_abort is not None, "has_step": on_step is not None}

    def unload(self, model: Any) -> None:
        pass


class TestBeginGenerate:
    def test_missing_required_returns_error(self) -> None:
        error, _steps, *_ = _ProbeAdapter().begin_generate({}, default_steps=20)
        assert error is not None
        assert error["status"] == "error"
        assert "prompt" in error["error"] and "output" in error["error"]

    def test_missing_required_custom_fields(self) -> None:
        error, *_ = _ProbeAdapter().begin_generate(
            {"mesh_path": "m"}, default_steps=20, required=("mesh_path", "output")
        )
        assert error is not None
        assert "output" in error["error"]

    def test_abort_before_generate(self) -> None:
        adapter = _ProbeAdapter()
        error, *_ = adapter.begin_generate(
            {"prompt": "p", "output": "o", "_abort": lambda: True},
            default_steps=20,
        )
        assert error == {"status": "error", "error": "cancelled before generate", "error_code": "CANCELLED"}

    def test_happy_path(self) -> None:
        progress: list[tuple[float | None, str | None]] = []

        def _progress(pct: float | None, msg: str | None) -> None:
            progress.append((pct, msg))

        error, steps, should_abort, on_step = _ProbeAdapter().begin_generate(
            {"prompt": "p", "output": "o", "steps": "50", "_progress": _progress},
            default_steps=20,
        )
        assert error is None
        assert steps == 50
        assert should_abort is None  # sem _abort no request
        assert on_step is not None
        assert progress == [(0.0, "started")]

    def test_default_steps_used_when_absent(self) -> None:
        error, steps, *_ = _ProbeAdapter().begin_generate({"prompt": "p", "output": "o"}, default_steps=7)
        assert error is None and steps == 7

    def test_bad_steps_falls_back_to_default(self) -> None:
        error, steps, *_ = _ProbeAdapter().begin_generate(
            {"prompt": "p", "output": "o", "steps": "abc"}, default_steps=4
        )
        assert error is None and steps == 4

    def test_on_step_reports_fraction(self) -> None:
        progress: list[float] = []
        adapter = _ProbeAdapter()

        def _progress(pct: float | None, msg: str | None) -> None:
            progress.append(pct or 0.0)

        _, _, _, on_step = adapter.begin_generate(
            {"prompt": "p", "output": "o", "_progress": _progress}, default_steps=20
        )
        assert on_step is not None
        on_step(5, 10)
        assert progress[-1] == 0.5


class TestFinishResponse:
    def test_ok_shape(self) -> None:
        r = WorkerAdapter.finish_response(output="out.png", seconds=1.23456)
        assert r == {"status": "ok", "output": "out.png", "seconds": 1.23}

    def test_extra_keys_merged(self) -> None:
        r = WorkerAdapter.finish_response(output="a.ogg", seconds=2.0, seed=42, runtime_budget={"x": 1})
        assert r["seed"] == 42 and r["runtime_budget"] == {"x": 1}

    def test_output_coerced_to_str(self) -> None:
        from pathlib import Path

        r = WorkerAdapter.finish_response(output=Path("o.glb"), seconds=0.0)
        assert r["output"] == "o.glb"
