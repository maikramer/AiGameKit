"""Orquestração da calibração: conduz um backend por um ciclo instrumentado.

Sequência (cada seta é uma marca na linha temporal do amostrador)::

    baseline → load → loaded_settled → [generate → settled]xN → unload
             → unloaded_settled → shutdown → post_shutdown

Três decisões que valem a correção do resultado:

- **A GPU tem de ser nossa.** Uma medição com outro job a correr mede o outro
  job. :meth:`CalibrationRunner.preflight` recusa com a lista de bloqueios em
  vez de produzir um número plausível e errado.
- **Nada de unload entre repetições.** O que se quer medir é a activação sobre
  pesos quentes — que é exatamente o estado em que o UMS despacha jobs seguidos.
- **Assentar antes de ler.** O driver não devolve VRAM instantaneamente; ler o
  residente imediatamente a seguir ao generate mede o pico, não o residente.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .analysis import Calibration, PhaseWindows, derive_calibration
from .sampler import Sample, VramSampler, default_probe

Sleep = Callable[[float], None]
Clock = Callable[[], float]

# VRAM de terceiros acima da qual a medição é recusada (compositor típico
# ~200-400 MiB; um job vizinho é sempre mais que isto).
DEFAULT_MAX_FOREIGN_MIB = 512
# Deriva do residual pós-unload entre ciclos que denuncia unload incompleto.
CONTEXT_DRIFT_MIB = 32


class RunnerError(RuntimeError):
    """Falha que impede a calibração de produzir um resultado utilizável."""


@dataclass
class CalibrationSpec:
    """Parâmetros de uma corrida de calibração.

    Attributes:
        backend: Nome do backend no registry.
        tool: Tool do monorepo (worker subprocesso). ``None`` = in-process.
        request: Request de ``generate`` representativo do uso real. Um prompt
            trivial mede um pico trivial — deve ser um job típico.
        load_kwargs: Kwargs de ``load`` (quant, offload, vistas, …).
        repeats: Repetições do generate. ≥2 separa warmup de estado estável.
        cycles: Pares load/unload extra para isolar o contexto CUDA.
        baseline_sec: Janela de silêncio antes do spawn.
        settle_sec: Espera para o driver assentar após cada fase.
        interval_sec: Intervalo alvo de amostragem.
        quant_mode: Etiqueta do modo de quantização (só para o relatório).
    """

    backend: str
    tool: str | None = None
    request: dict[str, Any] = field(default_factory=dict)
    load_kwargs: dict[str, Any] = field(default_factory=dict)
    repeats: int = 3
    cycles: int = 1
    baseline_sec: float = 1.0
    settle_sec: float = 1.5
    interval_sec: float = 0.05
    quant_mode: str = "none"


def default_gpu_info() -> tuple[str | None, int | None, str | None]:
    """``(nome, total_mib, driver)`` da GPU 0 — falha graciosa para ``None``."""
    name: str | None = None
    total: int | None = None
    driver: str | None = None
    with contextlib.suppress(Exception):
        from aigamekit_shared.gpu import list_gpu_snapshots

        snaps = list_gpu_snapshots() or []
        if snaps:
            snap = snaps[0]
            name = getattr(snap, "name", None) or (snap.get("name") if isinstance(snap, dict) else None)
            total = getattr(snap, "total_mib", None) or (snap.get("total_mib") if isinstance(snap, dict) else None)
    with contextlib.suppress(Exception):
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        first = (out.stdout or "").strip().splitlines()
        if first:
            parts = [p.strip() for p in first[0].split(",")]
            name = name or (parts[0] if parts else None)
            if total is None and len(parts) > 1 and parts[1].isdigit():
                total = int(parts[1])
            driver = parts[2] if len(parts) > 2 else None
    return name, total, driver


class CalibrationRunner:
    """Conduz o ciclo e devolve uma :class:`~modelserver.calibrate.analysis.Calibration`.

    Args:
        pool: Objeto com ``load``/``generate``/``unload``/``shutdown``/``worker_pid``
            (na prática :class:`modelserver.subprocess_pool.SubprocessWorkerPool`;
            em testes, um duplo).
        probe: Probe de processos compute (injetável).
        sleep: Espera (injetável).
        clock: Relógio monotónico (injetável).
        gpu_info: Fonte de metadados da GPU.
        max_foreign_mib: Limite de VRAM de terceiros no preflight.
        sampler_factory: Construtor do amostrador (testes usam o modo manual).
    """

    def __init__(
        self,
        pool: Any,
        *,
        probe: Callable[[], list[tuple[int, str, int | None]]] = default_probe,
        sleep: Sleep = time.sleep,
        clock: Clock = time.monotonic,
        gpu_info: Callable[[], tuple[str | None, int | None, str | None]] = default_gpu_info,
        max_foreign_mib: int = DEFAULT_MAX_FOREIGN_MIB,
        sampler_factory: Callable[..., VramSampler] = VramSampler,
    ) -> None:
        self._pool = pool
        self._probe = probe
        self._sleep = sleep
        self._clock = clock
        self._gpu_info = gpu_info
        self._max_foreign_mib = max_foreign_mib
        self._sampler_factory = sampler_factory

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def preflight(self, *, check_ums: bool = True) -> list[str]:
        """Razões que impedem uma medição fiável (lista vazia = pode avançar).

        Args:
            check_ums: Verificar se o supervisor tem trabalho em curso.

        Returns:
            Lista de bloqueios legíveis, por ordem de gravidade.
        """
        blockers: list[str] = []
        if check_ums:
            with contextlib.suppress(Exception):
                from aigamekit_shared.model_server import fetch_ums_queue_snapshot, ums_is_busy

                snapshot = fetch_ums_queue_snapshot()
                if snapshot and ums_is_busy(snapshot):
                    blockers.append(
                        "UMS tem jobs em curso — a medição competiria com eles pela GPU "
                        "(espera, ou usa `ums cancel --all`)"
                    )

        foreign = 0
        details: list[str] = []
        with contextlib.suppress(Exception):
            for pid, name, mib in self._probe() or []:
                if mib:
                    foreign += int(mib)
                    details.append(f"{name}(pid {pid}) {mib} MiB")
        if foreign > self._max_foreign_mib:
            blockers.append(
                f"{foreign} MiB de VRAM ocupados por outros processos (limite {self._max_foreign_mib}): "
                + ", ".join(details[:4])
            )
        return blockers

    # ------------------------------------------------------------------
    # Corrida
    # ------------------------------------------------------------------

    def run(self, spec: CalibrationSpec) -> Calibration:
        """Executa o ciclo instrumentado e devolve o resultado derivado.

        Raises:
            RunnerError: O ``load`` ou o ``generate`` falharam — sem dados, não
                se produz um footprint (um footprint de um job falhado é pior
                que nenhum).
        """
        if spec.repeats < 1:
            raise RunnerError("repeats deve ser ≥ 1")

        sampler = self._sampler_factory(
            probe=self._probe,
            pid_provider=lambda: self._worker_pids(spec.backend),
            interval_sec=spec.interval_sec,
            clock=self._clock,
            sleep=self._sleep,
        )
        gen_times: list[float] = []
        unload_windows: list[list[Sample]] = []

        sampler.start()
        try:
            sampler.mark("baseline_start")
            self._sleep(spec.baseline_sec)
            sampler.mark("baseline_end")

            sampler.mark("load_start")
            t0 = self._clock()
            try:
                self._pool.load(spec.backend, spec.tool, dict(spec.load_kwargs))
            except Exception as exc:
                raise RunnerError(f"load de {spec.backend} falhou: {exc}") from exc
            load_sec = self._clock() - t0
            sampler.mark("load_end")

            self._sleep(spec.settle_sec)
            sampler.mark("loaded_settled_end")

            for i in range(1, spec.repeats + 1):
                sampler.mark(f"gen{i}_start")
                t_gen = self._clock()
                try:
                    result = self._pool.generate(spec.backend, dict(spec.request))
                except Exception as exc:
                    raise RunnerError(f"generate #{i} de {spec.backend} falhou: {exc}") from exc
                gen_times.append(self._clock() - t_gen)
                sampler.mark(f"gen{i}_end")
                if isinstance(result, dict) and result.get("status") == "error":
                    raise RunnerError(f"generate #{i} devolveu erro: {result.get('error')}")
                self._sleep(spec.settle_sec)
                sampler.mark(f"settled{i}_end")

            sampler.mark("unload_start")
            with contextlib.suppress(Exception):
                self._pool.unload(spec.backend)
            self._sleep(spec.settle_sec)
            sampler.mark("unloaded_settled_end")
            unload_windows.append(sampler.window("unload_start", "unloaded_settled_end"))

            # Ciclos extra: só load/unload. Servem para separar contexto CUDA de
            # fuga — se o residual sobe a cada ciclo, o unload não devolve tudo.
            for c in range(2, max(1, spec.cycles) + 1):
                with contextlib.suppress(Exception):
                    self._pool.load(spec.backend, spec.tool, dict(spec.load_kwargs))
                self._sleep(spec.settle_sec)
                sampler.mark(f"cycle{c}_unload_start")
                with contextlib.suppress(Exception):
                    self._pool.unload(spec.backend)
                self._sleep(spec.settle_sec)
                sampler.mark(f"cycle{c}_unloaded_end")
                unload_windows.append(sampler.window(f"cycle{c}_unload_start", f"cycle{c}_unloaded_end"))

            sampler.mark("shutdown_start")
            with contextlib.suppress(Exception):
                self._pool.shutdown(spec.backend)
            self._sleep(spec.settle_sec)
            sampler.mark("post_shutdown_end")
        finally:
            sampler.stop()

        windows, extra_warnings = self._build_windows(sampler, spec, unload_windows)
        name, total, driver = self._safe_gpu_info()
        cal = derive_calibration(
            backend=spec.backend,
            tool=spec.tool,
            load_kwargs=spec.load_kwargs,
            quant_mode=spec.quant_mode,
            windows=windows,
            load_sec=load_sec,
            generate_sec=gen_times,
            interval_sec=spec.interval_sec,
            probe_errors=sampler.probe_errors,
            gpu_name=name,
            gpu_total_mib=total,
            driver_version=driver,
            measured_at=_utc_now(),
        )
        if extra_warnings:
            cal = replace(cal, warnings=(*cal.warnings, *extra_warnings))
        return cal

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _worker_pids(self, backend: str) -> set[int]:
        """PID raiz do worker (vazio enquanto não existe)."""
        getter = getattr(self._pool, "worker_pid", None)
        if not callable(getter):
            return set()
        with contextlib.suppress(Exception):
            pid = getter(backend)
            if pid:
                return {int(pid)}
        return set()

    def _build_windows(
        self,
        sampler: VramSampler,
        spec: CalibrationSpec,
        unload_windows: list[list[Sample]],
    ) -> tuple[PhaseWindows, list[str]]:
        """Fatia a série pelas marcas; escolhe a janela de unload canónica."""
        warnings: list[str] = []
        windows = PhaseWindows()
        with contextlib.suppress(KeyError):
            windows.baseline = sampler.window("baseline_start", "baseline_end")
        with contextlib.suppress(KeyError):
            windows.load = sampler.window("load_start", "load_end")
        with contextlib.suppress(KeyError):
            windows.loaded_settled = sampler.window("load_end", "loaded_settled_end")
        for i in range(1, spec.repeats + 1):
            with contextlib.suppress(KeyError):
                windows.generates.append(sampler.window(f"gen{i}_start", f"gen{i}_end"))
            with contextlib.suppress(KeyError):
                windows.settled.append(sampler.window(f"gen{i}_end", f"settled{i}_end"))
        with contextlib.suppress(KeyError):
            windows.post_shutdown = sampler.window("shutdown_start", "post_shutdown_end")

        if unload_windows:
            # O contexto verdadeiro é o menor residual observado: um residual
            # maior num ciclo posterior é fuga, não contexto.
            residuals = [_p50(w) for w in unload_windows]
            best = unload_windows[residuals.index(min(residuals))]
            windows.unloaded_settled = best
            drift = max(residuals) - min(residuals)
            if len(residuals) > 1 and drift > CONTEXT_DRIFT_MIB:
                warnings.append(
                    f"residual pós-unload varia {int(drift)} MiB entre ciclos "
                    f"({[int(r) for r in residuals]}): unload não devolve tudo"
                )
        return windows, warnings

    def _safe_gpu_info(self) -> tuple[str | None, int | None, str | None]:
        with contextlib.suppress(Exception):
            return self._gpu_info()
        return (None, None, None)


def _p50(samples: list[Sample]) -> float:
    """Mediana de ``self_mib`` (0.0 se vazio) — evita importar analysis aqui."""
    if not samples:
        return 0.0
    ordered = sorted(s.self_mib for s in samples)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _utc_now() -> str:
    """Timestamp ISO-8601 em UTC (segundos)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()
