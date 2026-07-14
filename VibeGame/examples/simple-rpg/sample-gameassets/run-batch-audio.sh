#!/bin/bash
set -e
source /home/maikeu/GitClones/GameDev/GameAssets/.venv/bin/activate
gameassets batch \
  --profile game.yaml \
  --manifest manifest \
  --presets-local presets-local.yaml \
  --skip-text2d \
  --skip-gpu-preflight \
  --skip-batch-lock \
  --log batch-log-audio.jsonl \
  "$@"
