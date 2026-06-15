#!/usr/bin/env bash
# gen-assets.sh — Run the GameAssets batch pipeline on a 6GB shared GPU (RTX 4050 Laptop).
#
# Sets the env vars that allow Text3D/Paint3D to share the GPU with other processes,
# enables low-VRAM mode, and runs the batch against sample-gameassets/.
#
# Usage:
#   ./scripts/gen-assets.sh                  # full batch (all assets)
#   ./scripts/gen-assets.sh --force          # regenerate everything
#   ./scripts/gen-assets.sh --skip-text2d    # skip 2D images
#   ./scripts/gen-assets.sh --dry-run        # preview commands
#   Any extra args are forwarded to `gameassets batch`.
set -euo pipefail

# Allow GPU sharing with other processes (RTX 4050 6GB is a shared laptop GPU).
export TEXT3D_ALLOW_SHARED_GPU="${TEXT3D_ALLOW_SHARED_GPU:-1}"
export PAINT3D_ALLOW_SHARED_GPU="${PAINT3D_ALLOW_SHARED_GPU:-1}"
# Never kill competing GPU processes (laptop has display server etc.).
export TEXT3D_GPU_KILL_OTHERS="${TEXT3D_GPU_KILL_OTHERS:-0}"
export PAINT3D_GPU_KILL_OTHERS="${PAINT3D_GPU_KILL_OTHERS:-0}"
# Reduce VRAM fragmentation for the SDNQ INT4 + cpu_offload path.
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_DIR="$SCRIPT_DIR/../sample-gameassets"

cd "$BATCH_DIR"

# --low-vram propagates low-VRAM settings to every sub-tool (text2d/text3d/paint3d/...).
# The text3d doctor auto-detects the cuda-1x6g hardware profile (SDNQ INT4, octree 128,
# 4096 chunks, hierarchical decoder, 1024x1024 image) — no manual --sdnq-preset needed.
exec gameassets batch \
  --profile game.yaml \
  --manifest manifest.yaml \
  --low-vram \
  "$@"
