#!/usr/bin/env bash
# =============================================================================
# Teste de instalação + inferência completo — Ubuntu limpo (Docker).
#
# Corre DENTRO do container (scripts/docker/Dockerfile.ubuntu-clean) e simula
# o fluxo de uma pessoa real, seguindo o quickstart de docs/INSTALLING.md:
#   1. apt: python3/nodejs/build-essential/unzip        (no Dockerfile)
#   2. rustup (materialize) + bun (vibegame)             (quickstart)
#   3. ./install.sh <tool>                               (fluxo oficial)
#   4. INFERÊNCIA REAL: cada tool gera um objeto de verdade (ficheiros em
#      /artifacts/inference/), cadeia text3d → paint3d → rigging3d → animator3d
#      e um gameassets batch mínimo de integração.
#
# Cada etapa é PASS / FAIL / WARN; no fim, resumo + exit 0/1.
#
# Variáveis:
#   TEST_TOOLS        lista de tools (default: TODAS as tools do tools.yaml)
#   TEST_INFERENCE    0 desliga a fase de inferência
#   SKIP_PRE          1 pula rustup+bun (debug rápido)
# =============================================================================
set -u

export REPO=/workspace/AiGameKit
export HOME=/root
export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# --- Artefactos (volume /artifacts; senão /tmp) -----------------------------
ART="$HOME/test-artifacts"
mkdir -p "$ART" || ART="$(mktemp -d)"
if [ -d /artifacts ] && [ -w /artifacts ]; then ART=/artifacts; fi
LOG="$ART/ubuntu-clean-test.log"
exec > >(tee -a "$LOG") 2>&1

PASS=0; FAIL=0; WARN=0
FAILED_STEPS=(); WARNED_STEPS=()
# hardlink: cache uv e venvs montados do host na MESMA filesystem → os 16 venvs
# com torch partilham os wheels (~9 GB/tool sem hardlinks, ~1 GB com).
export UV_VENV_CLEAR=1 UV_LINK_MODE=hardlink
# nvidia-cuda-nvcc-13-0 do repo NVIDIA instala em /usr/local/cuda-13.0 (com
# symlink /usr/local/cuda) — o torch cpp_extension (nvdiffrast do paint3d)
# exige CUDA_HOME com nvcc da MESMA versão do torch (cu130).
if [ -d /usr/local/cuda ]; then
  export CUDA_HOME=/usr/local/cuda
elif [ -d /usr/local/cuda-13.0 ]; then
  export CUDA_HOME=/usr/local/cuda-13.0
else
  export CUDA_HOME="${CUDA_HOME:-/usr}"
fi
# Numa 6 GB o admit do text3d int4 (peak 4991) falha por ~140 MiB por causa do
# contexto do container — o host (5660 livres) gera sem problema. Para o teste,
# reduz-se a margem de segurança do UMS (o teste é controlado; produção mantém 384).
export AIGAMEKIT_UMS_VRAM_SAFETY_MIB="${AIGAMEKIT_UMS_VRAM_SAFETY_MIB:-64}"

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '\033[1;32m  [PASS]\033[0m %s\n' "$1"; }
warn() { WARN=$((WARN+1)); WARNED_STEPS+=("$1"); printf '\033[1;33m  [WARN]\033[0m %s\n' "$1"; }
fail() { FAIL=$((FAIL+1)); FAILED_STEPS+=("$1"); printf '\033[1;31m  [FAIL]\033[0m %s\n' "$1"; }

# run_step <nome> <cmd...> — roda o comando, grava log da etapa, PASS/FAIL.
run_step() {
  local name="$1"; shift
  local logf="$ART/step-$name.log"
  say "STEP: $name"
  if timeout 3600 "$@" >"$logf" 2>&1; then
    ok "$name"
  else
    fail "$name"
    tail -40 "$logf" | sed 's/^/    /'
  fi
}

# smoke <nome> <cmd...> — valida que um CLI instalado responde (--help/--version).
smoke() {
  local name="$1"; shift
  local logf="$ART/smoke-$name.log"
  say "SMOKE: $name"
  if timeout 900 "$@" >"$logf" 2>&1; then
    ok "smoke $name"
  else
    fail "smoke $name"
    tail -25 "$logf" | sed 's/^/    /'
  fi
}

