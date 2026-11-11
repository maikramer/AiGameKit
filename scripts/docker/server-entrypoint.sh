#!/usr/bin/env bash
# Entrypoint do servidor de GPU: prepara o cache do vramd e arranca o supervisor.
#
# Todo o estado vive em /cache (VRAMD_CACHE_DIR): modelos, sandboxes de
# transferência, logs, WAL e o token estável dos clientes remotos.
set -euo pipefail

DATA_DIR="${VRAMD_CACHE_DIR:-/cache}"
export VRAMD_CACHE_DIR="$DATA_DIR"
export VRAMD_TOOLS_ROOT="${VRAMD_TOOLS_ROOT:-/opt/AiGameKit}"
export VRAMD_BACKENDS_FILE="${VRAMD_BACKENDS_FILE:-/opt/AiGameKit/Shared/src/aigamekit_shared/data/backends.yaml:/opt/AiGameKit/Shared/src/aigamekit_shared/data/calibrated/backends-6g.yaml}"
export VRAMD_TOKEN_FILE="${VRAMD_TOKEN_FILE:-$DATA_DIR/token}"
export VRAMD_IDLE_TIMEOUT_MIN="${VRAMD_IDLE_TIMEOUT_MIN:-0}"
export VRAMD_LISTEN="${VRAMD_LISTEN:-0.0.0.0:8790}"

# Cache num só sítio (o "cache do vramd"): modelos offline, sem HF_TOKEN.
export HF_HOME="${HF_HOME:-$DATA_DIR/models/hf}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export AIGAMEKIT_MODELS_DIR="${AIGAMEKIT_MODELS_DIR:-$DATA_DIR/models/aigamekit}"
export U2NET_HOME="${U2NET_HOME:-$DATA_DIR/models/u2net}"
export AIGAMEKIT_LOG_DIR="${AIGAMEKIT_LOG_DIR:-$DATA_DIR/logs}"

mkdir -p "$DATA_DIR/remote" "$DATA_DIR/logs" \
         "$DATA_DIR/models/hf" "$DATA_DIR/models/aigamekit" "$DATA_DIR/models/u2net"

if [ ! -s "$VRAMD_TOKEN_FILE" ]; then
    # Token estável criado uma vez (0600); o deploy lê-o para o cliente.
    umask 077
    python3 - <<'PY' > "$VRAMD_TOKEN_FILE"
import secrets
print(secrets.token_urlsafe(32))
PY
    chmod 600 "$VRAMD_TOKEN_FILE"
fi

echo "vramd servidor: listen=$VRAMD_LISTEN cache=$DATA_DIR modelos=$HF_HOME"
exec vramd start
