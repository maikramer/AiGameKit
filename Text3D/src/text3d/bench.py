"""Bench de calibração do decode Omni (octree x decoder x mc_level x bounds).

Motivação: octree acima da capacidade do latent não acrescenta detalhe — só
ruído interno (componentes soltos dentro da shell) e VRAM/tempo. Este bench
corre a matriz de knobs sobre a MESMA imagem + seed e mede, por caso:

- métricas de mesh (``utils.mesh_metrics``): boundary edges, componentes
  internos, volume de lixo, watertight;
- pico de VRAM e tempo de decode.

O relatório alimenta as constantes de tuning (``LATENT_DETAIL_CEILING``,
``bytes_per_query`` dos chunks dinâmicos, mc_level auto). As funções de
matriz/agregação são puras — testáveis em CI sem GPU; só ``run_bench_decode``
precisa de CUDA.

Uso típico::

    text3d bench-decode --image ref.png --octrees 256,384,448,512 \
        --decoders vanilla,flashvdm --bbox-preset building -o bench_out/
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aigamekit_shared.logging import Logger

_logger = Logger()

VALID_DECODERS = ("vanilla", "hierarchical", "flashvdm")
VALID_BOUNDS_MODES = ("cube", "auto")

# Limiar de lixo interno aceitável (fracção do volume da shell principal).
DEFAULT_MAX_INTERNAL_RATIO = 0.02
# Limiar de crescimento de boundary edges vs o octree anterior (buracos).
DEFAULT_MAX_BOUNDARY_GROWTH = 2.0


@dataclass(frozen=True)
class BenchCase:
    """Uma célula da matriz de calibração."""

    octree: int
    decoder: str
    mc_level: float | str = "auto"  # float explícito ou "auto"
    bounds_mode: str = "cube"

    @property
    def case_id(self) -> str:
        mc = self.mc_level if isinstance(self.mc_level, str) else f"{self.mc_level:g}"
        return f"o{self.octree}_{self.decoder}_mc{mc}_{self.bounds_mode}"


def parse_mc_levels(raw: str) -> list[float | str]:
    """CSV de mc_levels: floats ou ``auto``."""
    out: list[float | str] = []
    for tok in str(raw).split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.lower() == "auto":
            out.append("auto")
        else:
            out.append(float(tok))
    if not out:
        raise ValueError("mc_levels vazio")
    return out


def build_matrix(
    octrees: list[int],
    decoders: list[str],
    mc_levels: list[float | str] | None = None,
    bounds_modes: list[str] | None = None,
) -> list[BenchCase]:
    """Produto cartesiano validado dos knobs (puro, ordenado por decoder→octree)."""
    mc_levels = mc_levels or ["auto"]
    bounds_modes = bounds_modes or ["cube"]
    for d in decoders:
        if d not in VALID_DECODERS:
            raise ValueError(f"decoder inválido: {d!r} (válidos: {VALID_DECODERS})")
    for b in bounds_modes:
        if b not in VALID_BOUNDS_MODES:
            raise ValueError(f"bounds_mode inválido: {b!r} (válidos: {VALID_BOUNDS_MODES})")
    for o in octrees:
        if int(o) < 64:
            raise ValueError(f"octree inválido: {o}")
    cases: list[BenchCase] = []
    # Decoder no loop externo: o runner recarrega o pipeline por decoder.
    for d in decoders:
        for b in bounds_modes:
            for mc in mc_levels:
                for o in sorted(int(x) for x in octrees):
                    # flashvdm exige octree ≥ 256 (extract_geometry_fast_v2).
                    if d == "flashvdm" and int(o) < 256:
                        continue
                    cases.append(BenchCase(octree=int(o), decoder=d, mc_level=mc, bounds_mode=b))
    return cases


def _junk_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Métricas de lixo PRE-drop (bench) com fallback às pós-drop."""
    pre = row.get("pre_drop") or {}
    post = row.get("metrics") or {}
    if "internal_volume_ratio" in pre:
        return {
            "internal_volume_ratio": float(pre.get("internal_volume_ratio", 0.0)),
            "boundary_edges": int(post.get("boundary_edges", 0)),
            "n_internal": int(pre.get("n_internal", 0)),
        }
    return {
        "internal_volume_ratio": float(post.get("internal_volume_ratio", 0.0)),
        "boundary_edges": int(post.get("boundary_edges", 0)),
        "n_internal": int(post.get("internal_components", 0)),
    }


def recommend_latent_ceiling(
    results: list[dict[str, Any]],
    *,
    max_internal_ratio: float = DEFAULT_MAX_INTERNAL_RATIO,
    max_boundary_growth: float = DEFAULT_MAX_BOUNDARY_GROWTH,
) -> int | None:
    """Maior octree ainda «limpo» — candidato a ``LATENT_DETAIL_CEILING``.

    Critério (por série decoder+mc+bounds, agregado pelo pior caso), usando
    lixo **PRE-drop** quando disponível (pós-drop zera internals e cega o
    tecto):
    - ``internal_volume_ratio`` ≤ ``max_internal_ratio``;
    - ``boundary_edges`` (pós-drop) não cresce mais de ``max_boundary_growth``x
      vs o octree imediatamente abaixo na mesma série.

    Devolve ``None`` sem resultados válidos.
    """
    series: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in results:
        m = _junk_metrics(r)
        if "internal_volume_ratio" not in m:
            continue
        key = (str(r.get("decoder")), str(r.get("mc_level")), str(r.get("bounds_mode")))
        series.setdefault(key, []).append(r)

    clean_by_octree: dict[int, bool] = {}
    for rows in series.values():
        rows.sort(key=lambda r: int(r["octree"]))
        prev_boundary: int | None = None
        for r in rows:
            o = int(r["octree"])
            m = _junk_metrics(r)
            ok = float(m.get("internal_volume_ratio", 0.0)) <= max_internal_ratio
            b = int(m.get("boundary_edges", 0))
            if prev_boundary is not None and prev_boundary > 0 and b / prev_boundary > max_boundary_growth:
                ok = False
            prev_boundary = b
            clean_by_octree[o] = clean_by_octree.get(o, True) and ok

    clean = [o for o, ok in clean_by_octree.items() if ok]
    return max(clean) if clean else (min(clean_by_octree) if clean_by_octree else None)


