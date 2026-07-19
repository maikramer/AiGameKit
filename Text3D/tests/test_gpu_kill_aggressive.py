"""kill_gpu_compute_processes_aggressive (mocks)."""

from __future__ import annotations

import signal
from unittest.mock import patch

from text3d.utils.memory import kill_gpu_compute_processes_aggressive, list_nvidia_compute_apps


def test_list_nvidia_empty_when_no_binary(monkeypatch) -> None:
    monkeypatch.setattr("gamedev_shared.gpu.shutil.which", lambda _: None)
    assert list_nvidia_compute_apps() == []


def test_kill_skips_protected_and_self() -> None:
    apps = [
        (100, "Xorg", 50),
        (200, "/usr/bin/python3", 4000),
        (300, "gnome-shell", 80),
    ]
    with (
        patch("gamedev_shared.gpu.list_nvidia_compute_apps", return_value=apps),
        patch("gamedev_shared.gpu.os.kill") as mock_kill,
    ):
        logs = kill_gpu_compute_processes_aggressive(exclude_pid=200, term_wait_seconds=0.01)
    # exclude_pid 200: skip killing self
    # 100 Xorg protected, 300 gnome-shell protected
    mock_kill.assert_not_called()
    assert any("ignorado" in x for x in logs)


def test_kill_targets_unprotected() -> None:
    apps = [(999, "python3", 1000)]
    with (
        patch("gamedev_shared.gpu.list_nvidia_compute_apps", return_value=apps),
        patch("gamedev_shared.gpu.os.kill") as mock_kill,
        patch("gamedev_shared.gpu.time.sleep", lambda _: None),
        # PID 999 é fake — não depender do dono real de /proc/999 nesta máquina.
        patch("gamedev_shared.gpu._is_user_process", return_value=True),
    ):
        kill_gpu_compute_processes_aggressive(exclude_pid=1, term_wait_seconds=0.0)
    assert any(c[0][0] == 999 and c[0][1] == signal.SIGTERM for c in mock_kill.call_args_list)
