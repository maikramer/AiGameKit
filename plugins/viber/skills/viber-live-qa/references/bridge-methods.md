# Bridge BRP — métodos e API de debug

Transporte: BRP sobre HTTP (`bevy_remote`), porta 15702. Cliente CLI std-only.
Fonte canónica: `Viber/docs/BRIDGE.md` + `Viber/src/bridge/mod.rs`.

## Métodos `viber.*` registados no BRP

| Método | Uso |
|---|---|
| `viber.ping` | `pid` + `world` servido (identidade) |
| `viber.screenshot` + `viber.screenshot_status` | request/poll; PNG "captured" quando tem IEND |
| `viber.burst` + `viber.burst_status` | N frames numa folha 4096² (lado comprido), grid √N, índice carimbado |
| `viber.tree` | árvore de entidades: id/nome/pai/transform/componentes |
| `viber.logs` | ring-buffer de tracing (1000 entradas) |
| `viber.profiler` / `.tab` / `.export` / `.extra_toggle` | snapshot do profiler; `tab {"tab":"systems\|world\|physics\|audio\|extras\|all"}` |
| `viber.lua` | avalia Luau na engine (REPL; API abaixo) |
| `viber.input.key/text/click/move` | input sintético (KeyboardInput/MouseButtonInput/CursorMoved + ButtonInput) |
| `viber.raycast` | raio contra a física (primeira entidade + ponto/normal) |
| `viber.camera.orbit/pan/zoom/look_at/pose` | SÓ com `viber run --camera free` (fora: erro claro) |
| `viber.pick`, `viber.select` | picking |
| `viber.scene.ls/get/find/apply/undo/redo/save/pick/entities/schema` | edição viva (mesma API do `viber scene`); `schema {target}` = props `--@export` + sinais/métodos/conexões + valores; `prop-*` aplica-se sem respawn |
| `viber.scene.groups` / `viber.scene.instance` | `{name?}` → grupos e membros; `{scene, parent?, at?}` → `<Include>` de uma cena (spawn ao vivo). `apply` aceita `extract` (ramo → cena) |
| `viber.tool.list/scatter` | ferramentas de mundo (receitas `<Scatter>`) |

Mais os BRP builtin (`world.query`, `world.spawn_entity`, `world.insert_components`,
`world.mutate_components`, `registry.schema`…) para ECS cru.

## CLI `viber debug …` (atalhos)

```
engines | probe | screenshot -o f.png [--timeout-ms] | burst -o f.png
  [--frames 4|9|16] [--skip N] [--stats] | tree [--json] | logs
  [--limit N --level warn --grep x --json] | prof [--tab X --samples N --export]
  | lua '<código>' [-f f.lua] [--json] | schema [--grep x --crate viber]
  | methods [--grep x] | api [--grep x --json] | events [--since N]
  | step <n> | play | watch --lua 'expr' [--hz 10 --for 5 --csv|--json]
  | test <cenario.lua> | diff a.png b.png [--roi x,y,w,h --threshold %]
  | diff --baseline qa/golden atual.png [--update] | raycast x y z dx dy dz
  | hash | key w|space|esc|up|ctrl… [--shift] | text "olá" | click 400 300
  [--button right] | move 400 300
Flags de engine alvo (em QUALQUER subcomando): --port N | --world <caminho|nome|stem>
```

## REPL `viber.lua` — `viber.debug.*` por camada

Globals PERSISTEM entre chamadas; `return` devolve valor. Resposta
`{ok, result|error, applied, warnings}` (`--json`). Leituras = snapshot do início
da chamada; escritas aplicam no MESMO frame. Player como self (toda a `viber.*`
dos scripts funciona).

### VER (leituras, snapshot)
- `entities([raio])` (cap 4096, mais perto 1.º), `find(nome)`, `find_all(nome)`,
  `pos(id)`, `distance(a,b)`, `around(raio, limite?)` (cap 128)
