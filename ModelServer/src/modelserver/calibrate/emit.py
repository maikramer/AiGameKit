"""Emissão do descriptor calibrado (YAML v2) e do relatório completo (JSON).

O YAML gerado é **retrocompatível com o loader atual**
(:func:`modelserver.registry.load_descriptors`): mantém ``adapter`` e
``vram_mib`` no topo da entrada, e acrescenta os blocos novos (``vram:``,
``peak_profile:``, ``measured:``) que o loader v1 ignora. Assim o ficheiro
emitido pode substituir o ``data/backends.yaml`` antes de F1/F2 existirem.

Escolha deliberada: ``vram_mib`` emitido é o **pico medido** (pesos + contexto +
activação), não só os pesos — é o número que o admit compara com a VRAM livre.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import yaml

from .analysis import Calibration, round_up_mib

_HEADER = """\
# Descriptor de backends gerado por `ums calibrate` — NÃO editar à mão.
#
# Os valores em `vram:` são MEDIDOS (driver, por processo), não estimados:
#   weights_gib     pesos residentes após load, já sem o contexto CUDA
#   activation_gib  subida máxima acima do residente durante a inferência
#   context_gib     contexto CUDA + o que o unload não devolve ao driver
#   peak_mib        max(pico do load, pico da inferência) — o que o admit usa
#   safety_mib      margem recomendada (dispersão entre repetições + fragmentação)
#
# `measured:` guarda as condições da medição. Um descriptor medido noutra GPU
# ou com outros kwargs de load NÃO é transferível — recalibrar.
"""


def calibration_to_descriptor(
    cal: Calibration,
    *,
    adapter: str | None = None,
    priority: int = 0,
    footprint_key: str | None = None,
    runtime: dict[str, Any] | None = None,
    load_keys: Sequence[str] | None = None,
    shape_keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Constrói a entrada YAML de um backend a partir da calibração.

    Args:
        cal: Resultado da calibração.
        adapter: Dotted path do adapter (compat v1). Se ``None``, derivado do nome.
        priority: Prioridade de evicção a preservar do descriptor atual.
        footprint_key: Chave de footprint declarada (mantida para rastreio).
        runtime: Bloco ``runtime:`` v2. Se ``None``, usa ``monorepo_tool`` quando
            a calibração conhece a tool.
        load_keys: Kwargs que influenciam o load (substitui a allowlist global).
        shape_keys: Subconjunto que força reload quando muda.

    Returns:
        Dict pronto para ``yaml.safe_dump``, com as chaves v1 primeiro.
    """
    entry: dict[str, Any] = {
        "name": cal.backend,
        "adapter": adapter or f"modelserver.adapters.{cal.backend}",
        # Compat v1: o loader atual exige esta chave e usa-a como pico estático.
        "vram_mib": round_up_mib(cal.peak_mib),
        "priority": int(priority),
    }
    if cal.tool:
        entry["tool"] = cal.tool
    if footprint_key:
        entry["footprint_key"] = footprint_key

    entry["runtime"] = dict(runtime) if runtime else ({"monorepo_tool": cal.tool} if cal.tool else {})

    entry["vram"] = {
        "weights_gib": cal.weights_gib,
        "activation_gib": cal.activation_gib,
        "context_gib": cal.context_gib,
        "peak_mib": round_up_mib(cal.peak_mib),
        "safety_mib": cal.recommended_safety_mib,
        "admit_peak_mib": round_up_mib(cal.admit_peak_mib),
    }

    profile: dict[str, Any] = {"quant_mode": cal.quant_mode}
    if cal.staged_load_suspected:
        profile["staged_load"] = True
    if cal.load_peak_mib > cal.generate_peak_mib:
        profile["load_bound"] = True
    if cal.load_kwargs:
        profile["load_kwargs"] = dict(cal.load_kwargs)
    entry["peak_profile"] = profile

    if load_keys:
        entry["load_keys"] = sorted(set(load_keys))
    if shape_keys:
        entry["shape_keys"] = sorted(set(shape_keys))

    entry["measured"] = _measured_block(cal)
    return entry