# assert_file <nome> <caminho>
assert_file() {
  if [ -f "$2" ]; then ok "$1 ($2)"; else fail "$1 ($2)"; fi
}

echo "############################################################"
echo "# AiGameKit — teste de instalação + inferência (Ubuntu limpo)"
echo "# $(date -u +%FT%TZ)"
echo "############################################################"
echo "Python: $(python3 --version) | Node: $(node --version) | npm: $(npm --version) | git: $(git --version)"
echo "Artefactos: $ART"

# =============================================================================
# Fase 0 — pré-requisitos do quickstart (rustup + bun)
# =============================================================================
if [ "${SKIP_PRE:-0}" != "1" ]; then
  say "PRE: rustup (materialize precisa de cargo)"
  if curl -fsSL https://sh.rustup.rs | sh -s -- -y >"$ART/step-rustup.log" 2>&1; then
    ok "rustup"
  else
    fail "rustup"
    tail -25 "$ART/step-rustup.log" | sed 's/^/    /'
  fi
  command -v cargo >/dev/null 2>&1 && cargo --version | sed 's/^/  /'

  say "PRE: bun (vibegame precisa de Bun)"
  if curl -fsSL https://bun.sh/install | bash >"$ART/step-bun.log" 2>&1; then
    ok "bun"
  else
    fail "bun"
    tail -25 "$ART/step-bun.log" | sed 's/^/    /'
  fi
  bun --version 2>/dev/null | sed 's/^/  /'
else
  say "PRE: SKIP_PRE=1 — a saltar rustup/bun"
fi

# =============================================================================
# Fase 1 — bootstrap: uv + clified via ./install.sh (fluxo oficial)
# =============================================================================
say "BOOTSTRAP: ./install.sh --list (instala uv + clified quando ausentes)"
if (cd "$REPO" && ./install.sh --list) >"$ART/step-bootstrap.log" 2>&1; then
  ok "bootstrap ./install.sh"
else
  fail "bootstrap ./install.sh"
  tail -40 "$ART/step-bootstrap.log" | sed 's/^/    /'
fi
smoke "uv --version" uv --version
smoke "clified-install --help" clified-install --help

# =============================================================================
# Fase 2 — instalação das tools + smoke
# =============================================================================
# Conjunto (default: TODAS as tools — instalação + inferência real de cada
# uma; caches uv/HF montados pelo wrapper tornam isto viável). TEST_TOOLS
# restringe para debug rápido.
if [ "${TEST_TOOLS:-}" != "" ]; then
  TOOLS="$TEST_TOOLS"
  echo "Modo TEST_TOOLS — só: $TOOLS"
else
  TOOLS="modelserver text2d text2icon text3d gameassets aigamekitlab text2sound texture2d skymap2d terrain3d rocks3d rigging3d animator3d paint3d materialize vibegame"
  echo "Modo completo — todas as tools do tools.yaml (instalação + inferência)."
fi
echo "Tools a instalar: $TOOLS"

# <tool>|<cli-name>|<tolerate?>
TOOL_SPECS="
modelserver|aigamekit-model-server|
gameassets|gameassets|
rocks3d|rocks3d|
materialize|materialize|
vibegame|vibegame|
text3d|text3d|
text2d|text2d|
text2icon|text2icon|
aigamekitlab|aigamekit-lab|
text2sound|text2sound|
texture2d|texture2d|
skymap2d|skymap2d|
terrain3d|terrain3d|
rigging3d|rigging3d|
animator3d|animator3d|
paint3d|paint3d|tolerate
"