- `info(id)` (tudo: hp, script, IA, nav_profile), `components(id)`,
  `transform(id)`, `mesh(id)`, `material(id)`, `collider(id)`,
  `colliders([raio])`/`lights([raio])` (cap 256)
- `player()` → {id,x,y,z,hp,max_hp,xp,xp_next,speed}
- `time_scale()`, `fps()`, `prof()`, `stats()` (~100 ms!), `physics()` (tempos Rapier)
- `camera()`, `clock()`, `ground_state()`, `vault()`, `quests()`
- Mundo: `terrain(x,z)` (altura/estrada/água vivos), `biome_at`, `regions`,
  `weather_full`, `atmosphere`, `border`, `interior`, `nav()` (census landmass),
  `seeds`
- Jogo: `quest(id)`, `quest_defs`, `skills`, `waypoints`, `save_info`, `audio`,
  `ui_tree()` (ids + RECTS → clique exato por coordenada)
- `world_hash()` — SÓ estável com mundo congelado (`step 0`)

### MEXER (escritas, mesmo frame)
- Entidade (id numérico OU nome exato→substring): `set_pos`, `move_to` (Y no
  terreno), `teleport(x,y,z)`/`tp(x,z)` (player), `move_player(dx,dz)`,
  `hide/show/toggle_vis`, `disable/enable` (compõe `Disabled`: sai de queries,
  continua no mundo), `despawn(id)`, `rotate(id, graus)`, `set_scale(id, s)`
- Vitals: `heal/damage(n)`, `set_hp(n)`, `set_entity_hp/set_max_hp`, `kill(id)`
  (HP 0, sem i-frames), `xp(n)`, `give(item, n)`, `set_speed(n)`
- Estado de jogo: `quest_force(id, "active|ready|done|not_taken")`,
  `quest_progress`, `vault_set/take`, `skill_learn/points/reset`,
  `ai_state/ai_aggro/ai_calm_all`, `save()/load()`, `teleport_to(name)`
- Construção: `spawn("box:2,1,2", x, y, z, {color, collider=true, snap, yaw,
  scale})` ou `spawn("/assets/…/x.glb", …)` (id responde já; `info(id)` depois),
  `spawn_light`, `spawn_box/spawn_sphere(x,y,z, tam, "#hex")` (markers `debug:*`),
  `clear_markers()`, `set_material/set_light` (só materiais STANDARD)
- Lente ao vivo: `postfx{bloom=false, taa=false, …}` (os gates de `VIBER_NO_*`
  sem restart), `set_clock(minute)` (0–1440; 1380 = noite), `set_camera{distance,
  pitch, yaw, target}`, `sun{yaw, pitch, illuminance, shadows}`, `ground{moss,
  vale_soft, streaks, …}`, `set_weather{rain, clouds, wind}` (congela o ciclo),
  `rain_look{near_fade, alpha, width, rate}`, `audio_set`, `combat_music`,
  `set_time_scale(n)` (slow-mo/pausa), `face(x,z)`, `toast(msg)`,
  `set_window(w,h)`, `physics_set{gravity, paused}`, `nav_set`
- Câmaras de editor: `viber.camera.*` (só `run --camera free`)

### PROVAR
- `events(since_seq)` — ring de 1000 `{seq, time, kind, …}` (hurt/damage/death/
  quest/ui/travel/toast/levelup); o último seq é o próximo cursor
- `step(n)`/`play()` na REPL = `viber debug step/play`
- `dlss("performance")` (se compilado com a feature)

### DESCOBRIR
- `apidoc()` — assinatura + descrição de CADA `viber.debug.*` (guard test de
  paridade com docs); enumeração viva de `viber.*`/`viber.ui.*`/`viber.profiler`

## Cenários de QA (`<mundo>/qa/*.lua`)

Helpers injetados: `expect(cond, msg)`, `expect_near(a, b, tol, msg)`, `fail(msg)`.
`viber debug test qa/x.lua [--json]` → exit 1 em falha; `--json` devolve
`{chunk_ok, report:{asserts, fails}}`.