def _measured_block(cal: Calibration) -> dict[str, Any]:
    """Bloco ``measured:`` — condições e qualidade da medição."""
    block: dict[str, Any] = {
        "confidence": cal.confidence,
        "repeats": cal.repeats,
        "samples": cal.samples_n,
        "load_sec": cal.load_sec,
        "generate_sec_median": cal.generate_sec_median,
    }
    if cal.gpu_name:
        block["gpu"] = cal.gpu_name
    if cal.gpu_total_mib:
        block["gpu_total_mib"] = cal.gpu_total_mib
    if cal.driver_version:
        block["driver"] = cal.driver_version
    if cal.measured_at:
        block["at"] = cal.measured_at
    # Sinais que mudam decisões (fragmentação, fuga, warmup) só aparecem quando
    # não são zero — um YAML cheio de zeros esconde os que importam.
    for key, value in (
        ("fragmentation_mib", cal.fragmentation_mib),
        ("leak_mib_per_run", cal.leak_mib_per_run),
        ("warmup_delta_mib", cal.warmup_delta_mib),
        ("orphan_mib", cal.orphan_mib),
    ):
        if value:
            block[key] = value
    if cal.warnings:
        block["warnings"] = list(cal.warnings)
    return block


def calibration_to_yaml(
    calibrations: Sequence[Calibration] | Calibration,
    *,
    descriptors: dict[str, dict[str, Any]] | None = None,
    header: bool = True,
) -> str:
    """Serializa uma ou mais calibrações num documento ``backends.yaml`` v2.

    Args:
        calibrations: Uma calibração ou várias (uma por backend/variante).
        descriptors: Metadados por backend a preservar
            (``adapter``/``priority``/``footprint_key``/``runtime``/``load_keys``/
            ``shape_keys``). Normalmente vem do registry atual.
        header: Incluir o cabeçalho explicativo.

    Returns:
        Documento YAML.
    """
    items = [calibrations] if isinstance(calibrations, Calibration) else list(calibrations)
    meta = descriptors or {}
    entries = [calibration_to_descriptor(cal, **meta.get(cal.backend, {})) for cal in items]
    doc = {"version": 2, "backends": entries}
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)
    return (_HEADER + body) if header else body


def calibration_to_report(cal: Calibration, *, include_phases: bool = True) -> dict[str, Any]:
    """Relatório JSON completo (tudo o que foi medido, sem arredondar).

    O YAML é o que o UMS consome; isto é o que se guarda para auditar uma
    medição depois — inclui a estatística por fase.
    """
    report: dict[str, Any] = {
        "backend": cal.backend,
        "tool": cal.tool,
        "quant_mode": cal.quant_mode,
        "load_kwargs": dict(cal.load_kwargs),
        "vram_mib": {
            "context": cal.context_mib,
            "resident_loaded": cal.resident_loaded_mib,
            "weights": cal.weights_mib,
            "activation": cal.activation_mib,
            "load_peak": cal.load_peak_mib,
            "generate_peak": cal.generate_peak_mib,
            "peak": cal.peak_mib,
            "recommended_safety": cal.recommended_safety_mib,
            "admit_peak": cal.admit_peak_mib,
        },
        "health": {
            "fragmentation_mib": cal.fragmentation_mib,
            "leak_mib_per_run": cal.leak_mib_per_run,
            "warmup_delta_mib": cal.warmup_delta_mib,
            "orphan_mib": cal.orphan_mib,
            "staged_load_suspected": cal.staged_load_suspected,
        },
        "timing": {
            "load_sec": cal.load_sec,
            "generate_sec": list(cal.generate_sec),
            "generate_sec_median": cal.generate_sec_median,
        },
        "quality": {
            "confidence": cal.confidence,
            "repeats": cal.repeats,
            "samples": cal.samples_n,
            "interval_sec": cal.interval_sec,
            "max_gap_sec": cal.max_gap_sec,
            "missed_ratio": cal.missed_ratio,
            "probe_errors": cal.probe_errors,
            "foreign_baseline_mib": cal.foreign_baseline_mib,
            "foreign_max_mib": cal.foreign_max_mib,
            "contaminated": cal.contaminated,
            "warnings": list(cal.warnings),
        },
        "hardware": {
            "gpu": cal.gpu_name,
            "gpu_total_mib": cal.gpu_total_mib,
            "driver": cal.driver_version,
            "measured_at": cal.measured_at,
        },
    }
    if include_phases:
        report["phases"] = {name: stat.as_dict() for name, stat in cal.phases.items()}
    return report
