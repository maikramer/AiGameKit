#!/bin/bash
# Instalação AiGameKit via Clified (PyPI)
#
#   ./install.sh              → perfil 'core' (10 tools, zero-a-jogo)
#   ./install.sh examples     → core + céu/áudio/texturas/terreno/rochas + Viber
#   ./install.sh --all        → catálogo completo (acrescenta part3d, motion3d, intrinsic)
#   ./install.sh <tool>       → ferramenta individual (chaves de tools.yaml)
#   ./install.sh --list       → listar perfis e ferramentas
#
# O pre-flight (scripts/preflight.py) valida os pré-requisitos externos antes de
# começar e pára uma única vez com todos os comandos para instalar o que falta.
# Bypass para agentes/CI: AIGAMEKIT_PREFLIGHT=0.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLIFIED_TOOLS="${CLIFIED_TOOLS:-$SCRIPT_DIR/tools.yaml}"
export UV_VENV_CLEAR="${UV_VENV_CLEAR:-1}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

# shellcheck source=scripts/install-bootstrap.sh
source "$SCRIPT_DIR/scripts/install-bootstrap.sh"

# Perfis de instalação (docs/findings/TOOLKIT_CORE_PROFILE_STUDY.md):
#   ./install.sh core      — zero-a-jogo mínimo: DAG GLB animado → browser
#   ./install.sh examples  — tudo que os jogos de exemplo usam (core + céu/áudio/
#                            texturas/terreno/rochas + track nativa Viber)
CORE_TOOLS=(vramd text2d text3d paint3d rigging3d animator3d gameassets materialize aigamekitlab vibegame)
EXAMPLES_EXTRA_TOOLS=(texture2d skymap2d text2sound terrain3d rocks3d viber)

run_preflight() {
  local py
  if py="$(clified_resolve_python 2>/dev/null)"; then
    "$py" "$SCRIPT_DIR/scripts/preflight.py" "$@"
  else
    echo "Aviso: sem Python não há pre-flight; o bootstrap falhará com instruções." >&2
  fi
}

print_summary() {
  local profile="$1"; shift
  local tools=("$@")
  local failed_list="${failed[*]:-}"
  echo
  echo "Resumo do perfil '$profile':"
  local t
  for t in "${tools[@]}"; do
    if [[ " $failed_list " == *" $t "* ]]; then
      echo "  ✗ $t — FALHOU"
    else
      echo "  ✓ $t"
    fi
  done
}

next_steps() {
  echo
  echo "Próximos passos:"
  echo "  1. Abre um novo terminal (ou 'source ~/.profile') para garantir ~/.local/bin no PATH"
  echo "  2. gameassets doctor — confirma GPU, compressão GLB e LLM do dream"
  echo "  3. gameassets dream \"A dark fantasy RPG with skeletons and treasure chests\" --dry-run"
}

# Doctor informativo no fim do install (não-fatal; gameassets pode não ter instalado).
maybe_doctor() {
  local ga="$SCRIPT_DIR/GameAssets/.venv/bin/gameassets"
  if [ ! -x "$ga" ]; then ga="$(command -v gameassets 2>/dev/null || true)"; fi
  if [ -n "${ga:-}" ]; then
    echo
    "$ga" doctor || echo "(gameassets doctor falhou — corre-o manualmente mais tarde)" >&2
  fi
}

failed=()
had_args=$#

case "${1:-core}" in
  core|examples)
    profile="$1"
    shift
    tools=("${CORE_TOOLS[@]}")
    if [ "$profile" = "examples" ]; then
      tools+=("${EXAMPLES_EXTRA_TOOLS[@]}")
    fi
    if [ "$had_args" -eq 0 ]; then
      echo "Sem argumentos — a instalar o perfil 'core' (${#tools[@]} tools, zero-a-jogo)."
      echo "Outras opções: ./install.sh examples | --all | <tool> | --list"
    else
      echo "Perfil '$profile': ${tools[*]}"
    fi
    run_preflight --profile "$profile"
    for t in "${tools[@]}"; do
      ( clified_bootstrap "$t" "$@" ) || failed+=("$t")
    done
    print_summary "$profile" "${tools[@]}"
    if [ "${#failed[@]}" -gt 0 ]; then
      echo
      echo "Falharam: ${failed[*]}"
      echo "Repete individualmente: ./install.sh <tool>"
      echo "Depois de tudo OK: make dedupe-venvs --apply   (hardlinks entre venvs, poupa ~dezenas de GB)"
      exit 1
    fi
    maybe_doctor
    next_steps
    ;;
  --list|list)
    run_preflight --list-profiles
    ;;
  --all|all)
    run_preflight --profile all
    all_failed=0
    ( clified_bootstrap --all ) || all_failed=1
    if [ "$all_failed" -eq 0 ]; then
      maybe_doctor
      next_steps
    else
      exit 1
    fi
    ;;
  -*)
    # Flags do clified (--catalog, --doctor, --json, …) passam diretamente.
    clified_bootstrap "$@"
    ;;
  *)
    # Ferramenta individual: pre-flight só para o que ela precisa.
    run_preflight --tools "$1"
    clified_bootstrap "$@"
    ;;
esac
