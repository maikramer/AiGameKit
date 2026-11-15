---
description: QA vivo do jogo Vael — bridge, screenshot, burst, input e REPL Luau
argument-hint: [world.bsn]
allowed-tools: Bash, Read, mcp__viber__viber_status, mcp__viber__viber_screenshot, mcp__viber__viber_burst, mcp__viber__viber_logs, mcp__viber__viber_tree, mcp__viber__viber_lua, mcp__viber__viber_input
---

# QA vivo do Vael

Mundo alvo: `$ARGUMENTS` (vazio = `world.bsn`). O CLI é `vael-tools`; o editor
oficial é o Jackdaw (`jd open .`). Segue o skill **viber-live-qa**.

1. **Pré-validação**: `vael-tools analyze` — não avançar com erros.
2. **Arrancar**: `jd run` (normal, sem argumentos) ou
   `vael-tools run --scene main --entry dev --bridge 15706` (atalho de debug).
3. **Esperar**: `vael-tools debug --port <p> wait-ready --timeout 300` antes de
   qualquer screenshot/input. O carregamento demora dezenas de segundos e a
   bridge responde devagar durante o load.
4. **Baseline**: `viber_screenshot` e `viber_logs`. Verificar a identidade
   servida (`viber_status`) antes de confiar no screenshot.
5. **Observar**: `viber_burst --frames 9 [--skip N] [--stats]` para
   movimento/flicker; `viber_lua` com `viber.debug.player()/info(id)/around()`
   para posição/estado; `viber.debug.set_camera{…}`/`set_clock(1380)` para
   cenas/estado.
6. **Agir**: input sintético (`viber_input`), REPL (`tp`, `spawn`, `give`,
   `postfx{…}`). Escrita e leitura de verificação em CHAMADAS SEPARADAS.
7. **Provar**: comparar screenshots para regressão visual; `step <n>` +
   `hash` para determinismo; `events --since N` para "aconteceu?".

Reportar: o que se viu (screenshots + conclusão), o que se mudou e o veredicto.
Nunca matar o processo do jogo.
