"""Medido vs declarado — quanto é que os números escritos à mão erram.

Duas formas de errar, com custos diferentes:

- **subdimensionado** (declarado < medido): o UMS admite um job que não cabe →
  OOM a meio, com o custo de já ter carregado os pesos;
- **sobredimensionado** (declarado > medido): o UMS recusa (ou evicta um
  vizinho) sem necessidade → throughput perdido numa GPU que tinha espaço.

O comparador não decide qual é pior; reporta ambos com o desvio e a folga em
MiB para que a decisão seja do operador.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..vram_planner import peak_vram_mib as compute_peak_mib
from .analysis import Calibration

# Desvio relativo tolerado antes de marcar drift.
DEFAULT_TOLERANCE = 0.10

VERDICT_OK = "ok"
VERDICT_UNDER = "under"  # declarado abaixo do medido → risco de OOM
VERDICT_OVER = "over"  # declarado acima do medido → recusas desnecessárias
VERDICT_UNKNOWN = "unknown"


def verdict_for(declared: int | None, measured: int, *, tolerance: float = DEFAULT_TOLERANCE) -> str:
    """Classifica o desvio de um valor declarado face ao medido.

    Args:
        declared: Valor escrito no YAML/registry (``None`` = não declarado).
        measured: Valor medido.
        tolerance: Desvio relativo aceite em ambos os sentidos.

    Returns:
        ``ok`` | ``under`` | ``over`` | ``unknown``.
    """
    if declared is None:
        return VERDICT_UNKNOWN
    if measured <= 0:
        return VERDICT_UNKNOWN if declared > 0 else VERDICT_OK
    ratio = declared / measured
    if ratio < 1.0 - tolerance:
        return VERDICT_UNDER
    if ratio > 1.0 + tolerance:
        return VERDICT_OVER
    return VERDICT_OK


@dataclass(frozen=True)
class ComparisonRow:
    """Uma métrica comparada."""

    backend: str
    metric: str
    declared_mib: int | None
    measured_mib: int
    verdict: str
    note: str = ""

    @property
    def delta_mib(self) -> int | None:
        """``declarado - medido`` (positivo = folga a mais)."""
        if self.declared_mib is None:
            return None
        return self.declared_mib - self.measured_mib

    @property
    def ratio(self) -> float | None:
        """``declarado / medido`` (``None`` se indeterminado)."""
        if self.declared_mib is None or self.measured_mib <= 0:
            return None
        return round(self.declared_mib / self.measured_mib, 3)

    def as_dict(self) -> dict[str, Any]:
        """Forma serializável."""
        return {
            "backend": self.backend,
            "metric": self.metric,
            "declared_mib": self.declared_mib,
            "measured_mib": self.measured_mib,
            "delta_mib": self.delta_mib,
            "ratio": self.ratio,
            "verdict": self.verdict,
            "note": self.note,
        }


def compare_to_declared(
    cal: Calibration,
    *,
    declared_weights_mib: int | None,
    declared_activation_mib: int | None,
    declared_vram_mib: int | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[ComparisonRow]:
    """Compara pesos, activação e pico de admissão.

    O pico declarado é reconstruído com a mesma fórmula do admit
    (:func:`modelserver.vram_planner.peak_vram_mib`) — comparar pesos isolados
    esconde o erro que interessa, que é o da soma.

    Args:
        cal: Calibração medida.
        declared_weights_mib: Pesos declarados (do footprint/YAML).
        declared_activation_mib: Activação declarada.
        declared_vram_mib: ``vram_mib`` estático do descriptor, se existir.
        tolerance: Desvio relativo aceite.

    Returns:
        Linhas por métrica, na ordem: pesos, activação, pico de admissão,
        e ``vram_mib`` quando declarado.
    """
    rows: list[ComparisonRow] = [
        ComparisonRow(
            backend=cal.backend,
            metric="weights_mib",
            declared_mib=declared_weights_mib,
            measured_mib=cal.weights_mib,
            verdict=verdict_for(declared_weights_mib, cal.weights_mib, tolerance=tolerance),
            note="pesos residentes, já sem contexto CUDA",
        ),
        ComparisonRow(
            backend=cal.backend,
            metric="activation_mib",
            declared_mib=declared_activation_mib,
            measured_mib=cal.activation_mib,
            verdict=verdict_for(declared_activation_mib, cal.activation_mib, tolerance=tolerance),
            note="staged load: activação inclui pesos carregados no generate"
            if cal.staged_load_suspected
            else "subida máxima acima do residente",
        ),
    ]

    declared_peak = None
    if declared_weights_mib is not None and declared_activation_mib is not None:
        declared_peak = compute_peak_mib(declared_weights_mib, declared_activation_mib)
    # O medido inclui o contexto CUDA (que o footprint declarado ignora) — é
    # ele que o driver cobra, por isso entra na comparação de admissão.
    measured_admit = cal.admit_peak_mib
    rows.append(
        ComparisonRow(
            backend=cal.backend,
            metric="admit_peak_mib",
            declared_mib=declared_peak,
            measured_mib=measured_admit,
            verdict=verdict_for(declared_peak, measured_admit, tolerance=tolerance),
            note="pesos + activação + safety, como no admit",
        )
    )

    if declared_vram_mib is not None:
        rows.append(
            ComparisonRow(
                backend=cal.backend,
                metric="vram_mib",
                declared_mib=declared_vram_mib,
                measured_mib=cal.peak_mib,
                verdict=verdict_for(declared_vram_mib, cal.peak_mib, tolerance=tolerance),
                note="valor estático do backends.yaml vs pico medido",
            )
        )
    return rows


def declared_parts_from_registry(
    backend: str,
    *,
    registry: Any = None,
    quant_mode: str = "none",
    memory_efficient: bool = False,
    group_offload: bool = False,
    footprint_key: str | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Lê ``(pesos, activação, vram_mib)`` declarados, pela via do próprio UMS.

    Reutiliza :meth:`modelserver.backend_manager.BackendManager.footprint_parts_mib`
    de propósito: comparar contra uma reimplementação da fórmula compararia com
    a fórmula errada assim que uma das duas mudasse.

    Returns:
        Tuplo com ``None`` nas posições que não foi possível determinar.
    """
    from ..backend_manager import BackendManager
    from ..registry import Registry

    reg = registry if registry is not None else Registry()
    try:
        desc = reg.descriptor(backend)
    except KeyError:
        return (None, None, None)
    manager = BackendManager(reg)
    try:
        weights, activation = manager.footprint_parts_mib(
            backend,
            quant_mode=quant_mode,
            memory_efficient=memory_efficient,
            group_offload=group_offload,
            footprint_key=footprint_key,
        )
    except Exception:
        return (None, None, int(desc.vram_mib))
    return (int(weights), int(activation), int(desc.vram_mib))


def summarize_verdicts(rows: Sequence[ComparisonRow]) -> dict[str, int]:
    """Contagem por veredicto (para exit codes / resumo de CLI)."""
    out = {VERDICT_OK: 0, VERDICT_UNDER: 0, VERDICT_OVER: 0, VERDICT_UNKNOWN: 0}
    for row in rows:
        out[row.verdict] = out.get(row.verdict, 0) + 1
    return out
