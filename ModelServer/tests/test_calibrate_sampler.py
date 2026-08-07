"""Testes do amostrador de VRAM (``modelserver.calibrate.sampler``).

Sem GPU: o probe e o relógio são injetados. Os testes cobrem exatamente as
armadilhas que o módulo existe para evitar — atribuição por PID, descendentes,
gaps, cegueira do driver e falhas do probe.
"""

from __future__ import annotations

import threading
import time

import pytest
from modelserver.calibrate.sampler import (
    DEFAULT_INTERVAL_SEC,
    Mark,
    Sample,
    VramSampler,
    descendant_pids,
    peak_mib,
    slice_window,
)


class FakeClock:
    """Relógio monotónico controlado pelo teste."""

    def __init__(self, start: float = 100.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_sampler(script, *, pids=(4242,), interval=0.05, **kwargs) -> tuple[VramSampler, FakeClock]:
    """Amostrador manual cujo probe devolve o próximo item de ``script``."""
    clock = FakeClock()
    state = {"i": 0}

    def probe():
        idx = min(state["i"], len(script) - 1)
        state["i"] += 1
        return script[idx]

    sampler = VramSampler(
        probe=probe,
        pid_provider=lambda: set(pids),
        interval_sec=interval,
        clock=clock,
        sleep=clock.advance,
        expand_descendants=False,
        threaded=False,
        **kwargs,
    )
    return sampler, clock


class TestSampleShape:
    def test_missed_true_when_tracking_but_no_data(self):
        sample = Sample(t=0.0, self_mib=0, foreign_mib=100, self_pids=0, tracked_pids=1, gap_sec=0.05)
        assert sample.missed is True

    def test_missed_false_when_not_tracking(self):
        sample = Sample(t=0.0, self_mib=0, foreign_mib=100, self_pids=0, tracked_pids=0, gap_sec=0.0)
        assert sample.missed is False

    def test_missed_false_when_data_present(self):
        sample = Sample(t=0.0, self_mib=512, foreign_mib=0, self_pids=1, tracked_pids=1, gap_sec=0.05)
        assert sample.missed is False


class TestAttribution:
    def test_separates_self_from_foreign(self):
        script = [[(4242, "worker", 1500), (99, "firefox", 300)]]
        sampler, _ = make_sampler(script)
        sample = sampler.sample_now()
        assert sample.self_mib == 1500
        assert sample.foreign_mib == 300

    def test_sums_multiple_tracked_pids(self):
        script = [[(4242, "worker", 1000), (4243, "child", 500), (7, "other", 50)]]
        sampler, _ = make_sampler(script, pids=(4242, 4243))
        sample = sampler.sample_now()
        assert sample.self_mib == 1500
        assert sample.foreign_mib == 50
        assert sample.self_pids == 2

    def test_entries_without_mib_are_ignored(self):
        script = [[(4242, "worker", None), (99, "x", None)]]
        sampler, _ = make_sampler(script)
        sample = sampler.sample_now()
        assert sample.self_mib == 0
        assert sample.foreign_mib == 0
        assert sample.missed is True

    def test_no_tracked_pids_means_everything_is_foreign(self):
        script = [[(4242, "worker", 1500)]]
        sampler, _ = make_sampler(script, pids=())
        sample = sampler.sample_now()
        assert sample.self_mib == 0
        assert sample.foreign_mib == 1500
        assert sample.missed is False


class TestProbeResilience:
    def test_probe_exception_counts_and_does_not_raise(self):
        clock = FakeClock()

        def boom():
            raise RuntimeError("NVML ocupado")

        sampler = VramSampler(
            probe=boom,
            pid_provider=lambda: {1},
            clock=clock,
            sleep=clock.advance,
            expand_descendants=False,
            threaded=False,
        )
        sample = sampler.sample_now()
        assert sample.self_mib == 0
        assert sampler.probe_errors == 1

    def test_pid_provider_exception_keeps_previous_tracking(self):
        clock = FakeClock()
        calls = {"n": 0}

        def flaky_pids():
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("proc desapareceu")
            return {4242}

        sampler = VramSampler(
            probe=lambda: [(4242, "worker", 900)],
            pid_provider=flaky_pids,
            clock=clock,
            sleep=clock.advance,
            expand_descendants=False,
            threaded=False,
            pid_refresh_every=1,
        )
        first = sampler.sample_now()
        second = sampler.sample_now()
        assert first.self_mib == 900
        # Falha do provider não pode zerar a atribuição: mediria pico 0.
        assert second.self_mib == 900


class TestTimeline:
    def test_gap_is_measured_from_previous_sample(self):
        sampler, clock = make_sampler([[(4242, "w", 10)]])
        sampler.sample_now()
        clock.advance(0.4)
        sample = sampler.sample_now()
        assert sample.gap_sec == pytest.approx(0.4)

    def test_first_sample_has_zero_gap(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]])
        assert sampler.sample_now().gap_sec == 0.0

    def test_start_records_first_sample_at_t_zero(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]])
        sampler.start()
        assert sampler.samples[0].t == 0.0

    def test_pump_collects_expected_number_of_samples(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]], interval=0.05)
        sampler.start()
        n = sampler.pump(1.0)
        assert n == 20
        assert len(sampler.samples) == 21

    def test_pump_zero_duration_is_noop(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]])
        sampler.start()
        before = len(sampler.samples)
        assert sampler.pump(0) == 0
        assert len(sampler.samples) == before

    def test_marks_are_recorded_with_sample(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]])
        sampler.start()
        mark = sampler.mark("load_start")
        assert isinstance(mark, Mark)
        assert sampler.mark_time("load_start") == mark.t
        assert sampler.mark_time("inexistente") is None

    def test_mark_time_returns_last_occurrence(self):
        sampler, clock = make_sampler([[(4242, "w", 10)]])
        sampler.start()
        sampler.mark("x")
        clock.advance(1.0)
        second = sampler.mark("x")
        assert sampler.mark_time("x") == second.t


