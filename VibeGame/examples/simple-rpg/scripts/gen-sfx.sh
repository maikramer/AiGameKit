#!/usr/bin/env bash
# gen-sfx.sh — Generate game SFX/BGM as OGG via text2sound (--crop handles length).
#
# --crop truncates each output to the requested -d duration with a 60 ms fade-out,
# so no ffmpeg post-processing is needed.
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

echo "=== Generating 11 SFX (effects/Open Small -> OGG) ==="
gen sfx_hit          "sharp metal sword clash, bright metallic ring, single combat strike impact"        sword-clash    201 1.5
gen sfx_enemy_hurt   "pained creature grunt, short exertion groan, monster hurt vocalization"            grunt-effort   202 1.0
gen sfx_enemy_death  "creature death screech, dying monster wail, fading creature cry"                   creature-death 203 2.5
gen sfx_boss_roar    "deep intimidating boss monster roar, massive ogre bellow, aggressive beast howl"   creature-roar  204 3.0
gen sfx_shop_open    "wooden door creaking open, shop entrance, rustic door swing"                      door-open      205 2.0
gen sfx_buy          "pleasant purchase confirmation chime, coin transaction success, bright UI confirm" ui-confirm     206 1.5
gen sfx_error        "soft error buzz, negative UI feedback tone, denied action beep"                    ui-cancel      207 1.0
gen sfx_player_hurt  "human pain grunt, short hurt exertion, fighter taking a hit"                      grunt-effort   208 1.0
gen sfx_heal         "warm magical healing shimmer, restorative sparkle, gentle ascending heal chime"    heal           209 2.0
gen sfx_coin         "bright coin pickup chime, gold coin collect, cheerful currency jingle"            coin-pickup    210 1.0
gen sfx_item_drop    "item dropping on ground, object landing thud, loot drop impact"                    item-drop      211 1.5

echo
echo "=== Generating BGM battle loop (seamless) ==="
# BGM always uses the music model (Open 1.0). The old fallback to the effects
# model (Open Small, 11 s, cfg 1) produced the short low-quality loops this
# script used to ship: text2sound's hw-auto now fits Open 1.0 in 6 GB-class
# GPUs (fp16 + chunked VAE decode), so the fallback is gone on purpose.
# --seamless-loop crossfades tail→head and drops the folded head, so the OGG
# loops gapless under the engine's native loop=1 playback.
# -d 47 + --loop-edge-trim 4: the model composes an intro swell and a quiet
# outro for the requested length; looped raw those become a repeated
# creature-like transient at every cycle start plus an energy dip at the wrap.
# Trimming 4 s off each edge keeps only steady-state music for the loop.
bgm_prompt="intense fantasy battle music, driving orchestral combat theme, dramatic action rhythm"
# seed 220 composes a ~12 s quiet build-up, so battle needs a deeper edge trim
# than explore for uniform loop energy (validated: first/last 3 s RMS ≥ 60% of
# the track median).
text2sound generate "$bgm_prompt" -p battle --profile music -d 47 -s 100 --seed 220 --trim \
  --seamless-loop --crossfade-ms 2000 --loop-edge-trim 8 -f ogg -o bgm_battle.ogg >/dev/null 2>&1
printf "    -> bgm_battle.ogg (%s)\n" "$(du -h bgm_battle.ogg | cut -f1)"

echo
echo "=== Generating BGM explore loop (seamless) ==="
text2sound generate "peaceful fantasy village exploration music, gentle acoustic guitar and flute, warm ambient adventure melody, calm countryside" \
  --profile music -d 47 -s 100 --seed 300 --trim --seamless-loop --crossfade-ms 2000 --loop-edge-trim 4 -f ogg -o bgm_explore.ogg >/dev/null 2>&1
printf "    -> bgm_explore.ogg (%s)\n" "$(du -h bgm_explore.ogg | cut -f1)"

echo
echo "=== Done. OGG files: ==="
ls -lh *.ogg
