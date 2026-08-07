"""Amostrador de VRAM por processo — a parte onde a precisão se ganha ou perde.

Medir "quanto VRAM é que este modelo gasta" com ``nvidia-smi`` no fim do job dá
um número errado por quatro razões, todas tratadas aqui:

1. **O pico não está no fim.** A activação sobe e desce dentro do ``generate``;
   quem amostra no fim mede os pesos, não o pico. → thread de amostragem a
   ~20 Hz com o máximo por janela.
2. **O worker não é o único processo.** Compositor, browser, outro job. Medir
   ``memory.used`` do dispositivo mistura tudo. → soma **por PID** (NVML
   per-process), e o resto é contabilizado à parte como ``foreign``.
3. **O worker pode ter filhos.** Pipelines que fazem fork (dataloaders, decoders
   externos) alocam VRAM num PID diferente do worker. → o conjunto seguido é o
   worker **mais os descendentes**, re-descoberto periodicamente.
4. **A thread pode ser esfomeada.** Um GIL ocupado ou um probe lento abre
   buracos na série; um pico dentro de um buraco não é visto. → cada amostra
   regista o intervalo real e o máximo é reportado, para a análise poder baixar
   a confiança em vez de mentir.

O módulo não importa ``torch`` nem NVML diretamente: o probe é injetado
(default :func:`default_probe` → ``aigamekit_shared.gpu.list_nvidia_compute_apps``),
o que o torna testável sem GPU.
"""

from __future__ import annotations

import contextlib
import threading
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

# ``(pid, nome, mib | None)`` — mesmo formato de ``gpu.list_nvidia_compute_apps``.
ComputeApps = list[tuple[int, str, int | None]]
Probe = Callable[[], ComputeApps]
PidProvider = Callable[[], set[int]]
Clock = Callable[[], float]
Sleep = Callable[[float], None]

# 20 Hz: um generate de 5 s dá ~100 amostras; o custo do probe NVML (~1 ms)
# fica < 2% de um core. Mais rápido que isto começa a competir com o worker.
DEFAULT_INTERVAL_SEC = 0.05
# Re-descobrir descendentes a cada N amostras (walk de /proc é caro).
DEFAULT_PID_REFRESH_EVERY = 20


def default_probe() -> ComputeApps:
    """Probe por omissão: NVML (fallback ``nvidia-smi``) via ``aigamekit_shared``."""
    from aigamekit_shared.gpu import list_nvidia_compute_apps

    return list_nvidia_compute_apps()


def descendant_pids(pid: int) -> set[int]:
    """``{pid}`` mais todos os descendentes vivos.

    Um worker que faz fork (dataloader, decoder externo, subprocesso de export)
    aloca VRAM num PID que o driver reporta separadamente. Ignorá-los subestima
    o pico.

    Usa ``psutil`` quando disponível; senão faz um walk de ``/proc/*/stat``
    (campo 4 = PPID). Falha graciosa: devolve ``{pid}``.

    Args:
        pid: PID raiz (o worker).

    Returns:
        Conjunto de PIDs a seguir. Nunca vazio se ``pid`` > 0.
    """
    if pid <= 0:
        return set()
    with contextlib.suppress(Exception):
        import psutil

        proc = psutil.Process(pid)
        out = {pid}
        out.update(child.pid for child in proc.children(recursive=True))
        return out
    return _descendants_from_proc(pid)


def _descendants_from_proc(pid: int) -> set[int]:
    """Fallback sem psutil: constrói a árvore a partir de ``/proc/<pid>/stat``."""
    import os

    children: dict[int, list[int]] = {}
    with contextlib.suppress(Exception):
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            with contextlib.suppress(Exception), open(f"/proc/{entry}/stat", encoding="utf-8") as fh:
                # comm pode conter espaços/parênteses → cortar até ao último ')'.
                fields = fh.read().rsplit(")", 1)[-1].split()
                ppid = int(fields[1])
                children.setdefault(ppid, []).append(int(entry))

    out = {pid}
    stack = [pid]
    while stack:
        cur = stack.pop()
        for child in children.get(cur, ()):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


