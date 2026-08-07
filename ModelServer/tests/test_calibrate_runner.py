"""Testes do runner — ciclo completo contra uma GPU sintética.

O valor destes testes está no ``TestGroundTruth``: uma GPU falsa com uma
decomposição *conhecida* (contexto/pesos/activação/transiente de load) é
conduzida pelo runner real, e o resultado tem de reproduzir os números de
partida. É a verificação de ponta a ponta de que a amostragem, o fatiamento por
marcas e a derivação concordam entre si.
"""

from __future__ import annotations

import pytest
from modelserver.calibrate.analysis import CONFIDENCE_HIGH
from modelserver.calibrate.runner import (
    CalibrationRunner,
    CalibrationSpec,
    RunnerError,
    default_gpu_info,
)
from modelserver.calibrate.sampler import VramSampler

INTERVAL = 0.05


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeGpu:
    """Modelo de VRAM com decomposição conhecida.

    Cada fase devolve exatamente o que um driver reportaria para o processo do
    worker nesse momento; ``None`` significa "processo não existe / sem CUDA".
    """

    def __init__(
        self,
        *,
        pid: int = 4242,
        context: int = 320,
        weights: int = 1200,
        activation: int = 900,
        load_extra: int = 400,
        fragmentation: int = 0,
        leak: int = 0,
        warmup_extra: int = 0,
        foreign: int = 120,
        unload_residual_growth: int = 0,
    ) -> None:
        self.pid = pid
        self.context = context
        self.weights = weights
        self.activation = activation
        self.load_extra = load_extra
        self.fragmentation = fragmentation
        self.leak = leak
        self.warmup_extra = warmup_extra
        self.foreign = foreign
        self.unload_residual_growth = unload_residual_growth
        self.phase = "cold"
        self.run = 0
        self.unloads = 0

    @property
    def resident(self) -> int:
        return self.context + self.weights

    def mib(self) -> int | None:
        if self.phase in ("cold", "dead"):
            return None
        if self.phase == "load_ramp":
            return self.resident // 2
        if self.phase == "load_peak":
            return self.resident + self.load_extra
        if self.phase == "loaded":
            return self.resident
        if self.phase == "gen_low":
            return self.resident + self.activation // 4
        if self.phase == "gen_peak":
            extra = self.warmup_extra if self.run == 1 else 0
            return self.resident + self.activation + extra
        if self.phase == "settled":
            return self.resident + self.fragmentation + self.leak * (self.run - 1)
        if self.phase == "unloaded":
            return self.context + self.unload_residual_growth * (self.unloads - 1)
        raise AssertionError(f"fase desconhecida: {self.phase}")

    def probe(self) -> list[tuple[int, str, int | None]]:
        apps: list[tuple[int, str, int | None]] = [(99, "compositor", self.foreign)]
        mib = self.mib()
        if mib is not None:
            apps.append((self.pid, "worker", mib))
        return apps


class FakePool:
    """Duplo do ``SubprocessWorkerPool`` que move a GPU falsa pelas fases."""

    def __init__(self, gpu: FakeGpu, world: World, *, fail_load=False, fail_generate=False, error_result=False) -> None:
        self.gpu = gpu
        self.world = world
        self.fail_load = fail_load
        self.fail_generate = fail_generate
        self.error_result = error_result
        self.calls: list[str] = []

    def worker_pid(self, backend: str) -> int | None:
        return None if self.gpu.phase in ("cold", "dead") else self.gpu.pid

    def load(self, backend: str, tool: str | None, kwargs: dict) -> dict:
        self.calls.append("load")
        if self.fail_load:
            raise RuntimeError("worker morreu no load")
        self.gpu.phase = "load_ramp"
        self.world.tick(0.3)
        self.gpu.phase = "load_peak"
        self.world.tick(0.2)
        self.gpu.phase = "loaded"
        self.world.tick(0.2)
        return {"ready": True, "vram_mib": self.gpu.mib()}

    def generate(self, backend: str, request: dict) -> dict:
        self.calls.append("generate")
        if self.fail_generate:
            raise RuntimeError("CUDA OOM")
        self.gpu.run += 1
        self.gpu.phase = "gen_low"
        self.world.tick(0.2)
        self.gpu.phase = "gen_peak"
        self.world.tick(0.3)
        self.gpu.phase = "gen_low"
        self.world.tick(0.2)
        self.gpu.phase = "settled"
        if self.error_result:
            return {"status": "error", "error": "modelo recusou"}
        return {"status": "ok", "output": "/tmp/x.bin"}

    def unload(self, backend: str) -> bool:
        self.calls.append("unload")
        self.gpu.unloads += 1
        self.gpu.phase = "unloaded"
        return True

    def shutdown(self, backend: str) -> bool:
        self.calls.append("shutdown")
        self.gpu.phase = "dead"
        return True


