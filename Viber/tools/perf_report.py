#!/usr/bin/env python3
"""Relatório do A/B de performance gerado por tools/perf_ab.sh.

Uso: tools/perf_report.py [dir] [--systems nome1,nome2,...]

Lê os JSONs de `viber debug prof --samples --json` (um por braço/ronda) e
imprime a mediana de `frame_ms_avg`/`fps_avg` por braço, mais o delta. Com
`--systems`, mostra também o `avg_ms` por sistema escolhido (o campo `last` do
JSON traz o snapshot completo do profiler, incluindo os sistemas embrulhados em
`timed`).

Cite a MEDIANA, não a média de uma amostra: numa máquina com engines de agentes
paralelos o frame oscila com a contensão da GPU e a mediana é o que sobrevive.
"""
import argparse
import glob
import json
import os
import statistics
import sys


def load_arms(directory, phase="idle"):
    arms = {}
    for path in sorted(glob.glob(os.path.join(directory, "*-r*.json"))):
        base = os.path.basename(path)[:-5]          # arm0-r1-idle
        stem, _, tag = base.rpartition("-")
        if phase != "all" and tag != phase:
            continue
        arm, _, rnd = stem.rpartition("-r")
        if not rnd.isdigit():
            continue
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            print(f"  ! {base}: {error}", file=sys.stderr)
            continue
        arms.setdefault(arm, []).append(data)
    return arms


def median_of(rows, getter):
    values = [v for v in (getter(r) for r in rows) if isinstance(v, (int, float))]
    return statistics.median(values) if values else None


def system_avg(row, name):
    """`avg_ms` do sistema (o `max_ms` lê-se com `system_ms`)."""
    got = system_ms(row, name)
    return got[0] if got else None


def system_ms(row, name):
    """`(avg_ms, max_ms)` de um sistema. O `timed` guarda o nome canónico
    (`viber::particles::particle_emitter_update`), portanto compara-se por
    SUFIXO — escrever o caminho todo no CLI é ruído desnecessário."""
    systems = (row.get("last") or {}).get("systems") or []
    for entry in systems:
        if (entry.get("name") or "").endswith(name):
            return entry.get("avg_ms"), entry.get("max_ms")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "viber-perf-ab"))
    parser.add_argument("--systems", default="")
    parser.add_argument("--phase", default="idle",
                        help="idle | stress | all — o sufixo do JSON escrito pelo perf_ab.sh")
    parser.add_argument("--top", type=int, default=0,
                        help="imprime os N sistemas mais caros do último braço")
    args = parser.parse_args()

    arms = load_arms(args.directory, args.phase)
    if not arms:
        print(f"sem JSONs em {args.directory} (fase {args.phase})")
        return 1

    print(f"fase: {args.phase}")
    print(f"{'braço':<8} {'n':>2} {'frame_ms(med)':>14} {'fps_avg(med)':>13}")
    summary = {}
    for arm, rows in sorted(arms.items()):
        frame = median_of(rows, lambda r: r.get("frame_ms_avg"))
        fps = median_of(rows, lambda r: r.get("fps_avg"))
        summary[arm] = frame
        print(f"{arm:<8} {len(rows):>2} {frame if frame is not None else -1:>14.2f} "
              f"{fps if fps is not None else -1:>13.1f}")

    keys = sorted(summary)
    if len(keys) == 2 and summary[keys[0]] and summary[keys[1]]:
        a, b = summary[keys[0]], summary[keys[1]]
        delta = a - b
        pct = delta / a * 100.0 if a else 0.0
        print(f"\ndelta {keys[0]} -> {keys[1]}: {delta:+.2f} ms ({pct:+.1f} %)"
              f"   [{'mais rápido' if delta > 0 else 'mais lento'} o 2.º braço]")
        print("  (sinal positivo = o 2.º braço é mais RÁPIDO)")

    if args.systems:
        names = [n.strip() for n in args.systems.split(",") if n.strip()]
        header = " ".join(f"{k+'.avg':>13} {k+'.max':>13}" for k in sorted(arms))
        print(f"\n{'sistema':<44} {header}")
        for name in names:
            cells = []
            for arm in sorted(arms):
                value = median_of(arms[arm], lambda r, n=name: system_avg(r, n))
                peak = None
                for row in arms[arm]:
                    entry = system_ms(row, name)
                    if entry and entry[1] is not None:
                        peak = entry[1] if peak is None else max(peak, entry[1])
                cells.append(f"{value:>13.5f} {peak if peak is not None else 0:>13.5f}"
                             if value is not None else f"{'—':>13} {'—':>13}")
            print(f"{name:<44} " + " ".join(cells))

    if args.top:
        last_arm = sorted(arms)[-1]
        rows = arms[last_arm]
        systems = (rows[-1].get("last") or {}).get("systems") or []
        print(f"\ntop {args.top} sistemas ({last_arm}):")
        for entry in systems[:args.top]:
            print(f"  {entry.get('avg_ms', 0):>8.4f} ms  {entry.get('name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
