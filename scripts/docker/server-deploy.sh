#!/usr/bin/env bash
# Deploy do servidor de GPU do AiGameKit (vramd remoto) a partir desta máquina.
#
#   scripts/docker/server-deploy.sh preflight
#   scripts/docker/server-deploy.sh install-gpu-runtime
#   scripts/docker/server-deploy.sh sync-repo
#   scripts/docker/server-deploy.sh sync-models     # uma vez (134G+)
#   scripts/docker/server-deploy.sh sync-model models--org--nome [subpasta]
#   scripts/docker/server-deploy.sh build            # na máquina do servidor
#   scripts/docker/server-deploy.sh build-local      # nesta máquina (internet melhor)
#   scripts/docker/server-deploy.sh push-image       # build-local → servidor (zstd)
#   scripts/docker/server-deploy.sh up | up-image | ps | logs | down
#   scripts/docker/server-deploy.sh token            # escreve ~/.config/vramd/remote.env
#   scripts/docker/server-deploy.sh all
#
# Env: AIKIT_HOST (root@192.168.0.100), AIKIT_REMOTE_ROOT (/srv/aigamekit),
#      AIKIT_CACHE (/srv/vramd-cache), AIKIT_REPO ($REMOTE_ROOT/repo),
#      AIKIT_PORT (8790), AIKIT_MODELS=full|subset
set -euo pipefail

HOST="${AIKIT_HOST:-root@192.168.0.100}"
REMOTE_ROOT="${AIKIT_REMOTE_ROOT:-/srv/aigamekit}"
CACHE="${AIKIT_CACHE:-/srv/vramd-cache}"
REPO="${AIKIT_REPO:-$REMOTE_ROOT/repo}"
PORT="${AIKIT_PORT:-8790}"
MODELS="${AIKIT_MODELS:-subset}"
HOST_ONLY="${HOST#*@}"

LOCAL_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="scripts/docker/docker-compose.server.yml"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

# O root do servidor tem um contexto Docker remoto gravado (denv-locatelli…)
# que já não resolve; o engine local é o `default`. Forçamos por comando para
# não mexer na config global de quem usa o servidor.
remote_docker() { remote "DOCKER_CONTEXT=default docker $*"; }

preflight() {
    say "preflight $HOST"
    remote 'hostname; . /etc/os-release && echo "$PRETTY_NAME"; uname -m; \
        echo ---GPU---; nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv; \
        echo ---DISCO---; df -h / /srv 2>/dev/null | tail -2; \
        echo ---DOCKER---; DOCKER_CONTEXT=default docker --version; DOCKER_CONTEXT=default docker compose version; \
        echo ---CONTEXTO---; DOCKER_CONTEXT=default docker ps --format "{{.Names}} {{.Image}}" | head -5; \
        echo ---GPU-RUNTIME---; (nvidia-ctk --version && nvidia-container-cli --version) 2>&1 | head -3'
}

install_gpu_runtime() {
    say "nvidia-container-toolkit em $HOST"
    ssh -o BatchMode=yes "$HOST" bash -s <<'EOF'
set -euo pipefail
if command -v nvidia-ctk >/dev/null 2>&1; then
    echo "nvidia-ctk já instalado: $(nvidia-ctk --version | head -1)"
else
    install -m 0755 -d /usr/share/keyrings
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
fi
nvidia-ctk --version
EOF
    say "teste de GPU no Docker"
    remote_docker 'run --rm --gpus all nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi -L'
}

prepare_dirs() {
    say "diretórios no servidor"
    remote "mkdir -p '$CACHE'/models/hf/hub '$CACHE'/models/aigamekit '$CACHE'/models/u2net '$CACHE'/remote '$CACHE'/logs '$REPO' && chown -R 1000:1000 '$CACHE'"
}

sync_repo() {
    say "repo → $HOST:$REPO"
    remote "mkdir -p '$REPO'"
    rsync -az --delete --info=progress2 \
        --exclude '.git/lfs' \
        --exclude '**/.venv' \
        --exclude '**/__pycache__' \
        --exclude '**/*.pyc' \
        --exclude '**/.pytest_cache' \
        --exclude '**/.ruff_cache' \
        --exclude '**/*.egg-info' \
        --exclude '**/node_modules' \
        --exclude '**/target' \
        --exclude '**/dist' \
        --exclude 'outputs' \
        --exclude 'logs' \
        --exclude 'experiments' \
        --exclude '.installer-venv' \
        "$LOCAL_REPO/" "$HOST:$REPO/"
    # Sem .git a descoberta do monorepo falha; o .dockerignore já o incluía.
    remote "test -d '$REPO/.git' && echo 'ok: .git presente' || echo 'ERRO: .git em falta'"
}

sync_models() {
    prepare_dirs
    say "HF hub → cache do vramd (exclude = legado FLUX.2/SD1.5/FLUX.1)"
    local hf="${HF_HOME:-$HOME/.cache/huggingface}"
    local excludes=()
    if [ "$MODELS" = "subset" ]; then
        excludes=(
            --exclude 'models--black-forest-labs--FLUX.2-klein-4B*'
            --exclude 'models--stable-diffusion-v1-5--stable-diffusion-v1-5*'
            --exclude 'models--Disty0--FLUX.1-dev-SDNQ-uint4-svd-r32*'
        )
    fi
    rsync -aH --info=progress2 "${excludes[@]}" \
        "$hf/hub/" "$HOST:$CACHE/models/hf/hub/"
    say "u2net (ícones transparentes)"
    rsync -a --info=progress2 "$HOME/.u2net/" "$HOST:$CACHE/models/u2net/"
    say "Hi3D (refine)"
    rsync -a --info=progress2 "$HOME/.cache/aigamekit/models/" "$HOST:$CACHE/models/aigamekit/"
    remote "chown -R 1000:1000 '$CACHE/models'"
}