class World:
    """Cola entre o relógio falso e o amostrador manual."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.sampler: VramSampler | None = None

    def tick(self, seconds: float) -> None:
        if self.sampler is None:
            self.clock.advance(seconds)
            return
        self.sampler.pump(seconds)

    def factory(self, **kwargs) -> VramSampler:
        """Cria o amostrador em modo manual (sleep = avançar o relógio falso)."""
        kwargs.pop("sleep", None)
        kwargs.pop("clock", None)
        sampler = VramSampler(
            clock=self.clock,
            sleep=self.clock.advance,
            expand_descendants=False,
            threaded=False,
            pid_refresh_every=1,
            **kwargs,
        )
        self.sampler = sampler
        return sampler


def make_runner(gpu: FakeGpu, **pool_kwargs) -> tuple[CalibrationRunner, FakePool, World]:
    clock = FakeClock()
    world = World(clock)
    pool = FakePool(gpu, world, **pool_kwargs)
    runner = CalibrationRunner(
        pool,
        probe=gpu.probe,
        sleep=world.tick,
        clock=clock,
        gpu_info=lambda: ("FakeGPU", 6141, "580.0"),
        sampler_factory=world.factory,
    )
    return runner, pool, world


def spec(**kwargs) -> CalibrationSpec:
    params = {
        "backend": "fake3d",
        "tool": "fake3d",
        "request": {"prompt": "x", "output": "/tmp/x"},
        "load_kwargs": {"sdnq_preset": "int4"},
        "repeats": 3,
        "baseline_sec": 0.5,
        "settle_sec": 0.5,
        "interval_sec": INTERVAL,
        "quant_mode": "sdnq-int4",
    }
    params.update(kwargs)
    return CalibrationSpec(**params)


class TestGroundTruth:
    """A GPU falsa tem números conhecidos; o runner tem de os recuperar."""

    def test_recovers_context_weights_activation(self):
        gpu = FakeGpu(context=320, weights=1200, activation=900, load_extra=400)
        runner, _, _ = make_runner(gpu)
        cal = runner.run(spec())
        assert cal.context_mib == 320
        assert cal.weights_mib == 1200
        assert cal.activation_mib == 900

    def test_peak_is_the_generate_peak_when_it_dominates(self):
        gpu = FakeGpu(context=320, weights=1200, activation=900, load_extra=400)
        runner, _, _ = make_runner(gpu)
        cal = runner.run(spec())
        assert cal.generate_peak_mib == 2420
        assert cal.load_peak_mib == 1920
        assert cal.peak_mib == 2420

    def test_peak_is_the_load_transient_when_it_dominates(self):
        gpu = FakeGpu(context=300, weights=1000, activation=200, load_extra=1500)
        runner, _, _ = make_runner(gpu)
        cal = runner.run(spec())
        assert cal.peak_mib == cal.load_peak_mib == 2800
        assert any("pico no load" in w for w in cal.warnings)

    def test_clean_run_is_high_confidence(self):
        runner, _, _ = make_runner(FakeGpu())
        cal = runner.run(spec())
        assert cal.confidence == CONFIDENCE_HIGH
        assert cal.contaminated is False
        assert cal.warnings == ()

    def test_foreign_baseline_is_measured_not_attributed(self):
        runner, _, _ = make_runner(FakeGpu(foreign=450))
        cal = runner.run(spec())
        assert cal.foreign_baseline_mib == 450
        # A VRAM do compositor não entra na decomposição do modelo.
        assert cal.weights_mib == 1200

    def test_hardware_metadata_flows_into_the_result(self):
        runner, _, _ = make_runner(FakeGpu())
        cal = runner.run(spec())
        assert (cal.gpu_name, cal.gpu_total_mib, cal.driver_version) == ("FakeGPU", 6141, "580.0")
        assert cal.measured_at and cal.measured_at.endswith("+00:00")


class TestCycleMechanics:
    def test_pool_call_sequence(self):
        runner, pool, _ = make_runner(FakeGpu())
        runner.run(spec(repeats=2))
        assert pool.calls == ["load", "generate", "generate", "unload", "shutdown"]

    def test_no_unload_between_repeats(self):
        runner, pool, _ = make_runner(FakeGpu())
        runner.run(spec(repeats=3))
        assert pool.calls.index("unload") > pool.calls.count("generate")

    def test_repeats_are_all_measured(self):
        runner, _, _ = make_runner(FakeGpu())
        cal = runner.run(spec(repeats=3))
        assert cal.repeats == 3
        assert len(cal.generate_sec) == 3
        assert "generate_3" in cal.phases

    def test_timings_are_positive(self):
        runner, _, _ = make_runner(FakeGpu())
        cal = runner.run(spec())
        assert cal.load_sec > 0
        assert cal.generate_sec_median > 0

    def test_warmup_is_detected_across_repeats(self):
        runner, _, _ = make_runner(FakeGpu(warmup_extra=300))
        cal = runner.run(spec(repeats=3))
        assert cal.warmup_delta_mib == 300

    def test_fragmentation_is_detected(self):
        runner, _, _ = make_runner(FakeGpu(fragmentation=192))
        cal = runner.run(spec())
        assert cal.fragmentation_mib == 192

    def test_leak_across_repeats_is_detected(self):
        runner, _, _ = make_runner(FakeGpu(leak=120))
        cal = runner.run(spec(repeats=3))
        assert cal.leak_mib_per_run == pytest.approx(120, abs=1)

    def test_extra_cycles_pick_the_lowest_residual_as_context(self):
        gpu = FakeGpu(context=320, unload_residual_growth=200)
        runner, pool, _ = make_runner(gpu)
        cal = runner.run(spec(cycles=3))
        assert cal.context_mib == 320  # o menor residual, não o último
        assert any("residual pós-unload varia" in w for w in cal.warnings)
        assert pool.calls.count("load") == 3

    def test_single_cycle_has_no_drift_warning(self):
        runner, _, _ = make_runner(FakeGpu())
        cal = runner.run(spec(cycles=1))
        assert not any("residual pós-unload varia" in w for w in cal.warnings)

    def test_orphan_vram_after_shutdown_is_detected(self):
        class StubbornPool(FakePool):
            def shutdown(self, backend: str) -> bool:
                self.calls.append("shutdown")
                self.gpu.phase = "unloaded"  # worker não morreu
                return False

        clock = FakeClock()
        world = World(clock)
        gpu = FakeGpu(context=320)
        pool = StubbornPool(gpu, world)
        runner = CalibrationRunner(
            pool,
            probe=gpu.probe,
            sleep=world.tick,
            clock=clock,
            gpu_info=lambda: (None, None, None),
            sampler_factory=world.factory,
        )
        cal = runner.run(spec())
        assert cal.orphan_mib == 320
        assert any("órfão" in w for w in cal.warnings)


class TestFailures:
    def test_load_failure_raises_runner_error(self):
        runner, _, _ = make_runner(FakeGpu(), fail_load=True)
        with pytest.raises(RunnerError, match="load de fake3d falhou"):
            runner.run(spec())

    def test_generate_exception_raises_runner_error(self):
        runner, _, _ = make_runner(FakeGpu(), fail_generate=True)
        with pytest.raises(RunnerError, match="generate #1"):
            runner.run(spec())

    def test_generate_error_result_raises_runner_error(self):
        runner, _, _ = make_runner(FakeGpu(), error_result=True)
        with pytest.raises(RunnerError, match="modelo recusou"):
            runner.run(spec())

    def test_zero_repeats_rejected(self):
        runner, _, _ = make_runner(FakeGpu())
        with pytest.raises(RunnerError, match="repeats"):
            runner.run(spec(repeats=0))

    def test_pool_without_worker_pid_still_runs(self):
        class PidlessPool(FakePool):
            worker_pid = None  # type: ignore[assignment]

        clock = FakeClock()
        world = World(clock)
        gpu = FakeGpu()
        pool = PidlessPool(gpu, world)
        runner = CalibrationRunner(
            pool,
            probe=gpu.probe,
            sleep=world.tick,
            clock=clock,
            gpu_info=lambda: (None, None, None),
            sampler_factory=world.factory,
        )
        cal = runner.run(spec(repeats=1))
        # Sem PID não há atribuição: o resultado é zero, mas não rebenta.
        assert cal.peak_mib == 0

    def test_unload_failure_does_not_abort_the_run(self):
        class BadUnloadPool(FakePool):
            def unload(self, backend: str) -> bool:
                self.calls.append("unload")
                raise RuntimeError("worker não responde")

        clock = FakeClock()
        world = World(clock)
        gpu = FakeGpu()
        pool = BadUnloadPool(gpu, world)
        runner = CalibrationRunner(
            pool,
            probe=gpu.probe,
            sleep=world.tick,
            clock=clock,
            gpu_info=lambda: (None, None, None),
            sampler_factory=world.factory,
        )
        cal = runner.run(spec(repeats=1))
        assert cal.peak_mib > 0

    def test_gpu_info_failure_is_tolerated(self):
        clock = FakeClock()
        world = World(clock)
        gpu = FakeGpu()
        pool = FakePool(gpu, world)

        def boom():
            raise OSError("nvidia-smi ausente")

        runner = CalibrationRunner(
            pool,
            probe=gpu.probe,
            sleep=world.tick,
            clock=clock,
            gpu_info=boom,
            sampler_factory=world.factory,
        )
        cal = runner.run(spec(repeats=1))
        assert cal.gpu_name is None


class TestPreflight:
    def test_clean_gpu_has_no_blockers(self):
        runner, _, _ = make_runner(FakeGpu(foreign=100))
        assert runner.preflight(check_ums=False) == []

    def test_foreign_vram_blocks(self):
        gpu = FakeGpu(foreign=3000)
        runner, _, _ = make_runner(gpu)
        blockers = runner.preflight(check_ums=False)
        assert len(blockers) == 1
        assert "3000 MiB" in blockers[0]

    def test_busy_ums_blocks(self, monkeypatch):
        import aigamekit_shared.model_server as ms

        monkeypatch.setattr(ms, "fetch_ums_queue_snapshot", lambda **_: {"inflight": 1}, raising=False)
        monkeypatch.setattr(ms, "ums_is_busy", lambda snapshot=None: True, raising=False)
        runner, _, _ = make_runner(FakeGpu(foreign=10))
        blockers = runner.preflight()
        assert any("UMS tem jobs em curso" in b for b in blockers)

    def test_idle_ums_does_not_block(self, monkeypatch):
        import aigamekit_shared.model_server as ms

        monkeypatch.setattr(ms, "fetch_ums_queue_snapshot", lambda **_: {"inflight": 0}, raising=False)
        monkeypatch.setattr(ms, "ums_is_busy", lambda snapshot=None: False, raising=False)
        runner, _, _ = make_runner(FakeGpu(foreign=10))
        assert runner.preflight() == []

    def test_probe_failure_does_not_block(self):
        def boom():
            raise RuntimeError("NVML down")

        clock = FakeClock()
        world = World(clock)
        runner = CalibrationRunner(FakePool(FakeGpu(), world), probe=boom, sleep=world.tick, clock=clock)
        assert runner.preflight(check_ums=False) == []

    def test_custom_foreign_limit(self):
        gpu = FakeGpu(foreign=600)
        clock = FakeClock()
        world = World(clock)
        runner = CalibrationRunner(
            FakePool(gpu, world),
            probe=gpu.probe,
            sleep=world.tick,
            clock=clock,
            max_foreign_mib=5000,
        )
        assert runner.preflight(check_ums=False) == []


class TestDefaultGpuInfo:
    def test_returns_a_triple(self):
        info = default_gpu_info()
        assert isinstance(info, tuple)
        assert len(info) == 3
