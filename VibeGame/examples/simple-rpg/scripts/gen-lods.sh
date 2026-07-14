#!/usr/bin/env bash
# Generate LOD1 (for rigged/animated models) and LOD1+LOD2 (for static models)
# using text3d lod --meshopt. The current GLBs in public/ are already meshopt-
# compressed, so they go through copy → dequantize first to give bpy a readable
# float32 source.
#
# Rigged models: only LOD1 is used at runtime (LOD0 = full quality, LOD1 = the
#   one the game references). LOD2 is generated but not referenced for rigged.
# Static models: LOD1 becomes the base url, LOD2 the far-distance fallback.
#
# Usage:
#   bash scripts/gen-lods.sh rigged    # 14 rigged_animated models → *_lod1.glb
#   bash scripts/gen-lods.sh static    # 19 static models → *_lod1.glb + *_lod2.glb
#   bash scripts/gen-lods.sh all       # both
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
MESH_DIR="$ROOT/public/assets/meshes"
TMP="$(mktemp -d -t gen-lods-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# Prepare a bpy-readable (float32, no meshopt) source from a meshopt-compressed GLB.
decompress_source() {
  local src="$1" out="$2"
  npx --yes @gltf-transform/cli copy "$src" "$TMP/_c.glb" 2>/dev/null
  npx --yes @gltf-transform/cli dequantize "$TMP/_c.glb" "$out" 2>/dev/null
}

# Validate a generated LOD: must have skins+animations if rigged, meshopt compression.
validate_glb() {
  local file="$1" expect_rigged="${2:-false}"
  python3 - "$file" "$expect_rigged" <<'PY'
import json, struct, sys, os
path, expect_rigged = sys.argv[1], sys.argv[2] == "true"
with open(path, "rb") as f:
    f.read(12); jl = struct.unpack("<I", f.read(4))[0]; f.read(4)
    j = json.loads(f.read(jl))
exts = set(j.get("extensionsUsed", []))
has_meshopt = "EXT_meshopt_compression" in exts
skins = len(j.get("skins", []))
anims = len(j.get("animations", []))
ok = has_meshopt
if expect_rigged:
    ok = ok and skins >= 1 and anims >= 1
size_kb = os.path.getsize(path) / 1024
status = "OK" if ok else "FAIL"
print(f"  [{status}] {os.path.basename(path):40s} {size_kb:7.0f}KB  skins={skins} anims={anims} meshopt={has_meshopt}")
sys.exit(0 if ok else 1)
PY
}

gen_rigged() {
  local models=(
    hero goblin wolf bandit slime mosquito scorpion bogling shade npc_merchant
    boss_ogre bog_warden_boss sand_wyrm_boss witch_boss
  )
  echo "=== Generating LOD1 for ${#models[@]} rigged models ==="
  for m in "${models[@]}"; do
    local src="$MESH_DIR/${m}_rigged_animated.glb"
    if [ ! -f "$src" ]; then echo "  SKIP $m (missing $src)"; continue; fi
    echo ">>> $m"
    local deq="$TMP/${m}_deq.glb"
    decompress_source "$src" "$deq"
    local outdir="$TMP/${m}_lod"
    mkdir -p "$outdir"
    if ! text3d lod "$deq" -o "$outdir" -n "$m" --meshopt 2>/dev/null; then
      echo "  FAIL $m (text3d lod error)"; continue
    fi
    # Install LOD1 as the runtime rigged_animated LOD1.
    local lod1_src="$outdir/${m}_lod1.glb"
    local lod1_dst="$MESH_DIR/${m}_rigged_animated_lod1.glb"
    if [ -f "$lod1_src" ]; then
      cp "$lod1_src" "$lod1_dst"
      validate_glb "$lod1_dst" true || echo "  (validation failed for $m)"
    else
      echo "  FAIL $m (lod1 not generated)"
    fi
  done
}

gen_static() {
  # Static models referenced via *_lod0.glb that lack lod1/lod2.
  local models=(
    chapel crystal_blue dead_bush lily_pad market_stall medieval_well
    moss_rock mushroom_glow rock_mossy ruin_pillar scorpion_nest swamp_shack
    treasure_chest village_longhouse watchtower witch_hut wooden_barrel
    wooden_bench wooden_crate
  )
  echo "=== Generating LOD1+LOD2 for ${#models[@]} static models ==="
  for m in "${models[@]}"; do
    local src="$MESH_DIR/${m}_lod0.glb"
    if [ ! -f "$src" ]; then echo "  SKIP $m (missing $src)"; continue; fi
    echo ">>> $m"
    local deq="$TMP/${m}_deq.glb"
    decompress_source "$src" "$deq"
    local outdir="$TMP/${m}_lod"
    mkdir -p "$outdir"
    if ! text3d lod "$deq" -o "$outdir" -n "$m" --meshopt 2>/dev/null; then
      echo "  FAIL $m (text3d lod error)"; continue
    fi
    for level in lod1 lod2; do
      local s="$outdir/${m}_${level}.glb"
      local d="$MESH_DIR/${m}_${level}.glb"
      if [ -f "$s" ]; then
        cp "$s" "$d"
        validate_glb "$d" false || echo "  (validation failed for $m $level)"
      else
        echo "  FAIL $m ($level not generated)"
      fi
    done
  done
}

case "${1:-all}" in
  rigged) gen_rigged ;;
  static) gen_static ;;
  all) gen_rigged; echo; gen_static ;;
  *) echo "Usage: $0 [rigged|static|all]"; exit 1 ;;
esac

echo "=== Done ==="