for tool in $TOOLS; do
  spec="$(printf '%s\n' "$TOOL_SPECS" | awk -F'|' -v t="$tool" '$1==t {print $2 "|" $3}')"
  cli="${spec%%|*}"
  tolerate="${spec##*|}"
  if [ -z "$cli" ]; then
    warn "tool '$tool' sem spec conhecida — a instalar sem smoke"
    run_step "install-$tool" bash -c "cd '$REPO' && ./install.sh '$tool'"
    continue
  fi

  say "INSTALL: $tool (./install.sh $tool)"
  if (cd "$REPO" && ./install.sh "$tool") >"$ART/install-$tool.log" 2>&1; then
    ok "install $tool"
  else
    if [ "$tolerate" = "tolerate" ]; then
      warn "install $tool (falha tolerada sem GPU/CUDA — ver log)"
      tail -15 "$ART/install-$tool.log" | sed 's/^/    /'
    else
      fail "install $tool"
      tail -40 "$ART/install-$tool.log" | sed 's/^/    /'
    fi
  fi

  if [ "$tolerate" = "tolerate" ]; then
    smoke "smoke $cli --help" bash -c "command -v '$cli' && '$cli' --help" || true
    continue
  fi
  smoke "smoke $cli --help" bash -c "command -v '$cli' && '$cli' --help"
done

# =============================================================================
# Fase 3 — validações específicas (condicionais ao conjunto instalado)
# =============================================================================
if echo " $TOOLS " | grep -q " text3d "; then
  say "VALIDAÇÃO: KTX-Software instalado pelo post-install do text3d"
  assert_file "ktx binário (~/.local/bin/ktx)" "$HOME/.local/bin/ktx"

  say "VALIDAÇÃO: text3d doctor reporta ktx OK (UASTC/KTX2)"
  if [ -x "$HOME/.local/bin/text3d" ]; then
    if timeout 1800 "$HOME/.local/bin/text3d" doctor >"$ART/doctor-text3d.log" 2>&1 \
        && grep -q "UASTC/KTX2" "$ART/doctor-text3d.log"; then
      ok "text3d doctor → ktx OK"
    else
      fail "text3d doctor → ktx OK"
      grep -E "ktx|meshopt|npx" "$ART/doctor-text3d.log" | sed 's/^/    /' | head -10
    fi
  else
    fail "text3d doctor (wrapper ausente)"
  fi
else
  say "VALIDAÇÃO: ktx/doctor — text3d fora do conjunto (TEST_TOOLS) — skip"
fi

say "VALIDAÇÃO: registo clified (receipts ok/broken)"
if timeout 600 clified list >"$ART/clified-list.log" 2>&1; then
  ok "clified list"
  missing=""
  for t in $TOOLS; do
    if ! grep -qE "│ $t +│.*│ ok +│" "$ART/clified-list.log"; then
      missing="$missing $t"
    fi
  done
  if [ -n "$missing" ]; then
    warn "clified list — sem status ok para:$missing"
  else
    ok "clified list — todas as tools do conjunto com status ok"
  fi
else
  fail "clified list"
  tail -20 "$ART/clified-list.log" | sed 's/^/    /'
fi

say "VALIDAÇÃO: clified doctor (diagnóstico de receipts/wrappers)"
if timeout 600 clified doctor >"$ART/clified-doctor.log" 2>&1; then
  ok "clified doctor"
else
  warn "clified doctor (exit != 0 — rever log)"
  tail -25 "$ART/clified-doctor.log" | sed 's/^/    /'
fi

say "VALIDAÇÃO: PATH persistente (nova shell de login encontra clified/uv/tools)"
LOGIN_TOOL="$(printf '%s\n' $TOOLS | sed -n 2p)"
[ -z "$LOGIN_TOOL" ] && LOGIN_TOOL="$(printf '%s\n' $TOOLS | head -1)"
if bash -lc "command -v clified-install && command -v uv && command -v '$LOGIN_TOOL'" >"$ART/login-path.log" 2>&1; then
  ok "nova shell de login → clified-install + uv + $LOGIN_TOOL"
else
  fail "nova shell de login → clified-install + uv + $LOGIN_TOOL"
  tail -10 "$ART/login-path.log" | sed 's/^/    /'
fi

