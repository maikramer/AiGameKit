#!/usr/bin/env bash
# =============================================================================
# Teste de instalação + inferência em Ubuntu limpo — wrapper HOST.
#
# Faz build da imagem (contexto minimizado pelo .dockerignore) e corre o
# teste completo dentro do container. O teste pode demorar 1-3 h: instalação
# de todas as tools + INFERÊNCIA REAL (cada tool gera um objeto de verdade).
#
# Layout de disco (importante): o repo pode estar num disco separado/quota —
# os venvs ficam em $ARTIFACTS_DIR/venvs (no disco do repo) e são limpos entre
# grupos. O cache uv do HOST é montado em /root/.cache/uv.
#
# Caches do host montados para evitar downloads massivos:
#   - ~/.cache/huggingface  (modelos HF → /root/.cache/huggingface)
#   - ~/.cache/uv           (wheels → /root/.cache/uv)
# GPU: se `nvidia-smi` existir no host, corre com `--gpus all` (fallback a
# CPU se o runtime falhar).
#
# Uso:
#   scripts/docker/ubuntu-clean-test.sh                    # 2 grupos (tudo)
#   TEST_TOOLS="rocks3d materialize" ...                   # subset (1 grupo)
#   SKIP_INFERENCE=1 ...                                   # só instalação
#   NO_BUILD=1 ...                                         # reusa imagem
#
# Variáveis:
#   TEST_TOOLS        lista de tools (default: 2 grupos cobrem todas)
#   TEST_INFERENCE    0 desliga a fase de inferência
#   ARTIFACTS_DIR     onde guardar logs (default: <repo>/logs/ubuntu-clean-test)
#   NO_BUILD          =1 para saltar o docker build
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="aigamekit-ubuntu-clean:test"
DOCKERFILE="$REPO/scripts/docker/Dockerfile.ubuntu-clean"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$REPO/logs/ubuntu-clean-test}"

