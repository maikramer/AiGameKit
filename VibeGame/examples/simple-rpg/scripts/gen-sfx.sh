#!/usr/bin/env bash
# gen-sfx.sh — Generate game SFX/BGM as OGG via text2sound, then crop to game length.
#
# Stable Audio Open Small emits ~11 s regardless of the requested duration
# (seconds_start/seconds_total only nudges content placement), and --trim only
# strips silence — so we crop every SFX to its gameplay length with a 60 ms
# fade-out. BGM is kept full-length as a loop.
#
# Requires: text2sound (effects + music profiles), ffmpeg on PATH.
set -eu
export LC_ALL=C

AUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../public/assets/audio" && pwd)"
cd "$AUDIO_DIR"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
FADE_DUR=0.06

gen_crop() {
  local name="$1" prompt="$2" preset="$3" seed="$4" crop="$5"
  local raw="${name}.raw.ogg" final="${name}.ogg"
  echo "--- ${name} (preset=${preset}, crop=${crop}s) ---"
  text2sound generate "$prompt" -p "$preset" --profile effects \
    -d "$crop" --seed "$seed" --trim -f ogg -o "$raw" >/dev/null 2>&1
  local fade_st
  fade_st=$(LC_ALL=C awk "BEGIN{print ${crop} - ${FADE_DUR}}")
  ffmpeg -y -i "$raw" -t "$crop" \
    -af "afade=t=out:st=${fade_st}:d=${FADE_DUR}" \
    -c:a libvorbis -q:a 5 "$final" 2>/dev/null
  rm -f "$raw"
  printf "    -> %s (%.1fs, %s)\n" "$final" "$crop" "$(du -h "$final" | cut -f1)"
}

echo "=== Generating 11 SFX (effects/Open Small -> OGG) ==="
gen_crop "sfx_hit"          "sharp metal sword clash, bright metallic ring, single combat strike impact"        "sword-clash"    201 "1.5"
gen_crop "sfx_enemy_hurt"   "pained creature grunt, short exertion groan, monster hurt vocalization"            "grunt-effort"   202 "1.0"
gen_crop "sfx_enemy_death"  "creature death screech, dying monster wail, fading creature cry"                   "creature-death" 203 "2.5"
gen_crop "sfx_boss_roar"    "deep intimidating boss monster roar, massive ogre bellow, aggressive beast howl"   "creature-roar"  204 "3.0"
gen_crop "sfx_shop_open"    "wooden door creaking open, shop entrance, rustic door swing"                      "door-open"      205 "2.0"
gen_crop "sfx_buy"          "pleasant purchase confirmation chime, coin transaction success, bright UI confirm" "ui-confirm"     206 "1.5"
gen_crop "sfx_error"        "soft error buzz, negative UI feedback tone, denied action beep"                    "ui-cancel"      207 "1.0"
gen_crop "sfx_player_hurt"  "human pain grunt, short hurt exertion, fighter taking a hit"                      "grunt-effort"   208 "1.0"
gen_crop "sfx_heal"         "warm magical healing shimmer, restorative sparkle, gentle ascending heal chime"    "heal"           209 "2.0"
gen_crop "sfx_coin"         "bright coin pickup chime, gold coin collect, cheerful currency jingle"            "coin-pickup"    210 "1.0"
gen_crop "sfx_item_drop"    "item dropping on ground, object landing thud, loot drop impact"                    "item-drop"      211 "1.5"

echo
echo "=== Generating BGM battle loop ==="
bgm_prompt="intense fantasy battle music, driving orchestral combat theme, dramatic action rhythm"
if ! text2sound generate "$bgm_prompt" -p battle --profile music -d 30 --seed 220 --trim \
     -f ogg --low-vram -o bgm_battle.ogg >/dev/null 2>&1 || [ ! -f bgm_battle.ogg ]; then
  echo "    music/Open 1.0 failed -- falling back to effects 11s loop"
  # battle preset forces 30s which exceeds the effects model's 11s max; use the
  # prompt directly with explicit params instead.
  text2sound generate "$bgm_prompt, epic adventure" --profile effects \
    -d 11 -s 100 --seed 220 --trim -f ogg -o bgm_battle.ogg >/dev/null 2>&1
fi
printf "    -> bgm_battle.ogg (%s)\n" "$(du -h bgm_battle.ogg | cut -f1)"

echo
echo "=== Done. OGG files: ==="
ls -lh *.ogg