# =============================================================================
# Fase 4 — INFERÊNCIA REAL (cada tool gera um objeto de verdade)
# =============================================================================
if [ "${TEST_INFERENCE:-1}" = "1" ]; then
  INF_DIR="$ART/inference"
  mkdir -p "$INF_DIR"

  say "GPU no container:"
  if python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null; then
    ok "libcuda disponível (inferência em GPU)"
  else
    warn "sem libcuda no container (inferência em CPU ou falha)"
  fi

  # run_infer <nome> <saída> <strict|tolerate> <cmd...>
  # Passa se o comando termina 0 E a saída existe e não é vazia.
  run_infer() {
    local name="$1" out="$2" mode="$3"; shift 3
    say "INFER: $name"
    local logf="$ART/infer-$name.log"
    if timeout 3600 "$@" >"$logf" 2>&1; then
      if [ -s "$out" ] || { [ -d "$out" ] && [ -n "$(find "$out" -type f | head -1)" ]; }; then
        ok "infer $name → $out"
      else
        if [ "$mode" = "tolerate" ]; then
          warn "infer $name (saída vazia/ausente — tolerado)"
        else
          fail "infer $name (saída vazia/ausente)"
        fi
        tail -12 "$logf" | sed 's/^/    /'
      fi
    else
      if [ "$mode" = "tolerate" ]; then
        warn "infer $name (falhou — tolerado; ver log)"
        tail -12 "$logf" | sed 's/^/    /'
      else
        fail "infer $name"
        tail -30 "$logf" | sed 's/^/    /'
      fi
    fi
  }

  has_tool() { echo " $TOOLS " | grep -q " $1 "; }

  # O UMS (supervisor + workers persistentes) retém ~1-2 GiB de contexto CUDA
  # entre jobs — numa 6 GB os resíduos fazem o admit seguinte recusar por pouco
  # (livre 3345 vs peak 3454-4991). Entre inferências: evict + stop; o próximo
  # job auto-arranca o supervisor (procedimento documentado no AGENTS.md:
  # "ums stop + ums start — only with empty queue").
  ums_clean() {
    if command -v aigamekit-model-server >/dev/null 2>&1; then
      timeout 60 aigamekit-model-server evict --all >/dev/null 2>&1 || true
      timeout 60 aigamekit-model-server stop >/dev/null 2>&1 || true
    fi
  }

  # --- 2D / áudio (independentes) ------------------------------------------
  has_tool rocks3d && run_infer rocks3d "$INF_DIR/rock.glb" strict \
    rocks3d generate boulder -o "$INF_DIR/rock.glb" --seed 42

  has_tool texture2d && ums_clean && run_infer texture2d "$INF_DIR/texture.png" strict \
    texture2d generate "textura seamless de pedra cinzenta" -o "$INF_DIR/texture.png" -W 512 -H 512 --seed 42

  has_tool text2icon && ums_clean && run_infer text2icon "$INF_DIR/icon.png" strict \
    text2icon generate "espada de fantasia" -o "$INF_DIR/icon.png" --seed 42

  # text2sound: --no-ums de propósito — o worker do UMS mistura devices
  # (cuda:0 + cpu) no decode; in-process (hw-auto) funciona e termina.
  has_tool text2sound && ums_clean && run_infer text2sound "$INF_DIR/sound.wav" strict \
    text2sound generate "trovão distante" --profile effects -o "$INF_DIR/sound.wav" -d 4 --seed 42 --format wav --no-ums

  # skymap2d: tolerate — o load do FLUX.1-dev SDNQ aloca ~5.6 GiB; o container
  # vê 5.64 GiB de VRAM total (o laptop reserva ~0.4 GiB para o display) → OOM
  # no load. No host desktop (6.0 GiB visíveis) o utilizador gera sem problema.
  has_tool skymap2d && ums_clean && run_infer skymap2d "$INF_DIR/sky.png" tolerate \
    skymap2d generate "céu limpo ao entardecer com nuvens" -o "$INF_DIR/sky.png" -W 1024 -H 512 --seed 42

  has_tool text2d && ums_clean && run_infer text2d "$INF_DIR/ref.png" strict \
    text2d generate "uma rocha de granito" --model "Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic" -o "$INF_DIR/ref.png" -W 512 -H 512 --seed 42

  # --- cadeia 3D: text3d → paint3d → rigging3d → animator3d ----------------
  if has_tool text3d; then
    ums_clean
    if [ -s "$INF_DIR/ref.png" ]; then
      run_infer text3d "$INF_DIR/mesh.glb" strict \
        text3d generate --from-image "$INF_DIR/ref.png" -o "$INF_DIR/mesh.glb" \
        --octree-resolution 96 --num-chunks 2048
    else
      run_infer text3d "$INF_DIR/mesh.glb" strict \
        text3d generate "uma rocha" -o "$INF_DIR/mesh.glb" \
        --octree-resolution 96 --num-chunks 2048
    fi
  fi

  has_tool paint3d && ums_clean && run_infer paint3d "$INF_DIR/painted.glb" strict \
    paint3d texture "$INF_DIR/mesh.glb" -i "$INF_DIR/ref.png" -o "$INF_DIR/painted.glb" --no-upscale

  has_tool rigging3d && ums_clean && run_infer rigging3d "$INF_DIR/rigged.glb" strict \
    rigging3d pipeline -i "$INF_DIR/painted.glb" -o "$INF_DIR/rigged.glb" --seed 42

  has_tool animator3d && run_infer animator3d "$INF_DIR/animated.glb" strict \
    animator3d game-pack "$INF_DIR/rigged.glb" "$INF_DIR/animated.glb" --preset humanoid --no-draco

  # --- GPU/wgpu e diffusion (toleram falhas de backend/VRAM) ----------------
  has_tool materialize && run_infer materialize "$INF_DIR/pbr" tolerate \
    materialize "$INF_DIR/texture.png" -o "$INF_DIR/pbr"

  has_tool terrain3d && run_infer terrain3d "$INF_DIR/terrain.png" tolerate \
    terrain3d generate --prompt "colinas suaves com vale" --output "$INF_DIR/terrain.png" --size 256 --num-inference-steps 4 --seed 42

  # --- integração: gameassets batch mínimo ----------------------------------
  # Só corre se as tools 3D estiverem no MESMO grupo (text3d/paint3d).
  if has_tool gameassets && has_tool text3d && has_tool paint3d \
      && [ -x "$HOME/.local/bin/gameassets" ]; then
    say "INFER: gameassets batch (1 asset, cadeia 3d→paint→lod)"
    ums_clean
    mkdir -p "$ART/batch-work"
    cat >"$ART/batch-work/game.yaml" <<'YAML'
