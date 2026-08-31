#!/usr/bin/env bash
# instance-lock.sh — lock de instância Viber para testes multi-agente.
#
# PROBLEMA: vários agentes correm `viber run` de teste em paralelo e se
# matam uns aos outros com pgrep/kill. Este lock garante UMA instância de
# teste de cada vez por máquina: quem não consegue o lock ESPERA ou desiste
# — nunca mata o processo do outro.
#
# ⚠️ REGRAS (ver docs/findings/VIBER_MULTI_AGENT.md):
#   1. Antes de rodar uma instância de teste, ADQUIRA o lock.
#   2. O teste LIBERTA o lock no fim: no modo sourced o acquire instala um
#      `trap ... EXIT` automaticamente; `exec --` liberta ao terminar o
#      comando.
#   3. NUNCA `pkill`/`kill` processos viber alheios. Se `is-locked`, a
#      instância é de outro agente: aguarde ou re-agende o teste.
#   4. Lock órfão (PID morto) é detectado como stale e pode ser assumido.
#
# USO RECOMENDADO — script de teste (sourced; o trap EXIT liberta sozinho):
#
#   #!/usr/bin/env bash
#   source "$(dirname "$0")/instance-lock.sh"
#   viber_lock_acquire "meu-teste" || exit 1   # trap EXIT já instalado
#   cargo run -p viber -- run examples/simple-rpg/world.xml
#   # release acontece no EXIT — inclusive com erro ou Ctrl-C.
#
# USO ALTERNATIVO — wrapper de um comando:
#
#   scripts/instance-lock.sh exec -- cargo run -p viber -- run world.xml
#
# MODO INTERATIVO — segurar o lock manualmente (Ctrl-C liberta):
#
#   scripts/instance-lock.sh acquire <nome-do-agente>
#
# CONSULTA:
#
#   scripts/instance-lock.sh is-locked   # exit 0 = instância a correr
#   cat /tmp/viber-instance.lock         # PID + timestamp + dono
#
# Subcomandos: acquire [dono] | release | is-locked | exec -- <cmd...>
# Funções (quando sourced): viber_lock_acquire / viber_lock_release /
# viber_lock_is_locked.
#
# Override do caminho do lock: env VIBER_INSTANCE_LOCK=<ficheiro>.

# shellcheck shell=bash

VIBER_INSTANCE_LOCK="${VIBER_INSTANCE_LOCK:-/tmp/viber-instance.lock}"

# viber_lock_is_locked [quiet]
# Exit 0 se o lock existe e é de um processo VIVO; exit 1 caso contrário
# (sem lock, ou lock stale de processo morto). Imprime o estado.
viber_lock_is_locked() {
    local quiet="${1:-}"
    local pid holder
    if [[ ! -f "$VIBER_INSTANCE_LOCK" ]]; then
        [[ "$quiet" == "quiet" ]] || echo "FREE: sem lock ($VIBER_INSTANCE_LOCK)"
        return 1
    fi
    pid="$(sed -n 's/^PID=//p' "$VIBER_INSTANCE_LOCK" 2>/dev/null | head -1)"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        [[ "$quiet" == "quiet" ]] || echo "STALE: lock órfão (PID '${pid:-?}' morto) — acquire assume"
        return 1
    fi
    holder="$(sed -n 's/^HOLDER=//p' "$VIBER_INSTANCE_LOCK" | head -1)"
    [[ "$quiet" == "quiet" ]] || echo "LOCKED: instância a correr (PID $pid, dono: ${holder:-?})"
    return 0
}

