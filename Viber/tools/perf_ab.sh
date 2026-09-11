#!/usr/bin/env bash
# A/B de performance por gate de env, com `session up`/`down` (protocolo de QA).
#
# Uso: tools/perf_ab.sh <mundo.xml> <VAR> <valorA> <valorB> [rounds]
# Ex.: tools/perf_ab.sh worlds/qa-visual.xml VIBER_POINT_CONTACT_SHADOWS 1 0
#
# Cada braço sobe a engine partilhada com `VAR=valor`, espera o bridge e o
# terreno, deixa assentar, lê `prof --samples`, grava 5 frames seguidos e desce.
# Os braços correm INTERLEAVED (A,B,A,B,…): numa máquina com engines de agentes
# paralelos a contensão da GPU é a maior fonte de ruído e atinge os dois braços
# por igual, portanto é a comparação DENTRO de cada ronda que conta.
set -uo pipefail
cd "$(dirname "$0")/.."
# `VIBER_BIN` permite fixar uma cópia privada do binário: com agentes paralelos a
# reconstruir `target/release`, o A/B trocava de código a meio.
VIBER="${VIBER_BIN:-./target/release/viber}"
WORLD="$1"; VAR="$2"; VALA="$3"; VALB="$4"
ROUNDS="${5:-2}"
SETTLE="${VIBER_AB_SETTLE:-20}"
SAMPLES="${VIBER_AB_SAMPLES:-8}"

outdir="${TMPDIR:-/tmp}/viber-perf-ab"; mkdir -p "$outdir"
echo "mundo=$WORLD  var=$VAR  A=$VALA B=$VALB  rounds=$ROUNDS  samples=$SAMPLES  settle=${SETTLE}s"
echo "resultados em $outdir"

wait_bridge() {
  for _ in $(seq 1 120); do
    $VIBER debug --world "$WORLD" probe >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

run_arm() {
  local val="$1" round="$2" tag="$3"
  echo "--- [$tag r$round] $VAR=$val ---"
  local port_args=()
  [ -n "${VIBER_AB_PORT:-}" ] && port_args=(--port "$VIBER_AB_PORT")
  if ! env VIBER_PRUNE=0 "$VAR=$val" $VIBER session up --world "$WORLD" "${port_args[@]}" >/dev/null 2>&1; then
    echo "  !! session up falhou"; return 1
  fi
  if ! wait_bridge; then
    echo "  !! bridge nao subiu"; $VIBER session down --world "$WORLD" >/dev/null 2>&1; return 1
  fi
  sleep "$SETTLE"
  $VIBER debug --world "$WORLD" prof --samples "$SAMPLES" --json \
    > "$outdir/$tag-r$round-idle.json" 2>/dev/null
  $VIBER debug --world "$WORLD" prof --samples "$SAMPLES" 2>/dev/null | tr '\n' ' ' | sed 's/^/  /'
  echo
  # Fase de STRESS opcional: saltar o relógio por vários passos do dia força
  # uma passagem por passo nos materiais de chunk — é como o PICO se vê.
  if [ "${VIBER_AB_STRESS:-0}" = 1 ]; then
    for m in 120 300 420 540 660 780 900 1020 1140 1260 1380 60; do
      $VIBER debug --world "$WORLD" lua "viber.debug.set_clock($m) return true" >/dev/null 2>&1
      sleep 0.35
    done
    $VIBER debug --world "$WORLD" prof --samples "$SAMPLES" --json \
      > "$outdir/$tag-r$round-stress.json" 2>/dev/null
  fi
  for i in 1 2 3 4 5; do
    $VIBER debug --world "$WORLD" screenshot -o "$outdir/$tag-r$round-f$i.png" >/dev/null 2>&1
  done
  $VIBER session down --world "$WORLD" >/dev/null 2>&1
  sleep 3
}

for r in $(seq 1 "$ROUNDS"); do
  run_arm "$VALA" "$r" "A"
  run_arm "$VALB" "$r" "B"
done
echo "=== fim; PNGs e JSONs em $outdir ==="
