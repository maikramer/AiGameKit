# API Lua do Viber (`viber.*`)

Referência da superfície Luau exposta a scripts de entidade. Fonte da verdade:
`src/luau.rs` (`install_viber_api`) e `src/ui/script.rs` (`viber.ui`).

Um script é um ficheiro em `<dir-do-mundo>/scripts/<path>` ligado a uma entidade
via atributo universal `script="caminho.lua"` (ou `script="enemies/wolf.lua"` —
caminhos relativos a `scripts/` aceitam subpastas). Ver exemplos vivos em
`examples/simple-rpg/scripts/`.

## Runtime

- **Carregamento:** o chunk é compilado à primeira ativação de uma entidade que
  o referencia e o **top-level corre 1× por path** (não por entidade). Entidades
  que partilham o mesmo script partilham globals — o estado **por entidade**
  vive em `viber.state()`; a posição de spawn de cada entidade fica em
  `state.home` (gravada pela engine na ativação, alimenta `viber.home()`).
- **Hooks** definidos pelo script:
  - `function on_update(dt)` — corre todos os frames enquanto a entidade está
    ativa (a engine extrai a função após correr o top-level).
  - `function on_player_attack(px, pz)` — **opcional**; aggro-chain: quando o
    herói acerta uma criatura, todos os scripts num raio de **15 m do alvo
    atingido** recebem a posição do atacante (matilhas passam a perseguir).
- **LOD de IA:** além do raio de ativação (attr de spawner
  `activation-radius`, default **45 m**) o `on_update` **nem corre** —
  inimigo distante custa zero lógica.
- **Isolamento:** cada chunk tem environment próprio (`__index` → globals
  reais); scripts não se clobberizam globals. `viber` e stdlib são partilhados.
- **Erros são pcall-style:** reportados **1× por script** (warn, visível no
  `viber debug logs`) e nunca abortam a engine.
- **Semântica de comandos:** setters de movimento/combate/UI **enfileiram
  comandos** aplicados no fim do frame, depois de todos os `on_update` — sem
  acesso direto ao ECS. `move_towards`/`move_by` assentam o Y no terreno
  (amostra o heightfield) ao aplicar.
- **Hot-reload:** o chunk fica em cache por path; uma entidade re-spawnada
  reutiliza os globals existentes. Editar o `.lua` com a engine a correr
  RECARREGA (2026-09-07, `src/hot_reload.rs`; `VIBER_HOT_RELOAD=0` desliga):
  o chunk é recompilado e o top-level re-corre nas entidades ativas —
  globals do script resetam (estado em `viber.state()` sobrevive, pois vive
  fora do env). Erro de compilação = warn e o chunk antigo continua.

Esqueleto típico:

```lua
-- scripts/enemies/wolf.lua
local st = {}

function on_update(dt)
  local ok, px, py, pz = viber.player_position()
  if not ok then return end
  if not st.ready then
    st.ready = true          -- setup por entidade, 1× (globals são partilhados!)
    st.state = "wander"
  end
  local dist = viber.distance_to_player()
  st.state = viber.next_state(st.state, dist, 12.0, 22.0)
  if st.state == "chase" then
    viber.move_towards(px, pz, 3.2)
    if dist < 1.6 then viber.damage_player(8) end
  else
    local tx, tz = viber.wander_target(8.0)
    viber.move_towards(tx, tz, 1.1)
  end
end
```

## Geral

| Função | Devolve | Notas |
|--------|---------|-------|
| `viber.log(msg)` | — | `tracing` (target `viber::luau`) + ring-buffer do bridge (`viber debug logs`) |
| `viber.time()` | number | segundos desde o arranque da engine |
| `viber.position()` | `x, y, z` | snapshot da posição no início do frame |
| `viber.self_name()` | string | nome/debug-id da entidade dona |
| `viber.state()` | table | estado **desta** entidade (criado à pressa, persiste enquanto viva) |
| `viber.home()` | `x, z` | posição de SPAWN (gravada na ativação) — não confundir com `position()` |
| `viber.despawn_self()` | — | a entidade remove-se a si própria (árvore derrubada, baú aberto) |
| `viber.toast(msg)` | — | toast no HUD + log |

