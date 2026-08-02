"""Ciclo de vida do BatchDashboard — a app tem de sair sempre.

Regressão de um `gameassets resume` que ficou **7 horas a 99.9% de CPU**: o
`batch_fn` abortou às 04:41, o worker do Textual engoliu a excepção, e a app
continuou a desenhar ``✓ 0 ◌ 0 ✗ 0 Σ 21`` indefinidamente. Três buracos:

1. ``_run_batch_worker`` nunca chamava ``exit()`` (nem no caminho feliz);
2. excepção no ``batch_fn`` perdida, sem diagnóstico e sem código de saída;
3. sem watchdog — nada detectava a ausência total de progresso.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from gameassets.dashboard import (
    DEFAULT_STALL_TIMEOUT_SEC,
    STALL_TIMEOUT_ENV,
    BatchDashboard,
    resolve_stall_timeout,
)


class TestResolveStallTimeout:
    def test_default_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)
        assert resolve_stall_timeout() == DEFAULT_STALL_TIMEOUT_SEC

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STALL_TIMEOUT_ENV, "90")
        assert resolve_stall_timeout() == 90.0

    def test_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STALL_TIMEOUT_ENV, "0")
        assert resolve_stall_timeout() == 0.0

    def test_garbage_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STALL_TIMEOUT_ENV, "nao-e-numero")
        assert resolve_stall_timeout() == DEFAULT_STALL_TIMEOUT_SEC

    def test_negative_clamped_to_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STALL_TIMEOUT_ENV, "-5")
        assert resolve_stall_timeout() == 0.0


def _app(batch_fn, **kw) -> BatchDashboard:
    return BatchDashboard(
        game_title="t",
        asset_ids=["a", "b"],
        pipeline_desc="3d",
        batch_fn=batch_fn,
        **kw,
    )


class TestDashboardExits:
    def test_exits_when_batch_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Caminho feliz: a app não pode ficar viva depois do batch acabar."""
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)
        seen: list[str] = []

        def fn(dash: BatchDashboard) -> None:
            seen.append("ran")
            dash.finish()

        app = _app(fn)
        app.run(headless=True)
        assert seen == ["ran"]
        assert app.batch_error is None
        assert app.finished is True
        assert app.return_code == 0

    def test_exits_when_batch_forgets_finish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)
        app = _app(lambda dash: None)
        app.run(headless=True)
        assert app.batch_error is None
        assert app.finished is True

    def test_batch_exception_is_surfaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A excepção do worker tem de chegar ao chamador, não ser engolida."""
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)

        def boom(dash: BatchDashboard) -> None:
            raise RuntimeError("asset falhou")

        app = _app(boom)
        app.run(headless=True)
        assert isinstance(app.batch_error, RuntimeError)
        assert "asset falhou" in str(app.batch_error)
        assert app.return_code == 1

    def test_base_exception_also_surfaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``click.Abort`` herda de ``BaseException`` em algumas versões."""
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)

        class Abort(BaseException):
            pass

        def boom(dash: BatchDashboard) -> None:
            raise Abort("abortado")

        app = _app(boom)
        app.run(headless=True)
        assert isinstance(app.batch_error, Abort)
        assert app.return_code == 1


class TestWatchdog:
    def test_stalled_batch_is_aborted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Batch sem qualquer progresso é abortado em vez de girar para sempre."""
        monkeypatch.setenv(STALL_TIMEOUT_ENV, "0.01")
        import threading

        released = threading.Event()

        def hang(dash: BatchDashboard) -> None:
            # Espera até o watchdog matar a app (com tecto para não pendurar o teste).
            released.wait(timeout=30)

        app = _app(hang)
        try:
            app.run(headless=True)
        finally:
            released.set()
        assert isinstance(app.batch_error, TimeoutError)
        assert "sem progresso" in str(app.batch_error)
        assert app.return_code == 1

    def test_activity_keeps_watchdog_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Eventos de progresso renovam o watchdog."""
        monkeypatch.delenv(STALL_TIMEOUT_ENV, raising=False)

        def fn(dash: BatchDashboard) -> None:
            dash.feed_event("a", "text3d", "progress", phase="shape")
            dash.finish()

        app = _app(fn, stall_timeout_sec=600.0)
        app.run(headless=True)
        assert app.batch_error is None
