#!/bin/bash
set -e
source /home/maikeu/GitClones/GameDev/GameAssets/.venv/bin/activate
gameassets batch \
  --profile game.yaml \
  --manifest manifest \
  --presets-local presets-local.yaml \
  --skip-audio \
  --skip-gpu-preflight \
  --log batch-log-3d.jsonl \
  "$@"