## Perceção do player

| Função | Devolve | Notas |
|--------|---------|-------|
| `viber.distance_to_player()` | number \| nil | `nil` = sem player no mundo |
| `viber.player_position()` | `ok, x, y, z` | **4 valores**; `ok == false` quando não há player |
| `viber.player_hp()` | `ok, cur, max` | snapshot do HP do herói no início do frame; `ok == false` sem player/vitals |
| `viber.interacted(key)` | bool | tecla pressionada **neste frame** E player dentro do alcance de interação (3.5 m, ou o `range` de `set_interaction`). Teclas válidas: `"e" "j" "f" "q" "r" "space"` |
| `viber.ground_below(x, y, z)` | number \| nil | a superfície sólida mais alta em ou abaixo de `y` nesta coluna XZ. Acima do mundo = o topo; **dentro de uma gruta = o piso da gruta**; sob um arco ou sob o tabuleiro de uma `<Bridge>` = o chão do vão. `nil` sem terreno sólido abaixo. É a query certa para criaturas em túneis — os snaps de `move_towards`/`move_by` continuam a usar o TOPO |

## Movimento & rotação

| Função | Notas |
|--------|-------|
| `viber.move_towards(x, z, speed)` | passo deste frame (`speed` em m/s) na direção do ponto; Y assentado no terreno |
| `viber.move_by(dx, dz)` | passo relativo direto (m/s, multiplicado por `dt`); idem snap no terreno |
| `viber.face_towards(x, z)` | vira o yaw da entidade para o ponto |
| `viber.face_player()` | idem para o player (no-op sem player) |
| `viber.set_position(x, y, z)` | **legado** — posição absoluta SEM snap no terreno (compat); preferir `move_towards` |

## IA primitivas

As mesmas máquinas da engine (`src/ai.rs`), expostas para os scripts comporem.

| Função | Devolve | Notas |
|--------|---------|-------|
| `viber.wander_target(radius)` | `x, z` | ponto determinístico ao redor do `home`; incrementa `state.picks`. O `home` é gravado pela engine na **ativação** da entidade (`activate_at` usa a posição de spawn) — não é preciso chamar `viber.state()` antes |
| `viber.next_state(cur, dist, aggro, deaggro)` | `"wander"` \| `"chase"` | FSM wander↔chase com histerese (`cur` é `"wander"` ou `"chase"`) |

## Gesto & som

Expressividade de NPC: os gestos são acções **one-shot** no rig glTF
(`CharacterAnimator`) com blend de ~250 ms — o driver de locomoção recupera o
rig no fim do clip, como nos gestos idle da engine. O nome pedido é casado
contra os clips do GLB por fuzzy match (normaliza caixa, `_`/`-` e prefixos
de ferramenta, e aceita substring — `"foldarms"` encontra
`Animator3D_FoldArms`); rig sem clip correspondente = warn 1× e ignora
(sem crash). Packs `npc_*` trazem tipicamente `talk/yes/no/wave/call/
foldarms/lean/idle`.

| Função | Notas |
|--------|-------|
| `viber.gesture(name)` | gesto one-shot no rig da entidade; **alternativas** separadas por `,` são tentadas por ordem — `viber.gesture("salute,yes")` toca `salute` se o rig tiver, senão `yes` |
| `viber.sound(clip)` | SFX curto na posição da entidade (`assets/audio/sfx`, volume cai com a distância). Registry completo (case-insensitive; desconhecido = erro de script, warn 1×): `hit whoosh harvest ui chop_hit chop_break mine_hit mine_break levelup quest_complete travel loot chest_open footstep footstep_water hurt heal game_over quest_accept notification coin buy error save load shop_open enemy_hurt enemy_death wolf_growl/growl slime_squish boss_roar/roar shield_block/block door_open door_close bomb_drop jump dash`. Os OGGs vivem no pool partilhado (`examples/shared-assets/manifests/audio-sfx-*.yaml` — regenerar com `regen_audio.py`) e são espelhados para o exemplo pelo `scripts/sync_assets.py` |

## Combate & progressão

