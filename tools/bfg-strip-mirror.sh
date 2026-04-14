#!/usr/bin/env bash
# BFG: remove do histórico blobs maiores que o limiar (defeito: 2M).
# Uso (num clone BARE / mirror):
#   bash tools/bfg-strip-mirror.sh /tmp/GameDev.git
# Limiar custom:
#   BFG_STRIP_BLOBS=5M bash tools/bfg-strip-mirror.sh /tmp/GameDev.git
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRIP="${BFG_STRIP_BLOBS:-2M}"
MIRROR="${1:?Caminho para repositório .git bare (ex.: /tmp/GameDev.git)}"
if [[ ! -d "$MIRROR" ]]; then
  echo "Erro: não existe $MIRROR" >&2
  exit 1
fi
echo "BFG --strip-blobs-bigger-than $STRIP em $MIRROR"
"$HERE/bfg" --strip-blobs-bigger-than "$STRIP" "$MIRROR"
cd "$MIRROR"
git reflog expire --expire=now --all
git gc --prune=now --aggressive
echo "OK. git count-objects:"
git count-objects -vH
