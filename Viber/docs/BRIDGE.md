# BRIDGE.md — o protocolo de QA do Viber

O debug bridge é o caminho de um AGENTE DE IA para VER, MEXER, PROVAR e
DESCOBRIR a engine viva — o equivalente nativo do tooling Chrome DevTools do
VibeGame. Este doc é a referência completa; o AGENTS.md (raiz) tem as
receitas rápidas.

Módulo: `src/bridge/` (`mod.rs` servidor BRP, `lua.rs` a REPL, `events.rs`
eventos, `burst.rs` frames, `diff.rs` diff visual, `client.rs` cliente CLI,
`logs.rs` ring de tracing). Testes: `src/bridge/tests.rs` — App mínima +
bridge real em loopback, uma porta por teste.

## Transporte e descoberta

- BRP sobre HTTP (`bevy_remote`), porta **15702** por omissão.
- `viber run --bridge [PORTA]` liga; sem valor escolhe a 1.ª porta LIVRE.
- O cliente resolve a porta: `--port` → `--world` (caminho/nome/stem, via
  `engine.json` da sessão + validação por ping) → `VIBER_BRIDGE_PORT` →
  descoberta implícita → 15702. Com **várias engines vivas**, a escolha
  implícita é ERRO (lista as engines) — aponta sempre a tua.
- `viber debug engines` lista as vivas (porta, mundo, pid).
- QA ao vivo passa SEMPRE pelo protocolo de sessão (`viber session
  up/claim/release`) — ver AGENTS.md.

Métodos BRP: `viber.ping` (devolve `pid`+`world`), `viber.screenshot` +
`viber.screenshot_status` (request/poll; o PNG só é "captured" quando o
ficheiro tem IEND), `viber.burst` + `viber.burst_status`, `viber.tree`,
`viber.logs`, `viber.profiler`/`.tab`/`.export`/`.extra_toggle`, `viber.lua`,
`viber.input.key/text/click/move`, `viber.raycast` — mais os métodos builtin
do `bevy_remote` (`world.query`, `world.spawn_entity`,
`world.mutate_components`, …) para inspecção/mutação ECS crua.

## As quatro capacidades

### 1. VER (introspecção)

`viber.debug.*` — leituras do snapshot do INÍCIO da chamada (cap 4096
entidades, mais perto do player primeiro). Referência completa com
assinaturas: `viber debug api --json` na engine viva, ou `docs/LUA_API.md`.

Camadas:
- **Entidades**: `entities/find/find_all/pos/info/transform/mesh/material/
  collider/components/health/ai/around/colliders/lights` — `info()` traz
  tudo numa tabela (hp, script, IA, nav_profile incluídos).