| Função | Notas |
|--------|-------|
| `viber.damage_player(amount)` | dano pelo path único do feedback (i-frames, vinheta, número flutuante, morte, knockback com `from` = posição da entidade) |
| `viber.heal_player(amount)` | cura direta no HP do herói |
| `viber.apply_status(kind, secs)` | status effect no herói; hoje só `kind = "venom"` (tick 1/s) |
| `viber.add_xp(gain)` | XP direto no herói |
| `viber.topple()` | destrutível (`break-style: fall`): tomba na direção herói→entidade, remove o script e despawna no fim da queda |
| `viber.teleport_player(x, y, z)` | move o herói |

## Quests

Definições em JSON (21 quests do exemplo, embutidas em `src/quests.rs` via
`include_str!`); o estado viaja no save. `viber.quest_state` devolve
`"not_taken" | "active" | "ready" | "done" | "unknown"`.

| Função | Notas |
|--------|-------|
| `viber.quest_state(id)` | estado atual da quest `id` (frame-start) |
| `viber.quest_accept(id)` | aceita (toast "Quest aceita") |
| `viber.quest_turn_in(id)` | entrega se `ready`; aplica recompensas (XP/ouro/itens) e toast |
| `viber.report_kill(kind)` | reporta 1 kill do alvo `kind` (objetivo `kill`, auto-progresso) |
| `viber.report_visit(place)` | reporta visita ao marco `place` (objetivo `visit`) |
| `viber.report_collect(item, amount)` | colheita: deposita no **vault**; objetivos `collect` leem o inventário (auto-progresso). Recursos (`gold/wood/stone`) e itens de objetivo (`"dark-wood"`, `"bog-moss"`) |

## Economia & inventário

| Função | Notas |
|--------|-------|
| `viber.vault_add(kind, amount)` | deposita recurso (`gold`/`wood`/`stone`) |
| `viber.vault_get(kind)` | quantidade atual (0 se desconhecido) |
| `viber.item_add(id, amount)` | item de inventário (`potion`, `antidote`, `bomb`, …) |
| `viber.item_count(id)` | quantidade atual (0 se desconhecido) |
| `viber.alive_in_region(idx)` | hostis scriptados VIVOS na banda `idx` (0–4: centro/norte/sul/este/oeste; snapshot a 1 Hz — o gating do boss final usa isto) |

## Interação & UI

| Função | Notas |
|--------|-------|
| `viber.set_interaction(label, key, range?)` | registra alvo de interação: prompt HUD `[tecla] label` quando o player se aproxima (range default 3.5). `key` ∈ `"e" "j" "f" "q" "r" "space"` |
| `viber.interacted(key)` | ver Perceção — o par `set_interaction` + `interacted` é o padrão de colheita (`tree.lua`, `rock.lua`) |

## `viber.ui.*`

Superfície da UI declarativa (`<UiRoot>`/`<UiStyle>`, `src/ui/`). Os setters
enfileiram mutações aplicadas depois de todos os scripts; os readers leem o
snapshot do frame (bindings, estado por elemento, cliques, listas).

| Setter | Notas |
|--------|-------|
| `set_text(id, text)` | texto de um `UiText` — ou o **valor** de um `UiInput` |
| `set_value(id, value)` | fração de `UiBar`/`UiCooldown`, ou valor de `UiSlider` (clampado ao min/max dele) |
| `set_visible(id, visible)` | mostra/esconde |
| `set_disabled(id, disabled)` | desativa interação (restyle) |
| `add_class(id, class)` / `remove_class(id, class)` | classes do stylesheet |
| `toggle_class(id, class, on)` | o mais usado por HUD scripts |
| `set_style(id, declarations)` | inline style CSS-like — todo o dialecto (`"background: rose-500/40; box-shadow: 0 4 12 #00000088"`) |
| `set_checked(id, checked)` | estado de um `UiCheck` (classes `checked`/`unchecked` + tick sincronizam-se) |
| `set_anim(id, spec)` | liga movimento em runtime (`"spin 3"`, `"pulse"`, `"bob 1.5 10"`, `"shake"`); `"none"` desliga |
| `focus(id)` | dá o teclado a um `UiInput` (desfoca o anterior) |
| `open(id, open)` | abre/fecha um `UiModal` por id |
| `select_tab(group, tab)` | seleciona tab num grupo |
| `action(name, arg)` | levanta ação de gameplay (`learn`, `buy`, `sell`, `save`, `load`) |
| `list(name, rows)` | cria/repõe uma fonte de `<UiList>` por script — `rows` = `{{campo=valor, …}, …}`; números/booleanos stringify |

