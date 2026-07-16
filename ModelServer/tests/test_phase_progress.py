"""Progress/abort por fase nos adapters 3D (hooks sem GPU)."""

from __future__ import annotations

from typing import Any

from modelserver import protocol as P
from modelserver.adapters.base import BackendAdapter


class _PhaseAdapter(BackendAdapter):
    """Simula fases text3d/paint3d/part3d com abort entre steps."""

    name = "phase_fake"

    def load(self, **kwargs: Any) -> Any:
        return object()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")
        self.report_progress(request, 0.0, "started")
        if self.should_abort(request):
            return self.cancelled_response("cancelled before mid")
        self.report_progress(request, 0.5, "mid_phase")
        if self.should_abort(request):
            return self.cancelled_response("cancelled before done")
        self.report_progress(request, 1.0, "done")
        return {"status": P.STATUS_OK, "output": "/tmp/out.glb"}

    def unload(self, model: Any) -> None:
        pass


class TestPhaseProgress:
    def test_reports_at_least_two_progress_events(self) -> None:
        events: list[tuple[float | None, str | None]] = []
        req = {"_progress": lambda pct, msg: events.append((pct, msg))}
        adapter = _PhaseAdapter()
        resp = adapter.generate(object(), req)
        assert resp["status"] == P.STATUS_OK
        assert len(events) >= 2
        assert events[0][1] == "started"
        assert events[-1][1] == "done"

    def test_cancel_mid_phase(self) -> None:
        flag = {"abort": False}
        events: list[str | None] = []

        def _progress(pct: float | None, msg: str | None) -> None:
            events.append(msg)
            if msg == "started":
                flag["abort"] = True

        req = {
            "_abort": lambda: flag["abort"],
            "_progress": _progress,
        }
        adapter = _PhaseAdapter()
        resp = adapter.generate(object(), req)
        assert resp.get("error_code") == P.ERR_CANCELLED
        assert "started" in events
        assert "done" not in events
