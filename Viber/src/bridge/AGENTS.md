# AGENTS.md — src/bridge

Escopo: debug bridge — BRP sobre HTTP (`bevy_remote`) com métodos `viber.*`:
screenshots, input sintético, árvore de entidades, logs, profiler e REPL
Luau. É o equivalente nativo do tooling Chrome DevTools MCP do VibeGame.

## Ficheiros

| Ficheiro | Responsabilidade |
|----------|------------------|
| `mod.rs` | server: porta **15702** (`--bridge PORT` muda), constantes `METHOD_*`, handlers `viber.ping/screenshot/screenshot_status/burst/burst_status/tree/logs/profiler/lua/input.*/raycast` + `FrameStepper` (QA frame a frame) |
| `events.rs` | event log ESTRUTURADO: ring de 1000 `{seq,time,kind,...}` (hurt/damage/death/quest/ui/travel/toast/levelup) alimentado por `collect_bridge_events` (PostUpdate); lido por `viber.debug.events(since)` |
| `burst.rs` | burst de frames: N capturas seguidas numa folha única, 4096 no lado comprido, células com o formato do frame (`viber.burst`/`viber.burst_status`); store + pacing (`skip`), observer de `ScreenshotCaptured` (imagem crua, sem encode por frame), composição + PNG numa thread |
| `client.rs` | cliente CLI **std-only** (`viber debug …`); retry no connect (o bind é assíncrono) |
| `logs.rs` | layer de tracing → ring-buffer de 1000 entradas |
| `diff.rs` | diff de píxeis nativo (`viber debug diff`): mean/max/changed_pct/p99 sobre o delta max-channel, ROI opcional; exit 1 acima do `--threshold`; modo GOLDEN (`--baseline <dir>` semeia/compara `<stem>.png`, `--update` aceita) |
| `lua.rs` | método `viber.lua`: compila o chunk na env persistente da VM do `LuaScriptHost`, **player como self**; devolve `{ok, result\|error, applied, warnings}` |
| `tests.rs` | App mínima + bridge real em loopback (`cargo test`) |

## Regras

- Handlers correm como **sistemas exclusivos em `RemoteLast`** (depois de
  `Last`) — nunca bloquear um handler à espera de render.
- Screenshot é **request + poll** (`viber.screenshot` →
  `viber.screenshot_status`): a captura precisa de frames de render.
- Burst (`viber.burst` → `viber.burst_status`) é request/poll igual, mas o
  sistema spawna **NO MÁX UMA entidade `Screenshot` por frame** no total
  (single-shots incluídos — o `extract_screenshots` do bevy rejeita um 2.º
  target da mesma janela no mesmo frame) e a composição da folha corre em
  **thread** (`burst::drive`, chamado por `process_capture_requests`).
- REPL Luau: leituras vêm de um **snapshot do início da chamada**; escritas
  aplicam **no mesmo frame** (antes dos sistemas de gameplay). Sem guard de
  instruções — `while true do end` congela o frame (risco aceite, igual a um
  script de página no Chrome).
- Porta do cliente: `--port`, `--world` ou `VIBER_BRIDGE_PORT` (ver
  `docs/BRIDGE.md` para o protocolo completo).
- Os métodos BRP builtin (`world.query`, `world.spawn_entity`,
  `world.mutate_components`, …) ficam também expostos — inspecção/mutação
  live do ECS.
- **Função nova em `viber.debug.*`** exige ENTRADA em `DEBUG_API_DOCS`
  (lua.rs) — o guard test `test_apidoc_covers_registered_functions` falha
  com função sem doc ou doc órfã. Write ops novas: variante `DebugOp` +
  braço em `apply_one` + cheque em `op_non_finite`.
- FrameStepper (`viber.debug.step/play`): op arma o recurso (pausa + orçamento
  de frames); o sistema exclusivo `frame_stepper_system` (PostUpdate) consome
  o orçamento com `skip` no 1.º pass — o frame em que a op aplicou não conta.
  **Semântica (afinada no smoke): o step deixa o mundo PAUSADO** (speed 0);
  `play` retoma a `restore` guardada na PRIMEIRA chamada da cadeia (um step a
  seguir a outro não a clobber).
- `list_live_engines` valida IDENTIDADE (ping `world`), não só TCP: um
  `engine.json` stale a apontar para a porta de outro mundo não aparece em
  `viber debug engines` nem na descoberta implícita. Sem campo `world`
  (binário antigo) o registo mantém-se.
- `registry.schema`/`rpc.discover` são builtin do bevy_remote 0.19 —
  expostos como `viber debug schema`/`methods` (campos vivem em
  `/properties` como `$ref`s JSON-schema, não em `/fields`).

## Verificar

```bash
cd Viber && cargo test          # tests.rs sobe uma App com bridge em loopback
viber run worlds/hello.xml --bridge &   # engine com bridge
viber debug probe && viber debug tree --json
```