| Reader | Devolve | Notas |
|--------|---------|-------|
| `read(id)` | table ou nil | `{text, value, visible, checked, disabled}` de QUALQUER elemento com id — inputs reportam o texto digitado, sliders o valor |
| `exists(id)` | bool | o id é endereçável agora? |
| `focused()` | string ou nil | id do `UiInput` com o teclado |
| `get(name)` | string | valor formatado do binding `name` (`""` se desconhecido) |
| `number(name)` | number | fração 0..1 (ou contagem crua) do binding |
| `is_open(id)` | bool | modal `id` está aberto? |
| `tab(group)` | string | tab selecionada no grupo |
| `clicked(id)` | bool | true **no frame** em que o elemento foi pressionado |
| `list_count(name)` | number | nº de linhas da fonte de lista |
| `rows(name)` | table | cópia das linhas — `{{campo=…}, …}` |

Bindings disponíveis (`src/ui/bind.rs`, usados por `bind="…"` no XML e por
`get`/`number`): `health` (+`.text` `.value` `.low`), `xp`/`xp.text`, `level`
(+`.text`), `gold`, `wood`, `stone`, `cd.dash`, `cd.heal`, `cd.strike`,
`target` (+`.name` `.alive`), `clock`, `day`, `prompt.key`/`prompt.label`/
`prompt.active`, `quest` (+`.title` `.text` `.active`), `potion`, `antidote`,
`bomb`, `toast` (+`.active`), `combo` (+`.text`), `purse.recent`, `belt.recent`,
`xp.recent`, `quest.recent`, `combat.active`, `abilities.active`,
`vitals.active`, `zone.name`, `zone.active`.

```lua
-- scripts/ui/hud.lua (real; os VALORES chegam pelos bind="…" do XML,
-- o script trata só dos estados visuais que um binding não exprime)
function on_update(dt)
  viber.ui.toggle_class("hp-bar", "danger", viber.ui.number("health") <= 0.3)
  viber.ui.toggle_class("cd-dash", "ready", viber.ui.number("cd.dash") <= 0.001)
  viber.ui.toggle_class("vial-potion", "empty", viber.ui.number("potion") < 1)
  if viber.ui.clicked("menu-hint") then
    viber.ui.open("menu", true)
  end
end

-- Widgets interativos e listas por script (ver examples/simple-rpg/ui-showcase):
viber.ui.list("bag-demo", { { name = "Poção", count = 12 } })
local vol = viber.ui.read("volume")           -- slider
if vol and vol.value > 80 then viber.ui.set_anim("seal", "shake") end
if viber.ui.read("mute").checked then viber.ui.set_style("bell", "opacity: 0.4") end
```

Widgets declarativos (`UiGrid`, `UiCheck`, `UiSlider`, `UiInput`), atributos
universais (`anim="…"`, `tooltip="…"`), paleta de cores Tailwind e a lista
completa de propriedades de estilo: **`docs/UI.md`**.

## `viber.profiler`

Superfície do profiler nativo (`src/profiler/`, painel declarativo em **P** —
`examples/simple-rpg/world/profiler.xml` + `ui/profiler.css` +
`scripts/ui/profiler.lua`). Mesmo padrão do `viber.ui`: leitura de um
snapshot publicado pela engine, ações por fila aplicada pós-frame.

| Função | Devolve | Notas |
|--------|---------|-------|
| `viber.profiler()` | table ou nil | Snapshot completo (`tabs.systems/world/physics/audio` + `extras` + `state`); **nil com o modal fechado** — o driver nem acorda. Publicado a ~4 Hz pela engine. |
| `viber.profiler_cmd(cmd)` | — | Enfileira ação: `"freeze"`, `"reset"`, `"export"` (ficheiro), `"copy"` (JSON completo → clipboard), `"tab:systems\|world\|physics\|audio\|extras"`, `"radius:±N"` (raio das próximas), `"extra:<id>"` (toggle: `colliders`, `grass`, `physics-pause`). |

