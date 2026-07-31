"""Kill GPU respeita a fila UMS — não mata quando há jobs."""

from __future__ import annotations

from unittest.mock import patch

from aigamekit_shared.gpu import kill_gpu_compute_processes_aggressive


class TestKillRespectsUmsQueue:
    def test_refuses_when_ums_busy(self) -> None:
        snap = {
            "inflight": 1,
            "queue_depth": 0,
            "running": [{"job_id": "job-1", "backend": "text3d"}],
            "queued": [],
        }
        with (
            patch("aigamekit_shared.model_server.is_ums_running", return_value=True),
            patch("aigamekit_shared.model_server.fetch_ums_queue_snapshot", return_value=snap),
            patch("aigamekit_shared.model_server.ums_is_busy", return_value=True),
            patch("aigamekit_shared.gpu.list_nvidia_compute_apps") as apps,
        ):
            logs = kill_gpu_compute_processes_aggressive(exclude_pid=1, respect_ums_queue=True)
        apps.assert_not_called()
        assert any(line.startswith("[recusado]") for line in logs)
        assert any("UMS" in line for line in logs)

    def test_proceeds_when_ums_idle(self) -> None:
        with (
            patch("aigamekit_shared.model_server.is_ums_running", return_value=True),
            patch(
                "aigamekit_shared.model_server.fetch_ums_queue_snapshot",
                return_value={"inflight": 0, "queue_depth": 0, "running": [], "queued": []},
            ),
            patch("aigamekit_shared.model_server.ums_is_busy", return_value=False),
            patch("aigamekit_shared.model_server.discover_server_pids", return_value=set()),
            patch("aigamekit_shared.gpu.list_nvidia_compute_apps", return_value=[]),
        ):
            logs = kill_gpu_compute_processes_aggressive(exclude_pid=1, respect_ums_queue=True)
        assert any("Sem alvos" in line or "não listou" in line for line in logs)
        assert not any(line.startswith("[recusado]") for line in logs)

    def test_refuses_when_ums_up_but_snapshot_fails(self) -> None:
        with (
            patch("aigamekit_shared.model_server.is_ums_running", return_value=True),
            patch("aigamekit_shared.model_server.fetch_ums_queue_snapshot", return_value=None),
            patch("aigamekit_shared.gpu.list_nvidia_compute_apps") as apps,
        ):
            logs = kill_gpu_compute_processes_aggressive(exclude_pid=1, respect_ums_queue=True)
        apps.assert_not_called()
        assert any(line.startswith("[recusado]") for line in logs)
        assert any("snapshot" in line.lower() or "incerto" in line.lower() for line in logs)