# viber_lock_acquire [dono]
# Adquire o lock atómicamente (noclobber). Falha (exit 1) se outro processo
# VIVO o detém — nessa caso NÃO matar: aguardar. Assume locks stale.
# Com VIBER_LOCK_SOURCED=1 (setado quando o script é sourced) instala um
# `trap viber_lock_release EXIT` (+ INT/TERM) para libertar no fim do teste.
viber_lock_acquire() {
    local holder="${1:-${VIBER_LOCK_HOLDER:-unknown}}"
    if viber_lock_is_locked quiet; then
        local cur_pid cur_ts cur_holder
        cur_pid="$(sed -n 's/^PID=//p' "$VIBER_INSTANCE_LOCK" | head -1)"
        cur_ts="$(sed -n 's/^TIMESTAMP=//p' "$VIBER_INSTANCE_LOCK" | head -1)"
        cur_holder="$(sed -n 's/^HOLDER=//p' "$VIBER_INSTANCE_LOCK" | head -1)"
        echo "ERRO: instância Viber já a correr (PID $cur_pid, dono: '${cur_holder:-?}', desde $cur_ts)." >&2
        echo "      NÃO faça kill/pkill — é a instância de teste de outro agente." >&2
        echo "      Aguarde o fim do dono (poll: instance-lock.sh is-locked) ou re-agende." >&2
        return 1
    fi
    if [[ -f "$VIBER_INSTANCE_LOCK" ]]; then
        echo "AVISO: lock stale detectado ($(tr '\n' ' ' < "$VIBER_INSTANCE_LOCK")) — a assumir." >&2
        rm -f "$VIBER_INSTANCE_LOCK"
    fi
    # Criação atómica: noclobber falha se outro processo criar entretanto.
    if ! (set -o noclobber; printf 'PID=%s\nTIMESTAMP=%s\nHOLDER=%s\n' "$$" "$(date -Is)" "$holder" > "$VIBER_INSTANCE_LOCK") 2>/dev/null; then
        echo "ERRO: perdeu a corrida pelo lock ($(tr '\n' ' ' < "$VIBER_INSTANCE_LOCK" 2>/dev/null))." >&2
        return 1
    fi
    # Modo sourced/script: garante libertação no fim (EXIT, Ctrl-C, kill TERM).
    if [[ "${VIBER_LOCK_SOURCED:-0}" == "1" ]]; then
        trap 'viber_lock_release' EXIT
        trap 'viber_lock_release; trap - INT; kill -INT $$' INT
        trap 'viber_lock_release; trap - TERM; kill -TERM $$' TERM
    fi
    echo "LOCK adquirido por '${holder}' (PID $$) → $VIBER_INSTANCE_LOCK"
    return 0
}

# viber_lock_release
# Remove o lock se fôrmos o dono registado (ou se esse PID já estiver morto).
# Idempotente: sem lock, sai 0.
viber_lock_release() {
    if [[ ! -f "$VIBER_INSTANCE_LOCK" ]]; then
        return 0
    fi
    local pid
    pid="$(sed -n 's/^PID=//p' "$VIBER_INSTANCE_LOCK" | head -1)"
    if [[ "$pid" == "$$" ]] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$VIBER_INSTANCE_LOCK"
        echo "LOCK libertado (PID $$)"
    else
        echo "AVISO: lock pertence a outro processo vivo (PID $pid) — não libertado." >&2
        return 1
    fi
}

# ── Modo sourced vs CLI ─────────────────────────────────────────────────
# Sourced (padrão dos testes): expõe as funções e marca o modo para o
# acquire instalar o trap EXIT. CLI: roda o case de subcomandos.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    VIBER_LOCK_SOURCED=1
    return 0 2>/dev/null || true
fi

case "${1:-}" in
    acquire)
        # Modo interativo: adquire e mantém o lock enquanto este processo
        # viver (Ctrl-C / SIGTERM libertam via trap). Para testes
        # automáticos prefira sourced + viber_lock_acquire, ou `exec --`.
        VIBER_LOCK_SOURCED=1
        viber_lock_acquire "${2:-interactive}" || exit 1
        while sleep 3600; do :; done
        ;;
    release)
        viber_lock_release
        ;;
    is-locked)
        viber_lock_is_locked
        ;;
    exec)
        shift
        [[ "${1:-}" == "--" ]] && shift
        if [[ $# -eq 0 ]]; then
            echo "ERRO: uso: instance-lock.sh exec -- <comando [args...]>" >&2
            exit 2
        fi
        VIBER_LOCK_SOURCED=1
        viber_lock_acquire "exec: $*" || exit 1
        "$@" # o trap EXIT (instalado pelo acquire) liberta no fim
        ;;
    ""|-h|--help|help)
        sed -n '2,47p' "$0" | sed -n 's/^# \{0,1\}//p'
        ;;
    *)
        echo "ERRO: subcomando desconhecido '$1' (ver: instance-lock.sh --help)." >&2
        exit 2
        ;;
esac