# Copia um modelo HF (o dir + os blobs referenciados pelos symlinks), sem levar
# o cache inteiro. `sub` limita a uma subpasta do repo (ex.: o paint do
# Hunyuan3D-2.1). Uso: sync-model models--org--nome [subpasta]
sync_model() {
    local model="${1:?uso: sync-model models--org--nome [subpasta]}"
    local sub="${2:-}"
    local hub="${HF_HOME:-$HOME/.cache/huggingface}/hub"
    local list
    list="$(mktemp)"
    python3 - "$hub" "$model" "$sub" > "$list" <<'PY'
import sys
from pathlib import Path

hub = Path(sys.argv[1]).resolve()
root = hub / sys.argv[2]
sub = sys.argv[3].strip("/")
if not root.is_dir():
    sys.exit(f"modelo inexistente: {root}")
for path in root.rglob("*"):
    rel = path.relative_to(root).as_posix()
    if sub and not (rel == sub or sub in rel):
        continue
    print(path.relative_to(hub).as_posix())
for path in root.rglob("*"):
    rel = path.relative_to(root).as_posix()
    if sub and not (rel == sub or sub in rel):
        continue
    if path.is_symlink():
        target = path.resolve()
        if target.is_file():
            try:
                print(target.relative_to(hub).as_posix())
            except ValueError:
                continue
PY
    local size
    size="$(du -sh "$hub/$model" | cut -f1)"
    say "$model${sub:+ [$sub]} ($size) → cache do vramd"
    rsync -aHW --partial --timeout=60 --info=progress2 --files-from="$list" "$hub/" "$HOST:$CACHE/models/hf/hub/"
    rm -f "$list"
    remote "chown -R 1000:1000 '$CACHE/models/hf'"
}

compose_cmd() {
    remote "cd '$REPO' && DOCKER_CONTEXT=default docker compose -f '$COMPOSE' $*"
}

build() {
    say "build da imagem (longo: torch/bpy/hi3d)"
    remote "cd '$REPO' && DOCKER_CONTEXT=default BUILDKIT_PROGRESS=plain docker compose -f '$COMPOSE' build"
}

# Fallback quando a internet do servidor é fraca: build nesta máquina (que tem
# o repo e uma ligação melhor) e envio da imagem por LAN comprimida.
build_local() {
    say "build local ($LOCAL_REPO)"
    ( cd "$LOCAL_REPO" && DOCKER_CONTEXT=default docker build -f scripts/docker/Dockerfile.server -t aigamekit-server:local . )
}

push_image() {
    say "imagem → servidor (zstd -3 por ssh)"
    DOCKER_CONTEXT=default docker save aigamekit-server:local \
        | zstd -T0 -3 \
        | ssh -o BatchMode=yes "$HOST" 'zstd -d | DOCKER_CONTEXT=default docker load'
}

up_image() {
    prepare_dirs
    say "up (imagem carregada, sem build)"
    compose_cmd "up -d --no-build"
    compose_cmd "ps"
}

up() {
    prepare_dirs
    say "up"
    compose_cmd "up -d"
    compose_cmd "ps"
}

logs() { compose_cmd "logs --tail=120 -f"; }
down() { compose_cmd "down"; }
ps() { compose_cmd "ps"; }

write_token() {
    say "token do servidor → ~/.config/vramd/remote.env"
    local token
    token="$(remote "cat '$CACHE/token'")"
    if [ -z "$token" ]; then
        echo "ERRO: $CACHE/token vazio (o container já arrancou?)" >&2
        exit 1
    fi
    local dir="$HOME/.config/vramd"
    mkdir -p "$dir"
    umask 077
    cat > "$dir/remote.env" <<EOF
# Gerado por server-deploy.sh — não commitar.
export VRAMD_REMOTE=$HOST_ONLY:$PORT
export VRAMD_REMOTE_TOKEN=$token
export VRAMD_AUTO_START=0
EOF
    chmod 600 "$dir/remote.env"
    echo "ok: $dir/remote.env"
}

case "${1:-all}" in
    preflight) preflight ;;
    install-gpu-runtime) install_gpu_runtime ;;
    sync-repo) sync_repo ;;
    sync-models) sync_models ;;
    sync-model) shift; sync_model "$@" ;;
    build) build ;;
    build-local) build_local ;;
    push-image) push_image ;;
    up-image) up_image ;;
    up) up ;;
    down) down ;;
    ps) ps ;;
    logs) logs ;;
    token) write_token ;;
    all)
        preflight
        install_gpu_runtime
        sync_repo
        build
        up
        write_token
        ;;
    *)
        echo "uso: $0 {preflight|install-gpu-runtime|sync-repo|sync-models|sync-model|build|build-local|push-image|up|up-image|down|ps|logs|token|all}" >&2
        exit 2
        ;;
esac