def summarize_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Agregado do bench (puro): recomendação de tecto + piores casos."""
    ceiling = recommend_latent_ceiling(results)
    worst_internal = max(
        results,
        key=lambda r: float(_junk_metrics(r).get("internal_volume_ratio", 0.0)),
        default=None,
    )
    worst_boundary = max(
        results,
        key=lambda r: int((r.get("metrics") or {}).get("boundary_edges", 0)),
        default=None,
    )
    return {
        "cases": len(results),
        "recommended_latent_ceiling": ceiling,
        "worst_internal_case": worst_internal.get("case_id") if worst_internal else None,
        "worst_boundary_case": worst_boundary.get("case_id") if worst_boundary else None,
    }


def run_bench_decode(
    image: str | Path,
    cases: list[BenchCase],
    out_dir: str | Path,
    *,
    steps: int = 30,
    guidance: float = 5.0,
    seed: int = 1234,
    num_chunks: int | None = None,
    sdnq_preset: str = "",
    control_type: str | None = None,
    bbox: list[float] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Corre a matriz na MESMA imagem+seed e grava GLB + relatório JSON.

    Reusa o pipeline carregado entre casos do mesmo decoder (``keep_loaded``).
    Requer CUDA para medições de VRAM (CPU corre mas sem pico de VRAM).
    """
    import torch

    from .generator import HunyuanTextTo3DGenerator
    from .utils.mesh_metrics import mesh_quality_metrics

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    image = str(image)

    results: list[dict[str, Any]] = []
    by_decoder: dict[str, list[BenchCase]] = {}
    for c in cases:
        by_decoder.setdefault(c.decoder, []).append(c)

    for decoder, decoder_cases in by_decoder.items():
        gen = HunyuanTextTo3DGenerator(
            verbose=verbose,
            sdnq_preset=sdnq_preset,
            volume_decoder=decoder,
        )
        try:
            for i_case, case in enumerate(decoder_cases, start=1):
                if verbose:
                    _logger.info(
                        f"bench [{i_case}/{len(decoder_cases)}] {case.case_id} "
                        f"octree={case.octree} decoder={case.decoder} "
                        f"mc={case.mc_level} bounds={case.bounds_mode}"
                    )
                cuda = torch.cuda.is_available()
                if cuda:
                    torch.cuda.empty_cache()
                    torch.cuda.reset_peak_memory_stats()
                t0 = time.time()
                error: str | None = None
                mesh = None
                try:
                    mesh = gen.generate_from_image(
                        image,
                        num_inference_steps=steps,
                        guidance_scale=guidance,
                        octree_resolution=case.octree,
                        hy_seed=seed,
                        mc_level=case.mc_level,
                        bounds_mode=case.bounds_mode,
                        keep_loaded=True,
                        control_type=control_type,
                        bbox=bbox,
                        **({"num_chunks": num_chunks} if num_chunks is not None else {}),
                    )
                except Exception as exc:  # bench continua nos restantes casos
                    error = f"{type(exc).__name__}: {exc}"
                    _logger.warn(f"bench {case.case_id}: {error}")
                elapsed = time.time() - t0
                peak_gib = (torch.cuda.max_memory_allocated() / (1024**3)) if cuda else 0.0

                row: dict[str, Any] = {
                    **asdict(case),
                    "case_id": case.case_id,
                    "seconds": round(elapsed, 2),
                    "peak_vram_gib": round(peak_gib, 3),
                    "error": error,
                    "metrics": None,
                    "glb": None,
                }
                if mesh is not None and len(mesh.faces) > 0:
                    row["pre_drop"] = dict(getattr(gen, "last_decode_stats", {}) or {})
                    row["metrics"] = mesh_quality_metrics(mesh)
                    glb_path = out / f"{case.case_id}.glb"
                    try:
                        mesh.export(str(glb_path))
                        row["glb"] = str(glb_path)
                    except Exception as exc:
                        _logger.warn(f"bench {case.case_id}: export falhou ({exc})")
                results.append(row)
                if verbose:
                    m = row["metrics"] or {}
                    pre = row.get("pre_drop") or {}
                    _logger.info(
                        f"bench {case.case_id}: {row['seconds']}s vram={row['peak_vram_gib']}GiB "
                        f"faces={m.get('faces')} boundary={m.get('boundary_edges')} "
                        f"internos_pos={m.get('internal_components')} "
                        f"pre_int={pre.get('n_internal', '?')} "
                        f"(pre_vol={pre.get('internal_volume_ratio', 0):.4f})"
                    )
        finally:
            gen.unload()

    report = {
        "image": image,
        "seed": seed,
        "steps": steps,
        "guidance": guidance,
        "control_type": control_type,
        "bbox": bbox,
        "results": results,
        "summary": summarize_report(results),
    }
    report_path = out / "bench_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _logger.info(f"Relatório: {report_path}")
    return report