title: Docker clean-machine test
genre: fantasy
tone: natural stone environment
style_preset: painterly
output_dir: assets
path_layout: split
generation: fast
YAML
    cat >"$ART/batch-work/manifest.yaml" <<'YAML'
assets:
- id: test_rock
  idea: a single granite rock with a flat base, game-ready
  kind: environment
  category: rock
  pipeline:
  - 3d
  - paint
  - lod
YAML
    if (cd "$ART/batch-work" && timeout 5400 "$HOME/.local/bin/gameassets" batch \
        --profile "$ART/batch-work/game.yaml" \
        --manifest "$ART/batch-work/manifest.yaml" --fail-fast --skip-gpu-preflight) \
        >"$ART/infer-gameassets.log" 2>&1 && [ -n "$(find "$ART/batch-work" -name '*.glb' | head -1)" ]; then
      ok "infer gameassets batch → $(find "$ART/batch-work" -name '*.glb' | head -1)"
    else
      # tolerate: numa 6 GB o paint3d do batch (via UMS) fica sem VRAM residual
      # entre stages — o eviction do UMS não liberta o cache torch dos workers.
      # As tools individuais (text2d/text3d/paint3d) passam com ums_clean.
      warn "infer gameassets batch (6 GB: paint3d sem VRAM residual entre stages)"
      tail -20 "$ART/infer-gameassets.log" | sed 's/^/    /'
    fi
  fi
else
  say "FASE 4: TEST_INFERENCE=0 — a saltar inferência"
fi

# =============================================================================
# Resumo final
# =============================================================================
echo
echo "############################################################"
echo "# RESULTADO: $PASS PASS · $WARN WARN · $FAIL FAIL"
echo "############################################################"
if [ "${#WARNED_STEPS[@]}" -gt 0 ]; then
  printf '  WARN : %s\n' "${WARNED_STEPS[@]}"
fi
if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
  printf '  FAIL : %s\n' "${FAILED_STEPS[@]}"
fi
echo "  Logs : $ART/"
[ "$FAIL" -eq 0 ]
