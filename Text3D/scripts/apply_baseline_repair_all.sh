#!/usr/bin/env bash
# Aplica baseline_apply_repair.py a todos os GLBs *cruos* já existentes em
# testdata/baseline_meshes_raw/ (ignora *_repaired.glb e *_full.glb).
#
# Uso: cd Text3D && ./scripts/apply_baseline_repair_all.sh
#
# Não volta a correr Hunyuan/Text2D — só lê baseline_XX_name.glb e gera
# baseline_XX_name_repaired.glb e baseline_XX_name_full.glb.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/testdata/baseline_meshes_raw"
SCRIPT="${ROOT}/scripts/baseline_apply_repair.py"

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -d "$OUT" ]]; then
  echo "Pasta em falta: $OUT" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT" ]]; then
  echo "Script em falta: $SCRIPT" >&2
  exit 1
fi

shopt -s nullglob
candidates=("$OUT"/baseline_*.glb)
if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "Nenhum baseline_*.glb em $OUT — nada a fazer."
  exit 0
fi

for f in "${candidates[@]}"; do
  base="$(basename "$f" .glb)"
  case "$base" in
    *_repaired|*_full) continue ;;
  esac
  echo "=== $base ==="
  python "$SCRIPT" "$OUT" "$base"
done

echo "Concluído. Saída em: $OUT"