class TestWindows:
    def test_window_between_marks_is_inclusive(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]], interval=0.1)
        sampler.start()
        mark_a = sampler.mark("a")
        sampler.pump(0.5)
        mark_b = sampler.mark("b")
        window = sampler.window("a", "b")
        # Fronteiras incluídas (amostras com t igual ao da marca entram todas).
        assert window[0].t == mark_a.t
        assert window[-1].t == mark_b.t
        assert len(window) >= 6

    def test_window_excludes_samples_outside_marks(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]], interval=0.1)
        sampler.start()
        sampler.pump(0.3)  # ruído antes
        sampler.mark("a")
        sampler.pump(0.2)
        sampler.mark("b")
        sampler.pump(0.3)  # ruído depois
        window = sampler.window("a", "b")
        assert len(window) < len(sampler.samples)
        assert all(sampler.mark_time("a") <= s.t <= sampler.mark_time("b") for s in window)

    def test_window_missing_mark_raises(self):
        sampler, _ = make_sampler([[(4242, "w", 10)]])
        sampler.start()
        sampler.mark("a")
        with pytest.raises(KeyError):
            sampler.window("a", "nao_existe")

    def test_slice_window_handles_reversed_bounds(self):
        samples = [
            Sample(t=float(i), self_mib=i, foreign_mib=0, self_pids=1, tracked_pids=1, gap_sec=1.0) for i in range(5)
        ]
        assert len(slice_window(samples, 3.0, 1.0)) == 3

    def test_slice_window_empty_series(self):
        assert slice_window([], 0.0, 5.0) == []

    def test_peak_mib_of_empty_series_is_zero(self):
        assert peak_mib([]) == 0

    def test_peak_mib_picks_maximum(self):
        samples = [
            Sample(t=0.0, self_mib=v, foreign_mib=0, self_pids=1, tracked_pids=1, gap_sec=0.0) for v in (10, 900, 40)
        ]
        assert peak_mib(samples) == 900


class TestPidDiscovery:
    def test_descendant_pids_includes_self(self):
        assert time.monotonic  # sanity: módulo importado
        pids = descendant_pids(1)
        assert 1 in pids

    def test_descendant_pids_of_invalid_pid_is_empty(self):
        assert descendant_pids(0) == set()
        assert descendant_pids(-5) == set()

    def test_descendant_pids_of_current_process_finds_thread_free_tree(self):
        import os

        pids = descendant_pids(os.getpid())
        assert os.getpid() in pids

    def test_refresh_interval_limits_provider_calls(self):
        clock = FakeClock()
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return {4242}

        sampler = VramSampler(
            probe=lambda: [(4242, "w", 100)],
            pid_provider=provider,
            clock=clock,
            sleep=clock.advance,
            expand_descendants=False,
            threaded=False,
            pid_refresh_every=5,
        )
        for _ in range(10):
            sampler.sample_now()
        # 1ª (tracked vazio) + a cada 5 amostras.
        assert calls["n"] <= 3


class TestThreadedMode:
    def test_threaded_sampler_collects_and_stops(self):
        stop_after = threading.Event()

        def probe():
            return [(4242, "w", 128)]

        sampler = VramSampler(
            probe=probe,
            pid_provider=lambda: {4242},
            interval_sec=0.001,
            expand_descendants=False,
        )
        sampler.start()
        time.sleep(0.05)
        sampler.stop()
        stop_after.set()
        assert len(sampler.samples) > 2
        assert all(s.self_mib == 128 for s in sampler.samples)

    def test_double_start_is_idempotent(self):
        sampler = VramSampler(
            probe=lambda: [],
            pid_provider=set,
            interval_sec=0.001,
            expand_descendants=False,
        )
        sampler.start()
        sampler.start()
        sampler.stop()
        sampler.stop()  # não levanta

    def test_default_interval_is_twenty_hz(self):
        assert pytest.approx(0.05) == DEFAULT_INTERVAL_SEC
