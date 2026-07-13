#!/usr/bin/env bash
# Gera 3 GLBs de arquitectura para a cidade elaborada do simple-rpg.
# Pipeline por asset: text3d generate (shape + ref) → paint3d texture (PBR) → text3d collision.
# Estilo cartoon fantasy RPG alinhado ao presets-local.yaml (Pixar-meets-Zelda).
# RTX 4050: ~10-15 min por asset. Corre em sequência (partilham a mesma VRAM).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESHES_DIR="${SCRIPT_DIR}/../public/assets/meshes"
TMP_DIR="${MESHES_DIR}/_city_gen"
mkdir -p "${TMP_DIR}"

PREFIX="cartoon fantasy game asset, vibrant colors, bold outlines, stylized proportions, cel-shaded look, soft rounded shapes, Pixar-meets-Zelda aesthetic, single object centered, clean simple geometry, smooth mesh, game-ready"

gen_asset() {
  local id="$1"
  local desc="$2"
  local shape="${TMP_DIR}/${id}_shape.glb"
  local painted="${TMP_DIR}/${id}_painted.glb"
  local lod0="${MESHES_DIR}/${id}_lod0.glb"
  local coll="${MESHES_DIR}/${id}_collision.glb"

  echo ""
  echo "============================================================"
  echo "  [${id}] ${desc}"
  echo "============================================================"

  # 1. Shape + ref image via text3d generate (Text2D → Hunyuan3D).
  #    --save-reference-image grava <stem>_input.png (usada pelo paint3d).
  if [[ ! -f "${shape}" ]]; then
    echo "  [text3d] a gerar shape + ref..."
    text3d generate "${PREFIX}, ${desc}" \
      -o "${shape}" \
      --quality medium \
      --category prop \
      --export-origin feet \
      --save-reference-image
  else
    echo "  [text3d] shape já existe, salto."
  fi

  # ref gravado como <stem>_text2d.png junto ao shape (ver text3d generate --help).
  local ref="${TMP_DIR}/${id}_shape_text2d.png"

  # 2. PBR texture via paint3d.
  if [[ ! -f "${painted}" ]]; then
    echo "  [paint3d] a texturizar..."
    paint3d texture "${shape}" -i "${ref}" -o "${painted}" --no-upscale
  else
    echo "  [paint3d] já pintado, salto."
  fi

  # 3. LOD0 = painted (cópia directa — painted é o asset final para edifícios estáticos).
  cp "${painted}" "${lod0}"
  echo "  [lod0] ✓ ${id}_lod0.glb"

  # 4. Collision mesh (convex hull + decimate).
  text3d collision "${lod0}" -o "${coll}" --max-faces 400
  echo "  [collision] ✓ ${id}_collision.glb"

  echo "  >>> [${id}] CONCLUÍDO"
}

echo "### Geração de 3 GLBs de arquitectura — cidade elaborada simple-rpg"
echo "### Início: $(date '+%Y-%m-%d %H:%M:%S')"

gen_asset "village_longhouse" \
  "large medieval wooden longhouse mead-hall with carved timber beams, shingled steep roof, grand double doors, glowing warm windows, stone foundation, elder meeting hall"

gen_asset "watchtower" \
  "tall narrow medieval wooden watchtower with heavy stone base, crenellated battlement top, wooden ladder, flag pole, observation platform, defensive tower"

gen_asset "chapel" \
  "small medieval stone chapel with tall bell tower, arched stained-glass window, peaked slate roof, wooden door, cozy village temple, house of worship"

echo ""
echo "### Fim: $(date '+%Y-%m-%d %H:%M:%S')"
echo "### Outputs em ${MESHES_DIR}/"
ls -la "${MESHES_DIR}"/village_longhouse_lod0.glb "${MESHES_DIR}"/watchtower_lod0.glb "${MESHES_DIR}"/chapel_lod0.glb 2>/dev/null || true
