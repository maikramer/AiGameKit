---
name: viber-live-qa
description: Usar para testar, fazer QA ou debug de um mundo Viber a correr — protocolo de sessão partilhada (claim/release), debug bridge BRP na porta 15702 (screenshot, burst de flicker, input sintético, tree, logs), REPL Luau viber.debug.* (VER/MEXER/PROVAR/DESCOBRIR), edição viva viber scene --live, diff golden images, determinismo world_hash. Viber live QA, debug bridge, screenshot, deterministic step.
---

# Viber — QA vivo (engine a correr)

O debug bridge (`viber run --bridge`, BRP sobre HTTP, porta **15702**) é o caminho
do agente para VER, MEXER, PROVAR e DESCOBRIR a engine viva — o equivalente nativo
do Chrome DevTools. Doc canónica: `Viber/docs/BRIDGE.md`. Métodos: `references/bridge-methods.md`.

## Protocolo de sessão (OBRIGATÓRIO)

Uma engine por MUNDO, partilhada por todos os agentes paralelos (várias engines
esgotam a VRAM da GPU). Lease explícito — ver `references/session-gotchas.md`:

```bash
viber session status                    # SEM SESSÃO | LIBERADO | OCUPADO por X | engine MORTA
viber session up                        # sobe a engine + espera o bridge (porta livre auto)
viber session claim --owner <tarefa>    # exit 0 = conseguido; exit 3 = OCUPADO (não girar!)
# … 1–2 testes (debug lua/screenshot/…) e:
viber session release --owner <tarefa>  # liberta
viber session touch --owner <tarefa>    # renova TTL (300 s) em QA longa
```

- **Nunca lançar `viber run --bridge` diretamente** para QA — `session status`
  primeiro; sem sessão, `session up`.
- **Nunca matar processos de engine** — a via legítima é `viber session down`
  (exige sessão livre). Engine MORTA → `down && up`.
- Claim devolve exit 3 = outro agente tem o lease: fazer outro trabalho e voltar
  (no máximo `claim --wait 120` UMA vez).

## Apontar a engine certa

O cliente resolve a porta: `--port` → `--world` (caminho/nome/stem via `engine.json`
da sessão + ping de identidade) → `VIBER_BRIDGE_PORT` → descoberta implícita → 15702.
Com **várias engines vivas, a escolha implícita é ERRO intencional** (lista-as) —
apontar sempre a tua: `viber debug --world qa-pontes screenshot -o shot.png`.
`viber debug probe` é a exceção (lista em vez de falhar); `viber debug engines`
mostra quem está vivo.

## As quatro capacidades (`viber debug …`)

**VER** — leituras do snapshot do início da chamada: `tree`, `logs`, `prof
[--samples N]`, REPL `viber.debug.*` (entities/find/info/transform/material/
collider/player/quest/vault/terrain(x,z)/nav/around/ui_tree…).

**MEXER** — escritas que aplicam no MESMO frame: REPL (`viber.debug.tp/set_pos/
disable/enable/kill/heal/give/quest_force/vault_set/spawn("box:2,1,2", x,y,z, {…})/
set_material/set_light/postfx{bloom=false,…}/set_clock(1380= noite)/set_camera/
sun{…}/ground{…}/physics_set/rain_look/set_weather`), input sintético
(`key/text/click/move`), edição viva `viber scene --live …` (undo/redo; ops com
inversas; terreno/céu/UI/spawners ficam `needs_reload`).

**PROVAR** — `events --since N` (ring estruturado de dano/morte/quest/toast),
`step <n>`/`play` (pausa e avança EXATAMENTE n frames; `step 0` só congela),
`burst --frames 9 --skip N --stats` (veredicto numérico de flicker),
`diff a.png b.png [--roi] [--threshold %]` (exit 1 acima do limiar) + goldens
(`diff --baseline qa/golden atual.png [--update]`), `watch --lua 'expr' --hz 10
--for 5`, `test <cenario.lua>` (helpers expect/expect_near, exit 1 em falha),
`hash` (determinismo "mesma seed, mesmo mundo" num número — SÓ estável com o
mundo congelado `step 0`).

**DESCOBRIR** — a engine explica-se: `viber debug api [--grep x] [--json]`
(assinaturas de CADA `viber.debug.*` + enumeração viva de `viber.*`/`viber.ui.*`),
`methods` (todos os BRP), `schema --grep PointLight` (campos/tipos refletidos para
`world.mutate_components`), `logs --level warn --grep nav`.

## Loop típico

```bash
viber session up
viber session claim --owner <tarefa> || return
viber debug --world <mundo> screenshot -o antes.png        # baseline
viber debug --world <mundo> lua 'viber.debug.tp(0, -20) return true'   # mexer
viber debug --world <mundo> screenshot -o depois.png       # verificar
viber debug --world <mundo> diff antes.png depois.png --threshold 0.5
viber debug --world <mundo> logs --level warn
viber session release --owner <tarefa>
```

Screenshot é **request+poll** (~1–3 frames). Para movimento/flicker usar `burst`
(folha √N×√N, células com o formato do frame; `--skip 0` flicker de 1 frame,
skips maiores para caminhada/combate). Janela minimizada não captura (stall 30 s
→ error).

## Verificação: ler DEPOIS de escrever

Reads = snapshot do INÍCIO da chamada: uma escrita e a sua leitura de verificação
têm de ser **chamadas separadas**. Ex.: `lua 'viber.debug.tp(12, 8) return true'`
e depois `lua 'return viber.debug.player()'` (ou screenshot/tree). A env da REPL é
única e persiste entre chamadas (globals sobrevivem).

## Limites e armadilhas — ler `references/session-gotchas.md`

Snapshots com caps (4096 entidades, 256 colliders/lights, 128 around, cauda 256
dos events); `stats()` custa ~100 ms (não poluir medições); sem guard de
instruções na REPL (`while true do end` congela o frame); `world_hash` exige
mundo congelado; ops de debug que escrevem direto NÃO geram eventos (provocar com
gameplay real); screenshots pretos com GPU disputada; janela VISÍVEL para medir
performance (ocluída = números de lixo).

## MCP do plugin

Com o plugin instalado, as tools MCP (`viber_screenshot` devolve a imagem inline,
`viber_lua`, `viber_scene`, `viber_input`, `viber_status`, `viber_tree`,
`viber_logs`, `viber_burst`) envolvem o mesmo CLI — mesmas flags `--world`/`--port`.
O protocolo de sessão (claim/release) continua pela shell.

## Referências

- `references/bridge-methods.md` — métodos BRP + API `viber.debug.*` por camada
- `references/session-gotchas.md` — protocolo de sessão detalhado + armadilhas
