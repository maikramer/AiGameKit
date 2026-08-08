#!/usr/bin/env bash
# gen-missing.sh — regenerate the audio clips that failed while the props batch
# held the GPU. Run AFTER the batch with the UMS queue empty:
#
#   ums zero && bash scripts/gen-missing.sh
#
# The music model (Open 1.0) needs ~5120 MiB: `ums zero` frees the CUDA contexts
# of idle workers, otherwise hw-auto offloads to CPU and fails with
# "tensors on different devices" (and poisons the text2sound worker).
set -eu

AUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../public/assets/audio" && pwd)"
cd "$AUDIO_DIR"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
T2S="$(command -v text2sound)"

gen() {
  local name="$1" prompt="$2" preset="$3" seed="$4" dur="$5"
  echo "--- ${name} (${preset}, ${dur}s) ---"
  $T2S generate "$prompt" -p "$preset" --profile effects \
    -d "$dur" --seed "$seed" --crop --trim -f ogg -o "${name}.ogg"
  printf "    -> %s (%ss)\n" "${name}.ogg" "$dur"
}

echo "=== SFX faltantes (effects/Open Small) ==="
gen sfx_go    "loud race start horn blast, GO signal klaxon, powerful air horn, short urgent starting signal" alarm-klaxon 306 1.0
gen sfx_coin  "bright coin pickup chime, sparkling collectible jingle, short rewarding sparkle sound"          coin-pickup  307 0.8
gen sfx_lap   "soft lap completion chime, pleasant two-tone bell, checkpoint confirm sound, gentle success ping" ui-confirm   308 1.0
# Fanfare sem preset: preset `victory` força o modelo de música (Open 1.0);
# o effects model faz um jingle curto suficiente e cabe na GPU 6 GB.
gen sfx_finish "triumphant race finish fanfare, short victory jingle, celebratory brass melody, crossing the finish line" ui-confirm 309 4.0

echo
echo "=== BGM (music/Open 1.0, seamless loop) ==="
$T2S generate "upbeat arcade kart racing music, energetic synth rock loop, fast driving tempo, pumping bass line, catchy electric guitar melody, exciting race atmosphere" \
  --profile music -d 47 -s 100 --seed 310 --trim \
  --seamless-loop --crossfade-ms 2000 --loop-edge-trim 8 -f ogg -o bgm_race.ogg
printf "    -> bgm_race.ogg\n"

$T2S generate "cheerful arcade racing menu music, light synth pop loop, playful and relaxed, retro racing game lobby jingle, warm synth pads" \
  --profile music -d 47 -s 100 --seed 320 --trim \
  --seamless-loop --crossfade-ms 2000 --loop-edge-trim 4 -f ogg -o bgm_menu.ogg
printf "    -> bgm_menu.ogg\n"

echo
echo "=== Done. OGG files: ==="
ls -lh *.ogg
