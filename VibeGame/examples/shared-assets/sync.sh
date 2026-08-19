#!/usr/bin/env bash
# Distribute Crystal Vale shared packs into simple-racer.
#
# simple-rpg resolves the shared packs directly via symlinks
# (public/assets/{meshes,images}/<pack> → this pool) — no copies. simple-racer
# versions its own binaries for clone-friendliness, so it receives copies.
# Regen (GPU) happens in this folder:
#   cd examples/shared-assets && gameassets resume --profile game.yaml --manifest manifests/<pack>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/shared-assets/public/assets"
DST="$ROOT/simple-racer/public/assets"

if [[ ! -d "$SRC/meshes" ]]; then
  echo "sync: missing shared binaries at $SRC" >&2
  echo "sync: regen with: cd examples/shared-assets && gameassets resume --profile game.yaml --manifest manifests/<pack>" >&2
  exit 1
fi
if [[ ! -d "$DST" ]]; then
  echo "sync: missing example at $DST" >&2
  exit 1
fi

sync_dir() {
  local rel="$1"
  if [[ ! -d "$SRC/$rel" ]]; then
    echo "sync: skip missing $rel" >&2
    return 0
  fi
  mkdir -p "$DST/$rel"
  rsync -a "$SRC/$rel/" "$DST/$rel/"
  echo "sync: $rel → $DST"
}

for pack in forest village infra vegetation; do
  sync_dir "meshes/$pack"
done
for pack in forest village infra; do
  sync_dir "images/$pack"
done

# rock_mossy lives in the shared props pack (racer-only props stay put).
mkdir -p "$DST/meshes/props" "$DST/images/props"
for f in "$SRC/meshes/props"/rock_mossy_*; do
  rsync -a "$f" "$DST/meshes/props/$(basename "$f")"
done
rsync -a "$SRC/images/props/rock_mossy.png" "$DST/images/props/rock_mossy.png"
echo "sync: meshes/props/rock_mossy_* → $DST"

if [[ -f "$SRC/sky/sky.png" ]]; then
  mkdir -p "$DST/sky"
  rsync -a "$SRC/sky/sky.png" "$DST/sky/sky.png"
  echo "sync: sky/sky.png → $DST"
fi

echo "sync: done"
