#!/usr/bin/env bash
# Arranque do MCP server do plugin aigamekit-gen.
# Procura node sem depender do PATH de login do host (nvm, ~/.local, sistema)
# e faz exec — nenhum output para stdout (protocolo MCP é newline-delimited JSON).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

NODE_BIN=""
for candidate in \
  "$(command -v node 2>/dev/null || true)" \
  "$HOME"/.nvm/versions/node/*/bin/node \
  /usr/local/bin/node /usr/bin/node /opt/node/bin/node; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    NODE_BIN="$candidate"
    break
  fi
done

if [ -z "$NODE_BIN" ]; then
  echo "aigamekit-gen MCP: node não encontrado (procurei em PATH, ~/.nvm, /usr/local/bin, /usr/bin, /opt/node)" >&2
  exit 127
fi

exec "$NODE_BIN" "$SCRIPT_DIR/server.mjs" "$@"
