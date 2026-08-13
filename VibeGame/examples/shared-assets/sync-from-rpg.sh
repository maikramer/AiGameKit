#!/usr/bin/env bash
# Copy Crystal Vale packs from simple-rpg into simple-racer (no GPU).
# Source of truth for binaries stays in the RPG public/assets tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/simple-rpg/public/assets"
DST="$ROOT/simple-racer/public/assets"

if [[ ! -d "$SRC/meshes" ]]; then
  echo "sync-from-rpg: missing RPG assets at $SRC" >&2
  exit 1
fi

rsync_pack() {
  local rel="$1"
  if [[ ! -d "$SRC/$rel" ]]; then
    echo "sync-from-rpg: skip missing $rel" >&2
    return 0
  fi
  mkdir -p "$DST/$rel"
  rsync -a --delete \
    --exclude '_intermediate/' \
    --exclude '_intermediate' \
    "$SRC/$rel/" "$DST/$rel/"
  echo "sync-from-rpg: $rel"
}

for pack in forest village infra vegetation; do
  rsync_pack "meshes/$pack"
done

for pack in forest village infra; do
  rsync_pack "images/$pack"
done

if [[ -f "$SRC/sky/sky.png" ]]; then
  mkdir -p "$DST/sky"
  rsync -a "$SRC/sky/sky.png" "$DST/sky/sky.png"
  echo "sync-from-rpg: sky/sky.png"
fi

# rock_mossy lives in the RPG props pack (weapons stay RPG-only).
mkdir -p "$DST/meshes/props"
shopt -s nullglob
for f in "$SRC/meshes/props"/rock_mossy_*; do
  rsync -a "$f" "$DST/meshes/props/$(basename "$f")"
done
if [[ -d "$SRC/images/props" ]]; then
  mkdir -p "$DST/images/props"
  for f in "$SRC/images/props"/rock_mossy*; do
    rsync -a "$f" "$DST/images/props/$(basename "$f")"
  done
fi
echo "sync-from-rpg: meshes/props/rock_mossy_*"

echo "sync-from-rpg: done → $DST"
