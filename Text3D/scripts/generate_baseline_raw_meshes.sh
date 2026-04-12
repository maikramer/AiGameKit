#!/usr/bin/env bash
# Gera 8 baselines stress-test em três etapas comparáveis (por ficheiro base):
#
#   1) {name}.glb              — Hunyuan export cru (sem repair_mesh, sem remove_backing_plates)
#   2) {name}_repaired.glb     — mesmo ficheiro + repair_mesh (pipeline atual, defaults CLI)
#   3) {name}_full.glb         — repaired + remove_backing_plates (equivalente ao fluxo completo)
#
# Requisitos: Text3D instalado (pip install -e . em Text3D/), GPU para Hunyuan3D.
# Para 2–3 também: pymeshlab (remesh) e dependências de repair; sem pymeshlab o remesh
# pode ser ignorado com aviso (mesh passa igual).
#
# Uso (a partir da raiz do repositório GameDev ou Text3D):
#   cd Text3D && ./scripts/generate_baseline_raw_meshes.sh
#   cd Text3D && ./scripts/generate_baseline_raw_meshes.sh --clean   # apaga GLB/PNG antigos e gera do zero
#
# Se já tens os GLBs cruos e só queres *_repaired / *_full:
#   ./scripts/apply_baseline_repair_all.sh
#
# Saída: Text3D/testdata/baseline_meshes_raw/*.glb

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/testdata/baseline_meshes_raw"
mkdir -p "$OUT"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Limpando ${OUT} (*.glb, *.png)..."
  shopt -s nullglob
  for f in "${OUT}"/*.glb "${OUT}"/*.png; do
    rm -f "$f"
  done
  shopt -u nullglob
fi

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

run_one() {
  local name="$1"
  shift
  local prompt="$1"
  echo "=== ${name} ==="
  # PROMPT tem de vir no fim (Click); senão as opções partem o texto em argumentos extra.
  python -m text3d generate \
    -o "${OUT}/${name}.glb" \
    --preset fast \
    --max-retries 1 \
    --no-mesh-repair \
    --no-remove-plates \
    --save-reference-image \
    -- "${prompt}"
  echo "  (pós-processo: repaired + full)"
  python "${ROOT}/scripts/baseline_apply_repair.py" "${OUT}" "${name}"
}

# Oito prompts stress-test (aberturas, tampas finas, paredes duplas, bases) — inglês Text2D/Hunyuan
run_one "baseline_01_rock" "rough gray boulder rock, natural stone, small prop, solid form"
run_one "baseline_02_tree" "stylized low poly tree, round green canopy, brown trunk, game asset"
run_one "baseline_03_crate" "wooden storage crate, weathered planks, metal corners, open top, hollow box, game prop"
run_one "baseline_04_sword" "simple fantasy sword, short blade, stylized, single mesh"
run_one "baseline_05_pillar" "short stone pillar ruin, cracked surface, ancient, vertical"
run_one "baseline_06_teapot_lid" "ceramic teapot with thin flat lid on top, small curved spout, handle, game prop, single object"
run_one "baseline_07_bucket" "galvanized metal bucket, open top, thin rolled rim, hollow cylinder, game prop"
run_one "baseline_08_scale" "vintage brass balance scale, two thin circular pans on arms, delicate frame, desk prop"

echo "Concluído. GLB em: ${OUT}"
