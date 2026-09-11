#!/usr/bin/env bash
# A/B de DOIS BINÁRIOS com timings POR SISTEMA (`timed`), não frame time.
#
# Uso: tools/perf_bin_ab.sh <mundo.xml> <binA> <binB> [rounds]
#
# Porquê por sistema: numa máquina com engines de agentes paralelos o frame
# time mede contenda de GPU, não código. O `avg_ms` de um sistema embrulhado em
# `timed(...)` é relógio de parede à volta desse sistema, portanto muito mais
# robusto — e é onde os cortes de CPU se vêem.
#
# Cada braço sobe a engine por `session up` numa porta EXPLÍCITA (o `--port`
# evita cair na porta que outro agente já ocupa e evita o registo stale).
set -uo pipefail
cd "$(dirname "$0")/.."
WORLD="$1"; BIN_A="$2"; BIN_B="$3"
ROUNDS="${4:-1}"
PORT="${VIBER_AB_PORT:-15730}"
SETTLE="${VIBER_AB_SETTLE:-40}"
SAMPLES="${VIBER_AB_SAMPLES:-10}"

outdir="${TMPDIR:-/tmp}/viber-perf-ab"; mkdir -p "$outdir"
echo "mundo=$WORLD  porta=$PORT  rounds=$ROUNDS  settle=${SETTLE}s  samples=$SAMPLES"

# Sistemas a reportar (sufixo do nome canónico — o `timed` guarda
# `std::any::type_name_of_val` do fn item, ex. `viber::particles::particle_emitter_update`).
SYSTEMS="particles::particle_emitter_update,layer_material::terrain_daynight_tint,hud::hud_minimap_update,worldsys::atmosphere_drive,spawn::gltf_scene_spawner,worldsys::weather_drive"

collect() {
  local bin="$1" tag="$2" round="$3"
  echo "--- [$tag r$round] $(basename "$bin") ---"
  env VIBER_PRUNE=0 "$bin" session up --world "$WORLD" --port "$PORT" >/dev/null 2>&1
  local ok=0
  for _ in $(seq 1 120); do
    env "$bin" debug --world "$WORLD" probe >/dev/null 2>&1 && { ok=1; break; }
    sleep 1
  done
  [ "$ok" = 1 ] || { echo "  !! bridge nao subiu"; "$bin" session down --world "$WORLD" >/dev/null 2>&1; return 1; }
  sleep "$SETTLE"

  # 1) repouso: emissores de partículas / HUD
  "$bin" debug --world "$WORLD" prof --samples "$SAMPLES" --json > "$outdir/$tag-r$round-idle.json" 2>/dev/null
  # 2) stress do tint: saltar o relógio por vários passos do dia (cada passo
  #    quantizado dispara uma passagem pelos materiais de chunk)
  for m in 120 300 420 540 660 780 900 1020 1140 1260 1380 60; do
    "$bin" debug --world "$WORLD" lua "viber.debug.set_clock($m) return true" >/dev/null 2>&1
    sleep 0.35
  done
  "$bin" debug --world "$WORLD" prof --samples "$SAMPLES" --json > "$outdir/$tag-r$round-tint.json" 2>/dev/null

  "$bin" session down --world "$WORLD" >/dev/null 2>&1
  sleep 3
}

for r in $(seq 1 "$ROUNDS"); do
  collect "$BIN_A" A "$r"
  collect "$BIN_B" B "$r"
done

python3 - "$outdir" "$SYSTEMS" <<'PY'
import glob, json, os, statistics, sys
outdir, systems = sys.argv[1], [s for s in sys.argv[2].split(',') if s]
for phase in ("idle", "tint"):
    print(f"\n== fase: {phase} ==")
    print(f"{'sistema':<44} {'A avg':>9} {'A max':>9} {'B avg':>9} {'B max':>9}  {'Δavg':>8}")
    for name in systems:
        row = {}
        for arm in ("A", "B"):
            paths = glob.glob(os.path.join(outdir, f"{arm}-r*-{phase}.json"))
            avgs, maxs = [], []
            for p in paths:
                try:
                    last = json.load(open(p)).get("last") or {}
                except Exception:
                    continue
                for entry in last.get("systems") or []:
                    if (entry.get("name") or "").endswith(name):
                        avgs.append(entry.get("avg_ms", 0.0)); maxs.append(entry.get("max_ms", 0.0))
            row[arm] = (statistics.median(avgs) if avgs else None,
                        max(maxs) if maxs else None)
        a, b = row["A"], row["B"]
        if a[0] is None and b[0] is None:
            continue
        fmt = lambda v: f"{v:>9.4f}" if v is not None else f"{'—':>9}"
        delta = (a[0] - b[0]) if (a[0] is not None and b[0] is not None) else None
        print(f"{name:<44} {fmt(a[0])} {fmt(a[1])} {fmt(b[0])} {fmt(b[1])}  "
              f"{('%+8.4f' % delta) if delta is not None else '     —  '}")
print("\n(A = binário 'antes', B = 'depois'; Δavg positivo = B mais rápido)")
PY
echo "JSONs em $outdir"