@dataclass(frozen=True)
class Sample:
    """Uma amostra instantânea da VRAM do dispositivo, separada por dono.

    Attributes:
        t: Segundos monotónicos desde ``start()``.
        self_mib: Soma dos PIDs seguidos (worker + descendentes).
        foreign_mib: Soma de todos os outros processos compute.
        self_pids: Quantos PIDs seguidos apareceram no probe.
        tracked_pids: Quantos PIDs estávamos a seguir nesse instante.
        gap_sec: Intervalo real desde a amostra anterior (0.0 na primeira).
    """

    t: float
    self_mib: int
    foreign_mib: int
    self_pids: int
    tracked_pids: int
    gap_sec: float

    @property
    def missed(self) -> bool:
        """True se seguíamos PIDs mas o driver não reportou nenhum deles."""
        return self.tracked_pids > 0 and self.self_pids == 0


@dataclass(frozen=True)
class Mark:
    """Fronteira de fase na linha temporal da amostragem."""

    label: str
    t: float


@dataclass
class VramSampler:
    """Thread de amostragem com marcas de fase.

    Não é um context manager por acidente: as marcas têm de ser colocadas por
    quem conduz o ciclo (o runner), entre chamadas bloqueantes ao worker.

    Args:
        probe: Devolve a tabela de processos compute.
        pid_provider: Devolve o PID raiz a seguir (0/None enquanto não há worker).
        interval_sec: Intervalo alvo entre amostras.
        clock: Relógio monotónico (injetável em testes).
        sleep: Função de espera (injetável em testes).
        pid_refresh_every: Amostras entre re-descobertas de descendentes.
        expand_descendants: Seguir descendentes do PID raiz.
        threaded: ``False`` corre em modo manual (sem thread): quem conduz o
            ciclo chama :meth:`pump`. É o modo usado em testes, onde uma thread
            real tornaria a série não determinística.
    """

    probe: Probe = default_probe
    pid_provider: PidProvider = set
    interval_sec: float = DEFAULT_INTERVAL_SEC
    clock: Clock = time.monotonic
    sleep: Sleep = time.sleep
    pid_refresh_every: int = DEFAULT_PID_REFRESH_EVERY
    expand_descendants: bool = True
    threaded: bool = True

    _samples: list[Sample] = field(default_factory=list, init=False, repr=False)
    _marks: list[Mark] = field(default_factory=list, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _t0: float = field(default=0.0, init=False, repr=False)
    _last_t: float | None = field(default=None, init=False, repr=False)
    _tracked: set[int] = field(default_factory=set, init=False, repr=False)
    _since_refresh: int = field(default=0, init=False, repr=False)
    _probe_errors: int = field(default=0, init=False, repr=False)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Arranca a amostragem e recolhe imediatamente a primeira amostra."""
        if self._thread is not None:
            return
        self._t0 = self.clock()
        self._stop.clear()
        self.sample_now()
        if not self.threaded:
            return
        self._thread = threading.Thread(target=self._loop, name="ums-vram-sampler", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Pára a amostragem (uma amostra final é recolhida antes de sair)."""
        if self._thread is None:
            if not self.threaded:
                self.sample_now()
            return
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        self.sample_now()

    def pump(self, duration_sec: float) -> int:
        """Modo manual: recolhe as amostras que a thread teria recolhido.

        Cada iteração chama ``sleep(interval)`` — em testes é o relógio falso
        que avança aí, o que torna a série exatamente reprodutível.

        Args:
            duration_sec: Tempo simulado a cobrir.

        Returns:
            Número de amostras recolhidas (≥1 para duração positiva).
        """
        if duration_sec <= 0:
            return 0
        n = max(1, round(duration_sec / max(self.interval_sec, 1e-9)))
        for _ in range(n):
            self.sleep(self.interval_sec)
            self.sample_now()
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Espera primeiro: a amostra t=0 já foi recolhida em start().
            self.sleep(self.interval_sec)
            if self._stop.is_set():
                return
            self.sample_now()

    # ------------------------------------------------------------------
    # Amostragem
    # ------------------------------------------------------------------

    def sample_now(self) -> Sample:
        """Recolhe uma amostra imediata (usada em ``start``/``stop``/``mark``)."""
        now = self.clock()
        pids = self._resolve_pids()
        self_mib = 0
        foreign_mib = 0
        seen: set[int] = set()
        try:
            apps = self.probe() or []
        except Exception:
            self._probe_errors += 1
            apps = []
        for entry in apps:
            pid, _name, mib = entry
            if mib is None:
                continue
            if pid in pids:
                self_mib += int(mib)
                seen.add(pid)
            else:
                foreign_mib += int(mib)

        with self._lock:
            prev = self._last_t
            gap = 0.0 if prev is None else max(0.0, now - prev)
            self._last_t = now
            sample = Sample(
                t=now - self._t0,
                self_mib=self_mib,
                foreign_mib=foreign_mib,
                self_pids=len(seen),
                tracked_pids=len(pids),
                gap_sec=gap,
            )
            self._samples.append(sample)
        return sample

    def _resolve_pids(self) -> set[int]:
        """PIDs a seguir, com re-descoberta periódica de descendentes."""
        with self._lock:
            self._since_refresh += 1
            due = self._since_refresh >= self.pid_refresh_every or not self._tracked
        if not due:
            with self._lock:
                return set(self._tracked)

        roots: set[int] = set()
        provider_ok = True
        try:
            roots = {int(p) for p in (self.pid_provider() or set()) if p}
        except Exception:
            # Falha do provider (proc desapareceu a meio do walk) — manter o
            # conjunto anterior: medir pico zero é pior que um PID a mais.
            provider_ok = False
        resolved: set[int] = set()
        for root in roots:
            resolved |= descendant_pids(root) if self.expand_descendants else {root}
        with self._lock:
            # Conjunto vazio devolvido *com sucesso* é informação legítima
            # ("worker ainda não existe" / "já morreu"): aceitar, senão as
            # amostras pós-shutdown contam como cegueira do driver.
            if provider_ok:
                self._tracked = resolved
            self._since_refresh = 0
            return set(self._tracked)

    def mark(self, label: str) -> Mark:
        """Regista uma fronteira de fase (com amostra forçada no mesmo instante)."""
        self.sample_now()
        with self._lock:
            mark = Mark(label=label, t=self._samples[-1].t)
            self._marks.append(mark)
        return mark

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    @property
    def samples(self) -> list[Sample]:
        """Cópia da série completa."""
        with self._lock:
            return list(self._samples)

    @property
    def marks(self) -> list[Mark]:
        """Cópia das marcas, por ordem de registo."""
        with self._lock:
            return list(self._marks)

    @property
    def probe_errors(self) -> int:
        """Quantas vezes o probe levantou exceção (driver ocupado, NVML a cair)."""
        return self._probe_errors

    def mark_time(self, label: str) -> float | None:
        """Instante da **última** marca com este label (``None`` se não existe)."""
        for mark in reversed(self.marks):
            if mark.label == label:
                return mark.t
        return None

    def window(self, start_label: str, end_label: str) -> list[Sample]:
        """Amostras entre duas marcas, inclusive nas fronteiras.

        Raises:
            KeyError: Alguma das marcas não existe.
        """
        t_start = self.mark_time(start_label)
        t_end = self.mark_time(end_label)
        if t_start is None or t_end is None:
            missing = start_label if t_start is None else end_label
            raise KeyError(f"marca ausente: {missing!r}")
        return slice_window(self.samples, t_start, t_end)


def slice_window(samples: Sequence[Sample], t_start: float, t_end: float) -> list[Sample]:
    """Sub-série ``t_start <= t <= t_end`` (fronteiras inclusivas).

    Assume ``samples`` ordenada por ``t`` (é sempre, por construção). Usa busca
    binária — janelas são pedidas muitas vezes sobre séries longas.
    """
    if t_end < t_start:
        t_start, t_end = t_end, t_start
    times = [s.t for s in samples]
    lo = bisect_left(times, t_start)
    hi = bisect_right(times, t_end)
    return list(samples[lo:hi])


def peak_mib(samples: Iterable[Sample]) -> int:
    """Máximo de ``self_mib`` na série (0 se vazia)."""
    return max((s.self_mib for s in samples), default=0)