Teclas: **P** abre/fecha o modal (declarativo, `key="p"`); abas por clique
ou teclado nativo do modal (**`]`/`.`** próxima, **`[`/`,`** anterior,
**1–5** saltam); **F12**/**Pause** congela a aquisição, **`** exporta,
**PgUp/PgDn** raio. A UI é a fonte da verdade das abas — o driver espelha
para a engine. A bridge lê o MESMO JSON: `viber debug prof --tab tudo` (ou o método
`viber.profiler.tab {"tab": "all"}`) devolve exactamente o payload do
COPIAR/ficheiro.

## `viber.debug.*` (bridge/REPL)

Disponível quando o mundo corre com `--bridge` (`src/bridge/lua.rs`): é a
superfície do método `viber.lua` / `viber debug lua '<código>'` — o
"evaluate script" do debug bridge. O código corre na MESMA VM dos scripts,
com o player como self (toda a `viber.*` acima funciona). Leituras vêm do
snapshot do início da chamada; escritas aplicam no mesmo frame. Globals
persistem entre chamadas (REPL); `return` devolve o valor.

Dumps em volume (`colliders`/`lights`/`around`) e agregados (`stats`) contam
sobre o mundo real; no snapshot de entidades há cap de 4096 (mais perto do
player primeiro). `physics()` devolve os tempos do ÚLTIMO step do Rapier —
não há tempos por sistema/entidade no Bevy 0.19 (ver nota no AGENTS.md).

```lua
-- leitura (snapshot)
viber.debug.entities(raio?)        -- {id,name?,x,y,z,disabled} (cap 4096, mais perto 1.º)
viber.debug.find(nome)             -- id (bits) por nome exato → substring; find_all(nome) → tabela
viber.debug.pos(id)                -- x,y,z; id = bits numérico ou nome
viber.debug.distance(a, b)         -- metros entre duas entidades (snapshot)
viber.debug.player()               -- {id,x,y,z,hp,max_hp,xp,xp_next,speed}
viber.debug.camera()               -- {x,y,z,distance,pitch,yaw,target?} da OrbitCamera
viber.debug.clock()                -- {minute,dawn,dusk,minutes_per_real_second} (DayCycle)
viber.debug.vault()                -- {gold,wood,stone,items{}} ou nil (sem EconomyPlugin)
viber.debug.quests()               -- {id = "not_taken"|"active"|"ready"|"done"}
viber.debug.info(id)               -- TUDO: id,name,x,y,z,disabled,hidden,transform,
                                   --   parent,children,collider,rigidbody,mesh,material,components
viber.debug.components(id)         -- nomes dos componentes (ex.: "bevy_mesh::components::Mesh3d")
viber.debug.transform(id)          -- {x,y,z,pitch,yaw,roll,sx,sy,sz,gx,gy,gz?} (euler YXZ graus)
viber.debug.mesh(id)               -- {topology,vertices,indices,has_normals,has_uvs,
                                   --   uv_count,uv_min,uv_max} — UV_0 para QA de atlas
viber.debug.material(id)           -- {base_color={r,g,b,a},metallic,roughness(perceptual),
                                   --   unlit,base_color_texture={w,h}?,normal_map={w,h}?}
viber.debug.collider(id)           -- {shape="cuboid|ball|trimesh|compound|outro",hx,hy,hz,
                                   --   radius,vertices,shapes} (Rapier); rigidbody via info()
viber.debug.prof()                 -- snapshot do profiler (tabela; ver viber.profiler)
viber.debug.stats()                -- agregados do mundo INTEIRO: entities, meshes,
                                   --   colliders (+por shape), rigidbodies (+por tipo),
                                   --   lights (+shadows), emitters, scripted, disabled,
                                   --   scripts_total/active, fps, frame_ms_avg, terrain_chunks
viber.debug.physics()              -- tempos do ÚLTIMO step do Rapier: {enabled, step_ms,
                                   --   collision_detection_ms, solver_ms, ccd_ms, islands_ms,
                                   --   ncontacts, nconstraints} (nil sem física)
viber.debug.colliders(raio?)       -- [{id,name?,x,y,z,shape,hx..hz|radius|vertices,
                                   --   rigidbody?}] (cap 256; raio relativo ao player)
viber.debug.lights(raio?)          -- [{id,name?,x,y,z,kind,intensity,shadows,range?}]
viber.debug.around(raio, limite?)  -- resumo compacto de TUDO perto do player (default 64,
                                   --   cap 128, mais perto 1.º): id/name/distance/collider/
                                   --   mesh_vertices/light+shadows/scripted/rigidbody
viber.debug.fps()                  -- atalho para prof().fps (nil sem DiagnosticsStore)
viber.debug.time_scale()

-- escrita (mesmo frame)
viber.debug.set_pos(id, x, y, z)
viber.debug.move_to(id, x, z)      -- qualquer entidade, Y sentado no terreno
viber.debug.teleport(x, y, z)      -- player, Y explícito
viber.debug.tp(x, z)               -- player, Y sentado no terreno
viber.debug.move_player(dx, dz)    -- player, metros XZ, Y no terreno
viber.debug.face(x, z)             -- player olha para o ponto
viber.debug.rotate(id, graus)      -- soma yaw em torno do Y
viber.debug.set_scale(id, s)       -- escala uniforme
viber.debug.hide(id) / show(id) / toggle_vis(id)
viber.debug.disable(id) / enable(id)   -- componente Disabled (sai das queries)
viber.debug.despawn(id)
viber.debug.heal(n) / damage(n) / set_hp(n)   -- player (set_hp é absoluto, clamp [0,max])
viber.debug.kill(id)               -- HP a zero, sem i-frames nem feedback (debug cru)
viber.debug.xp(n) / give(item, n)
viber.debug.set_speed(n) / set_time_scale(n)  -- slow-mo; 0 = pausa
viber.debug.set_camera{distance=?, pitch=?, target=?}  -- OrbitCamera (screenshots)
viber.debug.set_clock(minuto)      -- 0–1440 (1380 = noite); sem DayCycle → warning
viber.debug.set_weather{rain=?, clouds=?, wind=?}  -- fixa o tempo e CONGELA o ciclo (A/B)
viber.debug.rain_look{near_fade=?, alpha=?, width=?, rate=?}  -- look da chuva ao vivo
viber.debug.set_window(w, h)       -- redimensiona a janela p/ QA responsivo (@media, vw/vh)
viber.debug.toast(msg)
viber.debug.spawn_box(x, y, z, tamanho, "#rrggbb"?)   -- marker debug:box:N
viber.debug.spawn_sphere(x, y, z, raio, "#rrggbb"?)   -- marker debug:sphere:N
viber.debug.clear_markers()        -- remove todos os markers debug:*

-- chão e sol AO VIVO (sem rebuild — os knobs acumulam entre chamadas)
viber.debug.sun{yaw=?, pitch=?, illuminance=?, shadows=?}
                                   -- roda a DirectionalLight E o sol do shader do
                                   -- terreno (publica sun_dir nos chunks); yaw =
                                   -- azimute em graus a partir de +X, pitch =
                                   -- altura no céu (0 horizonte, 90 zênite)
viber.debug.ground{moss=?, streaks=?, rock_darken=?, tri_slope=?, tri_soft=?,
                   strata_strength=?, patchiness=?, gravel=?, dirt=?,
                   forest=?, shore_width=?, vale_soft=?}
                                   -- pele das paredes (moss/streaks/rock_darken/…)
                                   -- + re-cozedura dos splats de chunk (patchiness =
                                   -- manchas de terra/folhada, gravel = ombro de
                                   -- cascalho em slope, dirt/forest = densidade 0–2,
                                   -- shore_width = banda de areia em m;
                                   -- vale_soft = 0 regiões de verde contrastadas
                                   -- (as "manchas"), 1 tudo fundido num verde)
viber.debug.ground_state()         -- valores correntes (tuning + paredes), nil sem terreno
```
