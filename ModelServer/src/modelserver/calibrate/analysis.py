"""Derivação pura: série de amostras → footprint com nível de confiança.

Sem torch, sem GPU, sem threads — só aritmética sobre :class:`~modelserver.calibrate.sampler.Sample`.
Todo o julgamento sobre "este número é de confiar" vive aqui.

Modelo de decomposição
----------------------

O que o driver reporta para o processo do worker é sempre a soma::

    resident = contexto_CUDA + pesos + cache_do_allocator (+ activação, durante o generate)

Nenhum destes é observável isoladamente, mas as **fronteiras de fase** dão-nos
um sistema resolúvel:

===============================  ====================================================
Medida                           Interpretação
===============================  ====================================================
``unloaded_settled`` (mediana)   contexto CUDA + o que o ``unload`` não liberta
``loaded_settled`` (mediana)     contexto + pesos residentes
``generate`` (máximo)            contexto + pesos + activação de pico
``load`` (máximo)                transiente do carregamento (shards, quantização)
===============================  ====================================================

Daí: ``contexto = unloaded``, ``pesos = loaded - contexto``,
``activação = pico_generate - loaded``.

Duas subtilezas que separam isto de "correr o nvidia-smi no fim":

1. **O pico pode estar no load, não no generate.** Carregar um shard fp16 para
   a GPU e só depois quantizar em int4 tem um transiente acima do estado
   estacionário. Admitir só com o pico do generate faz OOM no load. Por isso
   ``peak = max(pico_load, pico_generate)``.
2. **Activação ≫ pesos denuncia staged load.** Se o modelo carrega um segundo
   modelo *dentro* do generate (encoder de texto que sobe à GPU e desce), a
   diferença aparece como "activação" gigante. O número está certo para
   admissão, mas a interpretação não — por isso é sinalizado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .sampler import Sample

# --- Limiares de confiança (constantes para serem testáveis) -------------------

# Variação de VRAM de terceiros acima da qual a medição é considerada contaminada.
CONTAMINATION_MIB = 64
# Gap entre amostras acima de Nx o intervalo alvo → risco de pico não visto.
GAP_FACTOR_WARN = 4.0
# Fração de amostras sem dados do driver (com PIDs a seguir) tolerada.
MISSED_RATIO_WARN = 0.05
# Dispersão relativa entre picos de repetições acima da qual a medida é instável.
PEAK_SPREAD_WARN = 0.10
# Crescimento por repetição acima do qual se assume fuga de memória.
LEAK_WARN_MIB = 32
# Activação acima deste múltiplo dos pesos sugere carregamento faseado.
STAGED_LOAD_FACTOR = 1.5
# Granularidade de arredondamento (para cima) dos valores emitidos.
DEFAULT_GRANULARITY_MIB = 64
# Piso da margem de segurança recomendada (alinha com vram_planner).
MIN_SAFETY_MIB = 384

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


def percentile(values: Sequence[float], pct: float) -> float:
    """Percentil por *nearest-rank* (sem numpy — o UMS não depende dele).

    Args:
        values: Amostras (não precisa de estar ordenada).
        pct: Percentil em ``[0, 100]``.

    Returns:
        O valor no rank correspondente; ``0.0`` para série vazia.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if pct <= 0:
        return float(ordered[0])
    if pct >= 100:
        return float(ordered[-1])
    rank = max(1, min(len(ordered), int(-(-len(ordered) * pct // 100))))
    return float(ordered[rank - 1])


def median(values: Sequence[float]) -> float:
    """Mediana (0.0 para série vazia)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def round_up_mib(value: float, granularity: int = DEFAULT_GRANULARITY_MIB) -> int:
    """Arredonda **para cima** ao múltiplo de ``granularity``.

    Subestimar footprint causa OOM; sobrestimar causa uma recusa. Arredondar
    sempre para cima escolhe o erro barato.
    """
    if granularity <= 0:
        return int(value if value == int(value) else int(value) + 1)
    if value <= 0:
        return 0
    steps = -(-round(value) // granularity)
    return steps * granularity


@dataclass(frozen=True)
class PhaseStats:
    """Estatística de uma janela de amostras."""

    label: str
    n: int
    min_mib: int
    max_mib: int
    p50_mib: int
    p95_mib: int
    max_gap_sec: float
    missed: int
    foreign_min_mib: int
    foreign_max_mib: int
    duration_sec: float

    def as_dict(self) -> dict[str, Any]:
        """Forma serializável (relatório JSON)."""
        return {
            "label": self.label,
            "n": self.n,
            "min_mib": self.min_mib,
            "max_mib": self.max_mib,
            "p50_mib": self.p50_mib,
            "p95_mib": self.p95_mib,
            "max_gap_sec": round(self.max_gap_sec, 4),
            "missed": self.missed,
            "foreign_min_mib": self.foreign_min_mib,
            "foreign_max_mib": self.foreign_max_mib,
            "duration_sec": round(self.duration_sec, 3),
        }


def summarize_window(samples: Sequence[Sample], label: str) -> PhaseStats:
    """Resume uma janela; janela vazia devolve estatística a zeros."""
    if not samples:
        return PhaseStats(
            label=label,
            n=0,
            min_mib=0,
            max_mib=0,
            p50_mib=0,
            p95_mib=0,
            max_gap_sec=0.0,
            missed=0,
            foreign_min_mib=0,
            foreign_max_mib=0,
            duration_sec=0.0,
        )
    values = [float(s.self_mib) for s in samples]
    foreign = [s.foreign_mib for s in samples]
    # O gap da primeira amostra da janela mede a distância à amostra anterior
    # (fora da janela) — conta na mesma: é tempo cego dentro desta fase.
    gaps = [s.gap_sec for s in samples]
    return PhaseStats(
        label=label,
        n=len(samples),
        min_mib=int(min(values)),
        max_mib=int(max(values)),
        p50_mib=int(median(values)),
        p95_mib=int(percentile(values, 95)),
        max_gap_sec=max(gaps),
        missed=sum(1 for s in samples if s.missed),
        foreign_min_mib=min(foreign),
        foreign_max_mib=max(foreign),
        duration_sec=samples[-1].t - samples[0].t,
    )


@dataclass
class PhaseWindows:
    """Janelas recolhidas pelo runner, por fase do ciclo.

    Attributes:
        baseline: Antes do spawn do worker (mede o ruído de terceiros).
        load: Durante ``CMD_LOAD`` (transiente de carregamento).
        loaded_settled: Após o ``ready``, com o modelo em repouso.
        generates: Uma janela por repetição, durante o ``generate``.
        settled: Uma janela por repetição, após o generate assentar.
        unloaded_settled: Após ``CMD_UNLOAD``, worker ainda vivo.
        post_shutdown: Após o worker sair (deteta VRAM órfã).
    """

    baseline: list[Sample] = field(default_factory=list)
    load: list[Sample] = field(default_factory=list)
    loaded_settled: list[Sample] = field(default_factory=list)
    generates: list[list[Sample]] = field(default_factory=list)
    settled: list[list[Sample]] = field(default_factory=list)
    unloaded_settled: list[Sample] = field(default_factory=list)
    post_shutdown: list[Sample] = field(default_factory=list)


@dataclass(frozen=True)
class Calibration:
    """Resultado completo de uma calibração (um backend, uma configuração de load)."""

    backend: str
    tool: str | None
    load_kwargs: dict[str, Any]
    quant_mode: str

    # Decomposição (MiB).
    context_mib: int
    resident_loaded_mib: int
    weights_mib: int
    activation_mib: int
    load_peak_mib: int
    generate_peak_mib: int
    peak_mib: int
    recommended_safety_mib: int

    # Sinais de saúde.
    fragmentation_mib: int
    leak_mib_per_run: float
    warmup_delta_mib: int
    orphan_mib: int
    staged_load_suspected: bool

    # Tempos.
    load_sec: float
    generate_sec: tuple[float, ...]
    generate_sec_median: float

    # Qualidade da medição.
    repeats: int
    samples_n: int
    max_gap_sec: float
    interval_sec: float
    missed_ratio: float
    probe_errors: int
    foreign_baseline_mib: int
    foreign_max_mib: int
    contaminated: bool
    confidence: str
    warnings: tuple[str, ...]

    # Contexto de hardware.
    gpu_name: str | None = None
    gpu_total_mib: int | None = None
    driver_version: str | None = None
    measured_at: str | None = None

    phases: dict[str, PhaseStats] = field(default_factory=dict)

    @property
    def weights_gib(self) -> float:
        """Pesos em GiB (2 casas, arredondado para cima)."""
        return _gib_up(self.weights_mib)

    @property
    def activation_gib(self) -> float:
        """Activação em GiB (2 casas, arredondado para cima)."""
        return _gib_up(self.activation_mib)

    @property
    def context_gib(self) -> float:
        """Contexto CUDA em GiB (2 casas, arredondado para cima)."""
        return _gib_up(self.context_mib)

    @property
    def peak_gib(self) -> float:
        """Pico total em GiB (2 casas, arredondado para cima)."""
        return _gib_up(self.peak_mib)

    @property
    def admit_peak_mib(self) -> int:
        """Pico que o UMS deve usar para admitir (com a margem recomendada)."""
        return self.peak_mib + self.recommended_safety_mib


def _gib_up(mib: int) -> float:
    """MiB → GiB arredondado para cima a 2 casas (nunca subestima)."""
    if mib <= 0:
        return 0.0
    return -(-int(mib) * 100 // 1024) / 100.0


def recommend_safety_mib(
    peaks: Sequence[int],
    *,
    fragmentation_mib: int = 0,
    floor_mib: int = MIN_SAFETY_MIB,
) -> int:
    """Margem de segurança a recomendar para este backend.

    Combina três fontes de erro que o pico medido não cobre:

    - **dispersão entre repetições** (seeds/prompts diferentes movem o pico) —
      usa-se a amplitude observada;
    - **cache do allocator** que não volta ao driver entre jobs (fragmentação);
    - um **piso** alinhado com :data:`modelserver.vram_planner.DEFAULT_VRAM_SAFETY_MIB`
      para contexto/compositor.

    Args:
        peaks: Picos por repetição (MiB).
        fragmentation_mib: Cache retida após o generate.
        floor_mib: Piso da margem.

    Returns:
        Margem em MiB, arredondada para cima a 64.
    """
    spread = (max(peaks) - min(peaks)) if peaks else 0
    return round_up_mib(max(floor_mib, spread, max(0, fragmentation_mib)))


def _slope_per_run(values: Sequence[float]) -> float:
    """Declive por repetição via regressão linear simples (0.0 se < 2 pontos)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def derive_calibration(
    *,
    backend: str,
    tool: str | None,
    load_kwargs: dict[str, Any] | None,
    quant_mode: str,
    windows: PhaseWindows,
    load_sec: float,
    generate_sec: Sequence[float],
    interval_sec: float,
    probe_errors: int = 0,
    gpu_name: str | None = None,
    gpu_total_mib: int | None = None,
    driver_version: str | None = None,
    measured_at: str | None = None,
) -> Calibration:
    """Resolve a decomposição e classifica a confiança da medição.

    Args:
        backend: Nome do backend calibrado.
        tool: Tool do monorepo (``None`` para backends externos).
        load_kwargs: Kwargs usados no ``load`` (entram no descriptor emitido).
        quant_mode: Modo de quantização em vigor (``none``/``sdnq-int4``/…).
        windows: Janelas por fase (:class:`PhaseWindows`).
        load_sec: Duração do ``CMD_LOAD``.
        generate_sec: Duração de cada repetição.
        interval_sec: Intervalo alvo do amostrador (para avaliar gaps).
        probe_errors: Exceções do probe durante a corrida.
        gpu_name: Nome da GPU (contexto do relatório).
        gpu_total_mib: VRAM total do dispositivo.
        driver_version: Versão do driver.
        measured_at: Timestamp ISO da medição.

    Returns:
        :class:`Calibration` com números, sinais de saúde e avisos.
    """
    phases: dict[str, PhaseStats] = {
        "baseline": summarize_window(windows.baseline, "baseline"),
        "load": summarize_window(windows.load, "load"),
        "loaded_settled": summarize_window(windows.loaded_settled, "loaded_settled"),
        "unloaded_settled": summarize_window(windows.unloaded_settled, "unloaded_settled"),
        "post_shutdown": summarize_window(windows.post_shutdown, "post_shutdown"),
    }
    gen_stats = [summarize_window(w, f"generate_{i + 1}") for i, w in enumerate(windows.generates)]
    settled_stats = [summarize_window(w, f"settled_{i + 1}") for i, w in enumerate(windows.settled)]
    for stat in (*gen_stats, *settled_stats):
        phases[stat.label] = stat

    warnings: list[str] = []

    # --- Decomposição ---------------------------------------------------
    # Contexto: o que fica no processo depois do unload. Se não houver janela
    # de unload (corrida interrompida), assume-se 0 e avisa-se — nesse caso
    # "pesos" incluem o contexto e o pico continua correto.
    context_mib = phases["unloaded_settled"].p50_mib
    if phases["unloaded_settled"].n == 0:
        warnings.append("sem janela pós-unload: contexto CUDA não isolado (incluído nos pesos)")

    resident_loaded = phases["loaded_settled"].p50_mib
    if phases["loaded_settled"].n == 0:
        warnings.append("sem janela pós-load: pesos residentes desconhecidos")
    if context_mib > resident_loaded and resident_loaded > 0:
        # unload que liberta menos do que os pesos ocupavam, ou medição suja.
        warnings.append(
            f"residual pós-unload ({context_mib} MiB) ≥ residente com modelo ({resident_loaded} MiB): "
            "unload incompleto ou contaminação"
        )
        context_mib = min(context_mib, resident_loaded)

    weights_mib = max(0, resident_loaded - context_mib)
    load_peak = phases["load"].max_mib
    generate_peak = max((s.max_mib for s in gen_stats), default=0)
    activation_mib = max(0, generate_peak - resident_loaded)
    peak = max(load_peak, generate_peak, resident_loaded)

    if load_peak > generate_peak and load_peak > 0:
        warnings.append(
            f"pico no load ({load_peak} MiB) acima do pico de inferência ({generate_peak} MiB): "
            "admitir só pela inferência causaria OOM ao carregar"
        )

    staged = bool(weights_mib > 0 and activation_mib > STAGED_LOAD_FACTOR * weights_mib)
    if staged:
        warnings.append(
            f"activação ({activation_mib} MiB) > {STAGED_LOAD_FACTOR}x pesos ({weights_mib} MiB): "
            "provável carregamento faseado dentro do generate (peso não residente)"
        )

    # --- Saúde ----------------------------------------------------------
    settled_p50 = [float(s.p50_mib) for s in settled_stats if s.n]
    fragmentation = int(max(0.0, (settled_p50[0] - resident_loaded))) if settled_p50 else 0
    leak = _slope_per_run(settled_p50)
    if leak > LEAK_WARN_MIB:
        warnings.append(f"residente cresce ~{leak:.0f} MiB por repetição: provável fuga de VRAM")

    peaks = [s.max_mib for s in gen_stats if s.n]
    warmup_delta = 0
    if len(peaks) >= 2:
        warmup_delta = peaks[0] - max(peaks[1:])
        if warmup_delta > 0:
            warnings.append(f"primeira repetição com +{warmup_delta} MiB de pico (warmup): excluída da estabilidade")

    orphan = phases["post_shutdown"].p50_mib
    if orphan > 0:
        warnings.append(f"{orphan} MiB ainda atribuídos ao processo após shutdown: worker órfão?")

    # --- Qualidade da medição -------------------------------------------
    all_samples: list[Sample] = [
        *windows.baseline,
        *windows.load,
        *windows.loaded_settled,
        *[s for w in windows.generates for s in w],
        *[s for w in windows.settled for s in w],
        *windows.unloaded_settled,
        *windows.post_shutdown,
    ]
    samples_n = len(all_samples)
    max_gap = max((s.gap_sec for s in all_samples), default=0.0)
    tracked = [s for s in all_samples if s.tracked_pids > 0]
    missed_ratio = (sum(1 for s in tracked if s.missed) / len(tracked)) if tracked else 0.0

    foreign_baseline = int(median([float(s.foreign_mib) for s in windows.baseline])) if windows.baseline else 0
    measured_foreign = [s.foreign_mib for w in (windows.load, windows.loaded_settled, *windows.generates) for s in w]
    foreign_max = max(measured_foreign, default=foreign_baseline)
    contaminated = (foreign_max - foreign_baseline) > CONTAMINATION_MIB
    if contaminated:
        warnings.append(
            f"VRAM de terceiros variou {foreign_max - foreign_baseline} MiB durante a medição "
            f"(baseline {foreign_baseline} → {foreign_max}): pico possivelmente enviesado"
        )

    if max_gap > GAP_FACTOR_WARN * interval_sec:
        warnings.append(
            f"maior intervalo entre amostras {max_gap:.2f}s (alvo {interval_sec:.2f}s): pico pode ter escapado"
        )
    if missed_ratio > MISSED_RATIO_WARN:
        warnings.append(f"{missed_ratio:.0%} das amostras sem dados do driver para o worker")
    if probe_errors:
        warnings.append(f"{probe_errors} falhas do probe NVML durante a corrida")

    stable_peaks = peaks[1:] if len(peaks) >= 2 else peaks
    spread_ratio = 0.0
    if len(stable_peaks) >= 2:
        mean_peak = sum(stable_peaks) / len(stable_peaks)
        if mean_peak > 0:
            spread_ratio = (max(stable_peaks) - min(stable_peaks)) / mean_peak
        if spread_ratio > PEAK_SPREAD_WARN:
            warnings.append(f"picos entre repetições variam {spread_ratio:.0%}: medida instável (mais repetições?)")
    if len(peaks) < 2:
        warnings.append("apenas 1 repetição: warmup e dispersão não separados (--repeats 3 recomendado)")

    confidence = _confidence(
        contaminated=contaminated,
        missed_ratio=missed_ratio,
        max_gap=max_gap,
        interval_sec=interval_sec,
        repeats=len(peaks),
        spread_ratio=spread_ratio,
        has_load_window=phases["load"].n > 0,
        has_unload_window=phases["unloaded_settled"].n > 0,
    )

    gen_list = [float(v) for v in generate_sec]
    return Calibration(
        backend=backend,
        tool=tool,
        load_kwargs=dict(load_kwargs or {}),
        quant_mode=quant_mode,
        context_mib=context_mib,
        resident_loaded_mib=resident_loaded,
        weights_mib=weights_mib,
        activation_mib=activation_mib,
        load_peak_mib=load_peak,
        generate_peak_mib=generate_peak,
        peak_mib=peak,
        recommended_safety_mib=recommend_safety_mib(peaks, fragmentation_mib=fragmentation),
        fragmentation_mib=fragmentation,
        leak_mib_per_run=round(leak, 2),
        warmup_delta_mib=max(0, warmup_delta),
        orphan_mib=orphan,
        staged_load_suspected=staged,
        load_sec=round(float(load_sec), 3),
        generate_sec=tuple(round(v, 3) for v in gen_list),
        generate_sec_median=round(median(gen_list), 3),
        repeats=len(gen_list),
        samples_n=samples_n,
        max_gap_sec=round(max_gap, 4),
        interval_sec=interval_sec,
        missed_ratio=round(missed_ratio, 4),
        probe_errors=probe_errors,
        foreign_baseline_mib=foreign_baseline,
        foreign_max_mib=foreign_max,
        contaminated=contaminated,
        confidence=confidence,
        warnings=tuple(warnings),
        gpu_name=gpu_name,
        gpu_total_mib=gpu_total_mib,
        driver_version=driver_version,
        measured_at=measured_at,
        phases=phases,
    )


def _confidence(
    *,
    contaminated: bool,
    missed_ratio: float,
    max_gap: float,
    interval_sec: float,
    repeats: int,
    spread_ratio: float,
    has_load_window: bool,
    has_unload_window: bool,
) -> str:
    """Classifica a medição em ``high``/``medium``/``low``.

    ``low`` para defeitos que invalidam o número (contaminação, cegueira do
    probe); ``medium`` para os que só o tornam impreciso (1 repetição,
    dispersão, contexto não isolado).
    """
    if contaminated or missed_ratio > MISSED_RATIO_WARN or max_gap > GAP_FACTOR_WARN * interval_sec:
        return CONFIDENCE_LOW
    if not has_load_window or not has_unload_window:
        return CONFIDENCE_MEDIUM
    if repeats < 2 or spread_ratio > PEAK_SPREAD_WARN:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH
