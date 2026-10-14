---
description: Loop de QA vivo de um mundo Viber — sessão partilhada, bridge, screenshots, edição live e release
argument-hint: [world.xml]
allowed-tools: Bash, Read, mcp__viber__viber_status, mcp__viber__viber_screenshot, mcp__viber__viber_burst, mcp__viber__viber_logs, mcp__viber__viber_tree, mcp__viber__viber_lua, mcp__viber__viber_input, mcp__viber__viber_scene
---

# QA vivo de um mundo Viber

Mundo alvo: `$ARGUMENTS` (se vazio: descobrir — `world.static.xml` → `world.xml` →
`worlds/*.xml` no cwd; perguntar só se houver ambiguidade real).

Segue o skill **viber-live-qa** (protocolo de sessão é OBRIGATÓRIO) e executa:

1. **Pré-validação headless**: `viber analyze <mundo>` — NÃO avançar com erros
   (exit 1). Reportar avisos relevantes.
2. **Sessão**: `viber session status`; se "SEM SESSÃO" → `viber session up`.
   `viber session claim --owner qa-<mundo>-<tarefa>` — se exit 3 (OCUPADO),
   reportar quem tem o lease e parar (não girar). Definir owner único.
3. **Baseline**: `viber debug --world <stem> screenshot -o qa-antes.png` e
   `viber debug --world <stem> logs --level warn`. Verificar o mundo servido
   (ping/probe) antes de confiar no screenshot.
4. **Observar**: se a tarefa envia gameplay/movimento → `burst --frames 9
   [--skip N] [--stats]`; posição/estado → REPL `viber.debug.player()/info(id)/
   around()`. Para poses de câmara/estado: `viber.debug.set_camera{…}`,
   `set_clock(1380)` para noite.
5. **Agir sobre o bug/verificação pedida**: input sintético (key/text/click/move),
   REPL (`tp`, `disable/enable`, `spawn`, `postfx{…}`, `give/heal`…), ou edição
   `viber scene --live …` (ops com undo/redo; verificar com
   `viber.scene.entities` + screenshot). Lembre: escrita e leitura de verificação
   em CHAMADAS SEPARADAS (reads são snapshots do início da chamada).
6. **Provar**: `diff qa-antes.png qa-depois.png [--roi --threshold 0.5]` para
   regressão visual; `events --since N` para "aconteceu?"; `step <n>`+`hash` para
   determinismo; `test <mundo>/qa/*.lua` se existir cenário.
7. **Release SEMPRE** (mesmo com falha a meio): `viber session release --owner <o
   mesmo>`. Engine morta/sessão órfã: `viber session status` → `down && up` só se
   a sessão estiver livre.

Reportar: o que se viu (screenshots como ficheiros + conclusão), o que se mudou,
veredicto do diff/step/hash, e avisos do `logs`. Nunca matar processos de engine.