- **Mundo**: `terrain(x,z)` (altura/estrada/água AO VIVO via Arcs),
  `biome_at`, `regions`, `weather_full` (com o scheduler), `atmosphere`,
  `border`, `interior`, `nav` (census landmass: "porque é que a criatura não
  anda"), `clock`, `seeds`.
- **Jogo**: `quest(id)` funda, `quest_defs`, `vault`, `skills`, `waypoints`,
  `save_info`, `audio`, `ui_tree` (ids + RECTS → clique exato por
  coordenada).
- **Determinismo**: `world_hash()` — hash hex do CONTEÚDO do mundo (soma
  FNV-1a por entidade, independente de ordem/ids). Dois boots da mesma seed
  do mesmo binário → MESMO hash. `viber debug hash` é o atalho CLI.

### 2. MEXER (controlo)

Escritas como `DebugOp` aplicadas NO MESMO FRAME (erros viram `warnings`).
Destaque para o que não existia noutra engine de QA:
- **Vitals de qualquer entidade**: `set_entity_hp/set_max_hp` (o `set_hp(n)`
  continua a ser o do player).
- **Estado de jogo**: `quest_force(id, "active|ready|done|not_taken")`,
  `quest_progress`, `vault_set/take`, `skill_learn/points/reset` (aplicam o
  delta de bónus ao herói), `ai_state/ai_aggro/ai_calm_all`.
- **Lente ao vivo**: `postfx{bloom=false, taa=false, …}` — os mesmos gates
  de `VIBER_NO_*` sem restart (a bisseção do flicker ficou interativa);
  `audio_set`, `combat_music`, `rain_look`, `sun`, `ground`, `set_weather`,
  `set_clock`, `nav_set`.
- **Física**: `physics_set{gravity, paused}`; `viber.raycast` (BRP/CLI) para
  queries de colisão.
- **Construção**: `spawn("box:2,1,2", x, y, z, {color, collider=true, snap,
  yaw, scale})` primitivas FÍSICAS; `spawn("/assets/…/torre.glb", …)` GLB do
  pool (load assíncrono — o id responde já, `info(id)` depois mostra
  carregado); `spawn_light`; `set_material/set_light` ao vivo;
  `clear_markers` limpa TODO o `debug:*`.
- **Fluxo de jogo**: `save/load` (mesmo caminho da UI), `teleport_to(name)`,
  `set_camera{distance,pitch,yaw,target}`.

### 3. PROVAR (asserção e medição)

- **Event log estruturado** (`src/bridge/events.rs`): ring de 1000 eventos
  `{seq, time, kind, ...}` — `hurt/damage/death/quest/ui/travel/toast/
  levelup`. `viber.debug.events(since_seq)` ou `viber debug events --since
  N` (o último seq é o próximo cursor). "A quest ficou ready?", "houve dano
  no golpe?" são perguntas respondíveis sem grepar logs.
- **`viber debug step <n>`**: PÁRA o mundo e avança EXATAMENTE n frames à
  speed 1 — e **fica parado** (cada `step` volta a congelar; `step 0` só
  congela). `viber debug play` retoma a speed que estava antes da primeira
  chamada da cadeia. Com `burst` (e `world_hash`) dá medições determinísticas
  frame a frame. (`viber.debug.step(n)`/`play()` na REPL.)
- **`viber debug burst --stats`**: luma média/desvio POR FRAME + a oscilação
  máxima entre frames consecutivos — veredicto numérico de flicker sem
  abrir o PNG (`max_mean_swing`/`max_consecutive_delta` ≈ 0 em mundo parado).
- **`viber debug diff a.png b.png [--roi x,y,w,h] [--threshold %]`**: diff
  de píxeis nativo (`mean_delta/max_delta/changed_pct/p99`); exit 1 acima do
  limiar → regressão visual em CI.
- **Golden images**: `viber debug diff --baseline qa/golden atual.png
  [--threshold 0.5]` compara com `<dir>/<stem>.png`; na primeira vez SEMEIA o
  golden (exit 0) e `--update` aceita a mudança (regrava). Fluxo CI:
  `screenshot -o atual.png && viber debug diff --baseline qa/golden atual.png`.
- **`viber debug watch --lua 'expr' --hz 10 --for 5 [--csv|--json]`**:
  amostra qualquer expressão a N Hz — trajetórias, HP, nav census, sem loops
  à mão (`--csv` para plot, `--json` a coleção `[{t, value}]`).
- **`viber debug test <cenario.lua>`**: helpers `expect(cond, msg)` /
  `expect_near(a, b, tol, msg)` / `fail(msg)` injectados na REPL; exit 1 em
  falha; `--json` devolve `{chunk_ok, report:{asserts, fails}}`. Cenários
  vivem em `<mundo>/qa/*.lua`.

### 4. DESCOBRIR (a engine explica-se)

- **`viber.debug.apidoc()`** / **`viber debug api [--json] [--grep x]`**:
  assinatura + descrição de CADA função `viber.debug.*`, e enumeração viva
  de `viber.*` (jogo), `viber.ui.*` e `viber.profiler` — o agente descobre a
  API sem sair da engine. Guard test garante paridade exata
  docs↔registadas.
- `viber debug schema [--grep tipo|campo] [--crate viber] [--json]` —
  campos e tipos dos tipos REFLETIDOS (builtin `registry.schema` do BRP; sem
  filtro lista só os NOMES — o dump cru são MBs). O uso que interessa é
  `--grep PointLight` (ou por CAMPO: `--grep intensity`): dá os nomes e tipos
  exatos para `world.mutate_components`. Notas: os componentes do VIBER não
  são `Reflect` (para esses usa `viber.debug.*` — o schema cobre os tipos do
  Bevy); e o `--crate` não exclui tipos sem crate no path (primitivos/tuplas).
- `viber debug methods [--grep x] [--json]` — TODOS os métodos BRP
  disponíveis (`rpc.discover`/OpenRPC): builtin do bevy_remote + `viber.*`.
- `viber debug logs [--level warn] [--grep nav]` — filtros client-side.
- `viber debug engines` — quem está vivo (TCP **+ identidade por ping**: um
  `engine.json` stale que aponte para a porta de OUTRO mundo não aparece).

## Limites honestos

- Reads = snapshot do início da chamada: escrever e verificar exige DUAS
  chamadas (ou ler o World/screenshot).
- **`world_hash` só é estável com o mundo CONGELADO** (`step 0`/pausa): o
  hash inclui posições e HP, logo um mundo a correr dá valores diferentes a
  cada chamada. Receita de determinismo: `viber debug step 0`, `hash` duas
  vezes (iguais), `play`. Entre boots, compara com o mesmo tick de simulação.
- O event log é alimentado pelos MESSAGES de jogo: ops de debug que escrevem
  direto (`damage`/`heal`/`kill`/`give`) NÃO geram eventos — provoca com
  gameplay real (ou `toast`, que escreve `ScriptToast`).
- Snapshot 4096 entidades; dumps `colliders/lights` cap 256, `around` 128;
  `events` devolve a cauda de 256 do snapshot.
- `stats()` percorre o mundo inteiro (~100 ms a 60k entidades) — não poluir
  as medições com ela; `prof`/ping são baratos.
- Sem guard de instruções na REPL: `while true do end` congela o frame
  (risco aceite, igual a um script de página).
- `set_material` só em materiais STANDARD (primitivas/GLB) — os bindless do
  terreno destruíam o bind group num `get_mut`.
- Bevy 0.19 não expõe tempos por sistema/entidade de render — o que existe
  é `physics()`, `prof()` e os proxies de composição em `stats()`.

## Receitas novas (o fluxo do agente)

```bash
viber session up && viber session claim --owner qa-bridge
# descobrir
viber debug api --grep nav
viber debug engines
# ver
viber debug lua 'viber.debug.tp(120, 40) return viber.debug.terrain(120, 40)'
viber debug lua 'return viber.debug.nav().census'
# mexer e construir
viber debug lua 'viber.debug.spawn("box:2,2,2", 120, 0, 40, {color="#ffaa00"}) return true'
viber debug lua 'viber.debug.postfx{bloom=false, motion_blur=false} return true'
# provar
viber debug step 3 && viber debug burst -o b.png --frames 9 --stats
viber debug screenshot -o gold.png && viber debug diff gold.png novo.png --threshold 0.5
viber debug watch --lua 'viber.debug.player().x' --hz 20 --for 3 --csv
viber debug test worlds/qa-bridge/qa/sanity.lua
viber debug events --since 0
viber session release --owner qa-bridge
```