command -v docker >/dev/null || { echo "docker não encontrado no PATH." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon não está a correr (docker info falhou)." >&2; exit 1; }

echo "== Repo       : $REPO"
echo "== Artefactos : $ARTIFACTS_DIR"

# --- Limpeza preventiva de espaço (logs antigos de grupos anteriores) -------
rm -f "$REPO"/logs/ubuntu-clean-test.grupo-*.log 2>/dev/null || true
rm -rf "$REPO"/logs/grupo-* 2>/dev/null || true
echo "== Logs antigos de grupos anteriores removidos"

if [ "${NO_BUILD:-0}" != "1" ]; then
  echo "== Build da imagem (o .dockerignore minimiza o contexto)..."
  docker build -f "$DOCKERFILE" -t "$IMAGE" "$REPO"
else
  echo "== NO_BUILD=1 — a reusar imagem existente ($IMAGE)"
fi

mkdir -p "$ARTIFACTS_DIR"

# --- Caches do host (modelos HF + wheels uv) -------------------------------
DOCKER_RUN_ARGS=(-v "$ARTIFACTS_DIR:/artifacts")
for cache in "$HOME/.cache/huggingface" "$HOME/.cache/uv"; do
  if [ -e "$cache" ]; then
    dest="/root${cache#$HOME}"
    DOCKER_RUN_ARGS+=(-v "$cache:$dest")
    echo "== Cache montado: $cache -> $dest"
  fi
done
DOCKER_RUN_ARGS+=(-e HF_HOME=/root/.cache/huggingface -e HF_HUB_CACHE=/root/.cache/huggingface/hub)

# --- Venvs persistidos (no disco do repo; limpos entre grupos) --------------
# Cada <Tool>/.venv é montado a partir do host para não duplicar no overlay do
# docker. O uv copia (hardlinks não funcionam entre binds) — por isso os grupos
# são pequenos o suficiente para caber no disco do repo.
VENVS_DIR="$ARTIFACTS_DIR/venvs"
mkdir -p "$VENVS_DIR"
while read -r tool folder; do
  [ -z "$tool" ] && continue
  mkdir -p "$VENVS_DIR/$tool"
  DOCKER_RUN_ARGS+=(-v "$VENVS_DIR/$tool:/workspace/AiGameKit/$folder/.venv")
done < <(awk '/^  [a-z0-9]+:/{tool=substr($1,1,length($1)-1)} /^    folder:/{print tool, $2}' "$REPO/tools.yaml")
echo "== Venvs persistidos: $VENVS_DIR"

# --- GPU -------------------------------------------------------------------
GPU_FLAGS=()
if command -v nvidia-smi >/dev/null 2>&1 || [ -x /usr/bin/nvidia-smi ]; then
  GPU_FLAGS=(--gpus all)
  echo "== GPU detetada no host — a correr com --gpus all (fallback a CPU se falhar)"
fi

# --- Grupos -----------------------------------------------------------------
# Default: 2 grupos que cobrem todas as tools (limite: disco do repo).
# Grupo A (leve)  — modelserver rocks3d texture2d text2icon text2sound
#                   skymap2d aigamekitlab materialize
# Grupo B (GPU)   — cadeia 3D + integração: modelserver text2d text3d paint3d
#                   rigging3d animator3d terrain3d vibegame gameassets
# (modelserver em AMBOS: o UMS gere a VRAM 6 GB com sdnq-int4 — sem ele, o
# ensure_vram legacy recusa fp16 e a cadeia 3D não arranca.)
GROUP_A="modelserver rocks3d texture2d text2icon text2sound skymap2d aigamekitlab materialize"
GROUP_B="modelserver text2d text3d paint3d rigging3d animator3d terrain3d vibegame gameassets"

clean_artifacts() {
  # Apaga TUDO (incl. venvs persistidos) e recria o dir de venvs — o uv
  # (--clear) recusa limpar dirs que não são venv, por isso os dirs têm de
  # existir VAZIOS de forma previsível.
  docker run --rm -v "$ARTIFACTS_DIR:/artifacts" ubuntu:24.04 \
    sh -c 'rm -rf /artifacts/* && mkdir -p /artifacts/venvs'
}

run_group() {
  local name="$1" tools="$2"
  echo
  echo "############################################################"
  echo "# GRUPO $name — $tools"
  echo "############################################################"
  if [ -n "$(ls -A "$ARTIFACTS_DIR" 2>/dev/null)" ]; then
    echo "== A limpar artefactos do grupo anterior..."
    clean_artifacts
  fi
  echo "== A correr o teste (instalação + inferência; pode demorar 30-90 min)..."
  echo "== Log ao vivo: $ARTIFACTS_DIR/ubuntu-clean-test.log"

  # Exit codes do docker run:
  #   0/1  → resultado do teste (0 = sem falhas) — NUNCA refazer
  #   125/126/127 → erro de arranque (runtime GPU, comando, daemon) → tentar sem GPU
  local run_env=(-e "TEST_TOOLS=$tools" -e "TEST_INFERENCE=${TEST_INFERENCE:-1}" -e "SKIP_PRE=${SKIP_PRE:-0}")
  set +e
  docker run --rm "${GPU_FLAGS[@]}" "${DOCKER_RUN_ARGS[@]}" "${run_env[@]}" "$IMAGE"
  local rc=$?
  set -e
  # Log do grupo preservado FORA dos artefactos (o próximo grupo limpa-os)
  if [ -f "$ARTIFACTS_DIR/ubuntu-clean-test.log" ]; then
    cp "$ARTIFACTS_DIR/ubuntu-clean-test.log" "$REPO/logs/ubuntu-clean-test.grupo-$name.log" 2>/dev/null || true
    mkdir -p "$REPO/logs/grupo-$name"
    cp "$ARTIFACTS_DIR"/infer-*.log "$ARTIFACTS_DIR"/install-*.log "$ARTIFACTS_DIR"/smoke-*.log "$ARTIFACTS_DIR"/step-*.log \
      "$REPO/logs/grupo-$name/" 2>/dev/null || true
  fi
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
    return "$rc"
  fi
  if [ "${#GPU_FLAGS[@]}" -gt 0 ]; then
    echo "== Arranque falhou (exit $rc) com GPU — a re-tentar sem --gpus all"
    set +e
    docker run --rm "${DOCKER_RUN_ARGS[@]}" "${run_env[@]}" "$IMAGE"
    rc=$?
    set -e
    return "$rc"
  fi
  return "$rc"
}

FAILED_GROUPS=()
if [ -n "${TEST_TOOLS:-}" ]; then
  run_group "único" "$TEST_TOOLS" || FAILED_GROUPS+=("único")
else
  run_group "A (leve)" "$GROUP_A" || FAILED_GROUPS+=("A")
  run_group "B (GPU/cadeia 3D)" "$GROUP_B" || FAILED_GROUPS+=("B")
fi

echo
if [ "${#FAILED_GROUPS[@]}" -gt 0 ]; then
  echo "== GRUPOS COM FALHAS: ${FAILED_GROUPS[*]}"
  exit 1
fi
echo "== Todos os grupos concluíram sem falhas (exit 0)."
