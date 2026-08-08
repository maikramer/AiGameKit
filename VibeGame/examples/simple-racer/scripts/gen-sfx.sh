#!/usr/bin/env bash
# gen-sfx.sh — Generate racing SFX/BGM as OGG via text2sound.
#
# Mirrors examples/simple-rpg/scripts/gen-sfx.sh: --crop truncates each output
# to the requested -d duration with a 60 ms fade-out, so no ffmpeg post is needed.
#
# Spec de referência: sample-gameassets/manifests/audio.yaml (formato simple-rpg).
#
# Requires: text2sound (effects + music profiles) on PATH.
set -eu

AUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../public/assets/audio" && pwd)"
cd "$AUDIO_DIR"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

gen() {
  local name="$1" prompt="$2" preset="$3" seed="$4" dur="$5"
  echo "--- ${name} (${preset}, ${dur}s) ---"
  text2sound generate "$prompt" -p "$preset" --profile effects \
    -d "$dur" --seed "$seed" --crop --trim -f ogg -o "${name}.ogg" >/dev/null 2>&1
  printf "    -> %s (%ss, %s)\n" "${name}.ogg" "$dur" "$(du -h "${name}.ogg" | cut -f1)"
}

echo "=== Generating 9 racing SFX (effects/Open Small -> OGG) ==="
gen sfx_engine_rev "arcade race car engine revving up, high revving kart motor roar, aggressive throttle blip, mechanical engine growl"    clockwork      301 2.5
gen sfx_skid       "tire skid squeal on asphalt, sharp rubber screech, kart drift brake sound, short sliding friction squeak"               footsteps-grass 302 1.5
gen sfx_nitro      "nitro boost activation whoosh, powerful rocket-like surge, jet burst hiss with rising pitch, turbo power up"             magic-spell    303 2.0
gen sfx_crash      "kart crash impact, metal scrape and smash, car collision bang with debris rattle, short heavy crash"                     explosion      304 2.0
gen sfx_countdown  "single short countdown beep, clean digital tone, race start ready signal, brief electronic beep"                         ui-click       305 0.6
gen sfx_go         "loud race start horn blast, GO signal klaxon, powerful air horn, short urgent starting signal"                            alarm-klaxon   306 1.0
gen sfx_coin       "bright coin pickup chime, sparkling collectible jingle, short rewarding sparkle sound"                                   coin-pickup    307 0.8
gen sfx_lap        "soft lap completion chime, pleasant two-tone bell, checkpoint confirm sound, gentle success ping"                         ui-confirm     308 1.0
gen sfx_finish     "triumphant race finish fanfare, short victory jingle, celebratory brass melody, crossing the finish line"                victory        309 6.0

echo
echo "=== Generating BGM race loop (seamless) ==="
# BGM uses the music model (Open 1.0); hw-auto fits it in 6 GB-class GPUs
# (fp16 + chunked VAE decode). --seamless-loop crossfades tail→head and drops
# the folded head, so the OGG loops gapless under the engine's native loop=1.
# -d 47 + --loop-edge-trim 8: the model composes an intro swell/outro for the
# requested length; trimming 4–8 s off each edge keeps only steady-state music.
text2sound generate "upbeat arcade kart racing music, energetic synth rock loop, fast driving tempo, pumping bass line, catchy electric guitar melody, exciting race atmosphere" \
  --profile music -d 47 -s 100 --seed 310 --trim \
  --seamless-loop --crossfade-ms 2000 --loop-edge-trim 8 -f ogg -o bgm_race.ogg >/dev/null 2>&1
printf "    -> bgm_race.ogg (%s)\n" "$(du -h bgm_race.ogg | cut -f1)"

echo
echo "=== Generating BGM menu loop (seamless) ==="
text2sound generate "cheerful arcade racing menu music, light synth pop loop, playful and relaxed, retro racing game lobby jingle, warm synth pads" \
  --profile music -d 47 -s 100 --seed 320 --trim \
  --seamless-loop --crossfade-ms 2000 --loop-edge-trim 4 -f ogg -o bgm_menu.ogg >/dev/null 2>&1
printf "    -> bgm_menu.ogg (%s)\n" "$(du -h bgm_menu.ogg | cut -f1)"

echo
echo "=== Done. OGG files: ==="
ls -lh *.ogg
