#!/bin/bash
set -e

GAMEDEV_VENV="${GAMEDEV_VENV:-/home/maikeu/GitClones/GameDev/GameAssets/.venv}"
BIOME="${1:-all}"

# Prefer the newer global CLIs when present; the GameAssets venv may bundle
# older builds that predate options like text2d --quality.
export TEXT2D_BIN="${TEXT2D_BIN:-/home/maikeu/.local/bin/text2d}"
export TEXT3D_BIN="${TEXT3D_BIN:-/home/maikeu/.local/bin/text3d}"
export PAINT3D_BIN="${PAINT3D_BIN:-/home/maikeu/.local/bin/paint3d}"
export RIGGING3D_BIN="${RIGGING3D_BIN:-/home/maikeu/.local/bin/rigging3d}"
export ANIMATOR3D_BIN="${ANIMATOR3D_BIN:-/home/maikeu/.local/bin/animator3d}"

activate() {
  if [ -f "$GAMEDEV_VENV/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$GAMEDEV_VENV/bin/activate"
  fi
}

run_biome() {
  local biome="$1"
  local manifest_file="manifest.${biome}.yaml"
  if [ ! -f "$manifest_file" ]; then
    echo "[biomes] manifest nao encontrado: $manifest_file" >&2
    return 1
  fi
  echo "[biomes] === Gerando $biome (manifest=$manifest_file) ==="
  gameassets batch \
    --profile game.yaml \
    --manifest "$manifest_file" \
    --presets-local presets-local.yaml \
    --skip-audio \
    --skip-gpu-preflight \
    --log "batch-log-${biome}.jsonl"
}

run_audio() {
  echo "[biomes] === Gerando audio biome (BGM + SFX) ==="
  gameassets batch \
    --profile game.yaml \
    --manifest manifest \
    --presets-local presets-local.yaml \
    --skip-text2d \
    --skip-gpu-preflight \
    --skip-batch-lock \
    --only-audio-ids "bgm_dark_forest,bgm_desert,bgm_swamp,sfx_wolf_howl,sfx_scorpion_hiss,sfx_bogling_jump,sfx_quest_complete,sfx_npc_speak_low,sfx_npc_speak_mid,sfx_npc_speak_high" \
    --log batch-log-biome-audio.jsonl
}

case "$BIOME" in
  dark_forest|forest) activate; run_biome dark_forest ;;
  desert)             activate; run_biome desert ;;
  swamp)              activate; run_biome swamp ;;
  audio)              activate; run_audio ;;
  all)
    activate
    run_biome dark_forest
    run_biome desert
    run_biome swamp
    run_audio
    ;;
  *)
    echo "Usage: $0 {dark_forest|desert|swamp|audio|all}" >&2
    exit 2
    ;;
esac

echo "[biomes] done ($BIOME)"
