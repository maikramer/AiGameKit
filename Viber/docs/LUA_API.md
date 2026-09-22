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
| `viber.on_road(x, z)` | bool | ponto na fita de alguma estrada (query ao mundo carvado) |
| `viber.in_water(x, z)` | bool | ponto dentro da zona de carve de um corpo de água |
| `viber.ground_below(x, y, z)` | number \| nil | a superfície sólida mais alta em ou abaixo de `y` nesta coluna XZ. Acima do mundo = o topo; **dentro de uma gruta = o piso da gruta**; sob um arco ou sob o tabuleiro de uma `<Bridge>` = o chão do vão. `nil` sem terreno sólido abaixo. É a query certa para criaturas em túneis — os snaps de `move_towards`/`move_by` continuam a usar o TOPO |

## Terreno vivo (`viber.terrain.*`)

Edições do mundo em RUNTIME (Fase 3): a cratera/vala/aterro entra no
overlay de edições (recortes densos sobre a grid carvada) e a engine
re-mesha só as colunas afetadas — **mesh E collider trimesh**, pelo swap
atómico do LOD de sempre. A grid do bootstrap nunca é mutada; o
`viber.ground_below`/`ground_at` do frame seguinte já vê a edição. Não
persiste no save (v1, documentado).

| Função | Devolve | Notas |
|--------|---------|-------|
| `viber.terrain.lower(x, z, raio, profundidade)` | bool | abaixa (mineração/explosão): `h = cur − depth·falloff`; empilha em chamadas repetidas |
| `viber.terrain.raise(x, z, raio, altura)` | bool | levanta (aterro/colina) |
| `viber.terrain.flatten(x, z, raio [, altura])` | bool | achata à `altura` (ou à cota do centro quando omitida) com blend no falloff |
| `viber.terrain.crater(x, z, raio, profundidade)` | bool | tigela funda até 0.7·raio + rebordo saliente até ao raio |
| `viber.terrain.revision()` | u64 | revisão do overlay — muda quando alguma edição commitou |
| `viber.terrain.max_radius()` | number | teto que a engine aplica ao raio (96 m) |

Regras: o pedido entra numa fila e é aplicado no frame seguinte (no máximo
4 por frame — um `on_update` em loop não congela o frame); raio ≤ 96 m e
profundidade/altura ≤ 64 m (clamp); alturas resultantes ficam em
`[0, max-height]`; pedidos NaN/inválidos são rejeitados com warn. O COMBATE
fino pode usar isto para crateras de bombas, poços de mineração ou valas
de cerco — `viber.terrain.flatten` é o que se quer debaixo de um edifício
de script.

## Movimento & rotação

O movimento é **pedido**, não escrito. `move_towards`/`move_by` declaram a
velocidade planar desejada para o frame; um consumidor único na engine
(`ai::apply_ai_locomotion`, `PostUpdate`) faz a rampa de aceleração, dá o passo,
assenta o Y no terreno e roda o yaw. Consequências visíveis:

- **A entidade acelera e desacelera** em vez de arrancar à velocidade máxima no
  primeiro frame (`LocomotionProfile::accel`, 12 m/s² por omissão).
- **A viragem tem limite de velocidade** (`turn_rate`, 7 rad/s por omissão): um
  alvo novo faz a criatura *virar*, não rodar 180° num frame.
- **O corpo aponta para onde vai.** Um `face_towards`/`face_player` é um
  *pedido* de olhar, honrado só com a entidade praticamente parada (< 0.25 m/s).
  Chamar `face_player()` e `move_towards(outro_ponto)` no mesmo frame já não põe
  o NPC a andar de lado — a marcha ganha.
- **Deixar de pedir movimento trava.** Sem `move_towards` num frame a entidade
  desacelera até parar (é o que acontece quando o AI LOD salta o `on_update`).

| Função | Notas |
|--------|-------|
| `viber.move_towards(x, z, speed)` | pede marcha na direção do ponto a `speed` m/s (nunca ultrapassa o ponto); Y assentado no terreno |
| `viber.move_by(dx, dz)` | pede uma velocidade relativa direta em m/s; idem snap no terreno |
| `viber.face_towards(x, z)` | pede o yaw para o ponto — só ganha com a entidade parada |
| `viber.face_player()` | idem para o player (no-op sem player) |
| `viber.set_locomotion(walk, run [, turn_rate])` | fixa os nominais do rig (m/s) e a taxa de viragem (rad/s), desligando a auto-calibração |
| `viber.set_position(x, y, z)` | **legado** — posição absoluta SEM snap no terreno (compat); preferir `move_towards` |

### Nominais de locomoção (porque o clip deixou de derrapar)

O clip de locomoção é escolhido — e a sua cadência afinada — a partir dos
**nominais do rig**: a que velocidade é que este personagem "anda" e "corre".
Por omissão a engine **aprende-os sozinha** das velocidades que o script
comanda: um lobo que chama `move_towards(..., 2.2)` em patrulha e
`move_towards(..., 4.6)` em perseguição fica com `walk = 2.2` / `run = 4.6`, e
as duas passadas batem certo com o chão. Um NPC que só anda a 1.1 m/s fica com
`walk = 1.1` e um `run` sintético acima, para nunca saltar sozinho para o clip
de corrida.

Só é preciso `viber.set_locomotion` quando o clip do GLB foi assado a uma
cadência que não corresponde à velocidade a que o script move a entidade.

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
| `viber.fire_projectile(id [, x, y, z])` | dispara um projétil do `<ProjectileTemplate id=…>` do mundo, da boca da entidade (+1.2 m) para o peito do herói (+1.1 m) ou para o ponto dado; devolve `false` sem herói nem alvo. A facção do template decide quem leva o dano (`enemy` → herói pelo path único do feedback; `player` → criaturas com `Health`; `neutral` → só terreno); `gravity > 0` faz arco balístico. Template desconhecido = warn 1× e o disparo cai |
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

## Input genérico (`viber.input.*`)

Input CRU de teclado e rato — qualquer tecla, não só as de interação. NÃO é
gateado por `MenusOpen`: um script que queira respeitar menus compõe com
`viber.ui.is_open()`. Os três estados partilham o parser (`"w"`, `"7"`,
`"f5"`, `"up"`/`"arrowup"`, `"lshift"`, `"="`, símbolos literais; rato:
`"mouse1"`/`"lmb"`, `"mouse2"`/`"rmb"`, `"mouse3"`/`"mmb"`).

| Função | Devolve |
|--------|---------|
| `viber.input.pressed(nome)` | `true` no frame em que a tecla foi pressionada |
| `viber.input.down(nome)` | `true` enquanto held |
| `viber.input.released(nome)` | `true` no frame em que foi largada |

## Eventos engine→Lua (`viber.events()`)

A fila de eventos do JOGO — o que mudou FORA dos scripts. Pull model: a 1.ª
chamada SUBSCREVE o script (quem nunca chamou nunca recebe); cada chamada
devolve e limpa a fila do próprio script. Cap de 64 por script (transbordo
descarta o evento novo, warn 1×).

```lua
for _, ev in ipairs(viber.events()) do
  if ev.type == "kill" then print(ev.name, ev.entity) end
end
```

| `type` | Campos | Quando |
|--------|--------|--------|
| `kill` | `name` (kind do script, ex. `"wolf"`), `entity` (bits) | abate pelo melee nativo OU HP a zero por `viber.entity_damage` |
| `player_hurt` | `amount` (pós-guard/parry), `hp` | o herói apanhou dano real |
| `player_died` | — | respawn efetuado |
| `collect` | `item`, `amount` | entrou algo no vault (loot, report_collect, item_add) |
| `quest_done` | `id` | quest entregue |
| `level_up` | `level` | subida de nível |
| `ui_action` | `name`, `arg` | ação da UI declarativa (`viber.ui.action`) — os handlers nativos correm à mesma, SALVO as ações reclamadas por `viber.own_action` |

## Timers (`viber.after` / `viber.every`)

Substitui o `st.t += dt` manual. World-scoped: correm independentemente do
raio de ativação do dono; a callback corre com o ctx seedado à entidade.

```lua
viber.after(2.5, function() viber.toast("passaram 2,5 s") end)
local h = viber.every(0.5, function() ... end)
viber.timer_cancel(h)
```

## Vitais genéricas de entidade

O dano/cura de QUALQUER entidade em Lua — o que deixa um jogo fazer o seu
combate sem o melee nativo. `id` = bits de entidade (`viber.find`); sem `id`
= a entidade corrente. Leitura por snapshot de início-de-frame; escrita
aplica pós-frame.

| Função | Efeito |
|--------|--------|
| `viber.entity_hp(id?)` | `ok, cur, max` ( snapshots de entidades com `Health`) |
| `viber.entity_set_max_hp(max, id?)` | cria o `Health` se faltar (current = max); redimensiona se existir |
| `viber.entity_damage(amount, id?)` | dano direto (sem i-frames — esses são do path do player). HP ≤ 0 EMITE `{type="kill"}` e NÃO corre o caminho nativo (cadáver/XP/quests são do melee) — o script decide |
| `viber.entity_heal(amount, id?)` | cura, clampe ao max |

## Procura por nome

| Função | Devolve |
|--------|---------|
| `viber.find(nome)` | bits da entidade (exato primeiro, substring depois) ou `nil` |
| `viber.find_all(nome)` | array com TODAS as correspondências |

## Estado de jogo e módulos

```lua
-- viber.game(): tabela ÚNICA por mundo — sobrevive ao hot-reload (como
-- viber.state() sobrevive por entidade). Persiste no save como world_kv
-- (JSON plano: string/número/bool) em QUALQUER preset — o save é serviço de
-- engine. Gravar/carregar por script: `viber.save()` / `viber.load_save()` (o
-- mesmo ficheiro do botão Guardar/Carregar do menu).
viber.game().bosses_killed = (viber.game().bosses_killed or 0) + 1

-- viber.load("lib/x.lua"): corre um chunk de scripts/ em env próprio UMA
-- vez por mundo e devolve o seu return (cacheado) — fatora bibliotecas
-- partilhadas (ver scripts/lib/fsm.lua do exemplo).
local fsm = viber.load("lib/fsm.lua")

-- viber.save() / viber.load_save(): gravam/carregam o save do mundo (posição do
-- herói + vitais + game(); campos RPG quando existem). Um jogo sem RPG usa
-- isto para auto-save em checkpoints (ver worlds/lua-demo).
viber.save()
```

## Spawn em runtime (`viber.spawn_prototype`)

Instancia um `<Prototype>` do mundo. O spawn real é pós-frame (sistema
exclusivo — primitivas com textura/colisor precisam dos `Assets`); sem `y`
o Y assenta na superfície renderizada. `opts.on_spawned(bits)` corre no fim
do frame do spawn.

```lua
viber.spawn_prototype("goblin", px + 3, pz)
viber.spawn_prototype("fireball", px, pz, { y = 1.5, on_spawned = function(bits)
  projéteis[bits] = true
end })
```

## Combate & FX (primitivas de jogo)

O que deixa um jogo fazer o SEU combate/habilidades em Lua reusando a lógica
nativa (falloff, knockback, morte com cadáver/XP/quests, partículas, câmara).
Todas degradam para no-op quando o recurso subjacente não existe (mundo
`gameplay: none` sem o plugin respetivo) — excepto `radial_damage`/`burst`,
que só dependem de componentes/mensagens sempre registados.

### Posição & alvos

| Função | Devolve |
|--------|---------|
| `viber.entity_position(id?)` | `ok, x, y, z` do snapshot do frame (`ok == false` sem entrada; sem `id` = a própria) |
| `viber.nearby(raio [, limite])` | lista `{id, name, x, y, z, distance}` de entidades NOMEADAS à volta da própria, mais perto 1.º (cap 64; default 16) |
| `viber.player_forward()` | `dx, dz` — forward do herói no plano (modelo olha +Z), para dash/mira |

### Dano e morte

| Função | Efeito |
|--------|--------|
| `viber.radial_damage(x, z, raio, dano [, opts])` | dano em ÁREA com falloff linear (cheio no centro → metade na borda); `opts.knockback = força` empurra radialmente. Mortes seguem a **paridade do melee** (corpo + XP + quests `kill` + evento `{type="kill"}` + SFX) |
| `viber.entity_despawn(id)` | remove a entidade (`despawn_self` é o atalho da própria) |
| `viber.status_clear(kind)` | limpa um status do herói (`"venom"`; default sem arg) — o que o antídoto nativo faz |

### Partículas

| Função | Efeito |
|--------|--------|
| `viber.burst(preset, x, y, z [, count])` | burst de partículas; preset validado: `fire smoke fireflies ground-dust sparkle leaves snow sand-dust magic core` **+ `sparks`** (o impacto do melee nativo); desconhecido = erro de script; default 12 |
| `viber.ring(x, z, raio [, cor])` | anel de choque no chão; `cor` em `"#rrggbb"` |

### Câmara & feedback

| Função | Efeito |
|--------|--------|
| `viber.shake(força)` | trauma da câmara (o melee usa ~0.2 por golpe, 0.45 no slam) |
| `viber.kick(dx, dy, dz)` | solavanco direcional (mola) |
| `viber.fov_kick(graus)` | pulso de FOV (dash nativo: +8°) |
| `viber.punch(stops, bloom)` | pulso de pós-processo (exposição/bloom) |
| `viber.hit_stop(segundos)` | congela o tempo virtual (impacto; o melee usa 0.06–0.13 s) |
| `viber.damage_number(texto [, opts])` | número flutuante; `opts.color = "#rrggbb"`, `opts.x/y/z` (default: 1.8 m acima de si) |

### Animação

| Função | Efeito |
|--------|--------|
| `viber.play_clip(nome [, opts])` | clip de ação no rig (fuzzy match, blend 250 ms); `opts.speed` escala a reprodução (a colheita nativa usa 1.4), `opts.id` escolhe outra entidade (default: a própria). Generaliza o `viber.gesture` (que é `play_clip(nome)` sem opts) |

Exemplo de habilidade em Lua (radial + juice + morte nativa):
`examples/simple-rpg/scripts/game/abilities.lua`.

## Scripts "sempre ativos" (controllers)

Um `<Entity script="…" tag="always-active">` corre o seu `on_update` em
QUALQUER lugar do mundo — sem o "LOD de IA" de 45 m que congela scripts
distantes (mesma política dos scripts de UI). É o que um CONTROLLER de jogo
(habilidades, hotbar, diretor) precisa; criaturas/NPCs ficam sem o tag para
preservar o congelamento por distância.

## Diálogo & posse de sistemas

Um NPC/dador pode conduzir o seu PRÓPRIO diálogo em Lua:

| Função | Efeito |
|--------|--------|
| `viber.say(texto [, segundos])` | escreve no **balão nativo** do HUD (o mesmo do diálogo de quests); sem `<DialogueBalloon>` no mundo = no-op com warn 1×; `\n` faz multi-linha; default 4 s |
| `viber.quest_def(id)` | tabela da definição autoral (`id/title/npc/biome/kind/target/count/radius/gold/xp/items/lines_intro/lines_progress/lines_complete`) ou `nil` — lê o MESMO `<mundo>/quests/*.json` da engine |
| `viber.quest_defs()` | lista com todas as definições do mundo |
| `viber.own_system(nome)` | **reclama um sistema nativo** para Lua: o handler da engine cala e a lógica passa a ser do script. Nomes: `dialogue` (o `[E]` dos `<DialogueNPC>`), `abilities` (C/E/R), `bomb` (B), `guard` (L), `hotbar` (1/2), `harvest` (J). Sem posse, o comportamento nativo é o de sempre |

Exemplo completo: `examples/simple-rpg/scripts/npc/forest-wolves.lua` (Hald —
linhas do JSON, `say` no balão, `quest_accept`/`quest_turn_in`).

> **GOTCHA**: o top-level do script corre ANTES do primeiro snapshot do
> frame — leituras como `viber.quest_def`/`viber.find` só têm dados dentro do
> `on_update` (o exemplo busca a def laziamente, na 1.ª chamada).

## Economia adicional

| Função | Efeito |
|--------|--------|
| `viber.vault_take(kind, amount)` | consome do vault (recurso OU item); sem stock = no-op com warn 1× (o script guarda-se com `vault_get`) |
| `viber.own_action(nome)` | reclama uma ação da UI para Lua: o handler nativo cala e a ação chega via `viber.events()`. Sem dono, o comportamento nativo é o de sempre (compat) — ver `scripts/game/shop.lua` do exemplo |

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
| `set_style(id, declarations, true)` | SUBSTITUI o inline inteiro (o merge nunca desfaz uma declaração; isto sim) |
| `clear_style(id)` | remove o inline — a folha volta a mandar |
| `set_checked(id, checked)` | estado de um `UiCheck` (classes `checked`/`unchecked` + tick sincronizam-se) |
| `set_anim(id, spec)` | liga movimento em runtime (`"spin 3"`, `"pulse"`, `"bob 1.5 10"`, `"shake"`); `"none"` desliga |
| `focus(id)` | dá o teclado a um `UiInput` (desfoca o anterior) |
| `open(id, open)` | abre/fecha um `UiModal` por id |
| `select_tab(group, tab)` | seleciona tab num grupo |
| `action(name, arg)` | levanta ação de gameplay (`learn`, `buy`, `sell`, `save`, `load`) |
| `list(name, rows)` | cria/repõe uma fonte de `<UiList>` por script — `rows` = `{{campo=valor, …}, …}`; números/booleanos stringify |
| `create{tag=…, id=…, parent=…, children={…}, …}` | **cria um elemento** em runtime com o mesmo construtor do XML — a chave `children` aceita uma subárvore recursiva de tabelas; sem `id` gera `ui-gen-N`; devolve o id (o elemento APARECE no frame seguinte, é endereçável logo a seguir) |
| `destroy(id)` | remove o elemento e a subárvore (registry incluído) |
| `tween(id, {property=, to=, from=, duration=, easing=, delay=})` | interpola um campo animável (`opacity`, `background`, `color`, `width`, `height`, `top/right/bottom/left`, `rotate`, `scale`, `font-size`); `to`/`from` = número ou cor (`"#ff0000"`); termina com evento `tween_done` |
| `set(name, value)` | **bind de script**: alimenta `bind="nome"`, class-binds `bind="nome:classe"` e `get`/`number`; nomes da engine têm prioridade (colisão = warn e recusa) |

| Reader | Devolve | Notas |
|--------|---------|-------|
| `read(id)` | table ou nil | `{text, value, visible, checked, disabled, rect={x,y,w,h}, hovered}` de QUALQUER elemento com id — inputs reportam o texto digitado, sliders o valor |
| `rect(id)` | table ou nil | `{x, y, w, h}` pós-layout no espaço autoral (píxeis do CSS) |
| `query(seletor)` | {ids…} | casam um seletor do dialeto — tag, `.classe`, `#id`, `:not(.a)`, descendente, `>` e pseudos (`:hover` casa quando o elemento ESTÁ hovered; ancestrais ficam neutros) |
| `classes(id)` | {classes…} | classes actuais do elemento |
| `children(id)` / `parent(id)` | {ids…} / string ou nil | estrutura |
| `exists(id)` | bool | o id é endereçável agora? |
| `focused()` | string ou nil | id do `UiInput` com o teclado |
| `get(name)` | string | valor formatado do binding `name` (`""` se desconhecido); engine primeiro, depois os do `set()` |
| `number(name)` | number | fração 0..1 (ou contagem crua) do binding; idem fallback |
| `is_open(id)` | bool | modal `id` está aberto? |
| `tab(group)` | string | tab selecionada no grupo |
| `clicked(id)` | bool | true **no frame** em que o elemento foi pressionado |
| `events([prefix])` | {eventos…} | **DRENA** a fila de eventos — ver abaixo |
| `list_count(name)` | number | nº de linhas da fonte de lista |
| `rows(name)` | table | cópia das linhas — `{{campo=…}, …}` |

**Eventos** (`viber.ui.events()` devolve o lote do frame e esvazia a fila;
com prefixo, filtra por id a começar por ele — os restantes caem):

| `type` | campos | emitido quando |
|--------|--------|----------------|
| `click` | `id` | elemento pressionado |
| `value_changed` | `id`, `value` | slider muda de valor (barras/cooldowns NÃO emitem — muda a cada frame) |
| `text_changed` | `id`, `text` | input muda de texto por teclado |
| `checked_changed` | `id`, `checked` | check liga/desliga |
| `focus_changed` | `id`, `focused` | input recebe/perde o teclado |
| `tab_changed` | `group`, `tab` | aba activa muda |
| `tween_done` | `id`, `property` | um `viber.ui.tween` terminou |
| `hover_enter` / `hover_leave` | `id` | o ponteiro entrou/saiu do elemento |

```lua
for _, ev in ipairs(viber.ui.events("shop-")) do
  if ev.type == "click" then comprar(ev.id)
  elseif ev.type == "value_changed" then volume(ev.value) end
end
-- O prefixo SÓ retira o que casa: dois scripts com prefixos diferentes
-- convivem; `events()` sem prefixo drena tudo.

-- Criação dinâmica + tween:
local toast = viber.ui.create{ tag = "uibutton", parent = "hud",
  class = "toast", text = "Quest concluída!", style = "background: emerald-700ee" }
viber.ui.tween(toast, {property = "opacity", from = 0, to = 1, duration = 0.25})
viber.ui.destroy(toast)

-- Binds por script (class-binds engine-driven sem loops de Luau):
viber.ui.set("radar.alerta", true)   -- <UiPanel bind="radar.alerta:alerta"/>
```

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

-- introspecção profunda (M1): vitals de QUALQUER entidade, IA, nav, mundo
viber.debug.health(id)             -- {current,max,dead} de qualquer entidade (nil sem Health)
viber.debug.ai(id)                 -- {state="wander|chase",speed,aggro_radius,attack_radius,
                                   --   home,desired,velocity,goal,nav_profile="civil|wild"}
                                   --   (FSM da engine + AiLocomotion + perfil de navmesh)
viber.debug.nav()                  -- pilha de navegação: {enabled,agent_radius,agent_height,
                                   --   tile_size,offroad_cost,tile_center?,tile_generating,
                                   --   tile_generations,tile_obstacles?,census{id→n}}
                                   --   census = estados landmass ("fora-da-mesh"/"sem-caminho"…)
viber.debug.quest(id)              -- quest FUNDA: {id,title,npc,biome,status,objective{kind,
                                   --   target,count,progress_text},visited,rewards{gold,xp,items}}
viber.debug.quest_defs()           -- [{id,title,status,kind,npc}] de todas as quests embutidas
viber.debug.regions()              -- [{id,display_name,fog_density,tint,pp_exposure,
                                   --   pp_bloom_strength}] das <BiomeRegion>
viber.debug.biome_at(x, z)         -- região do ponto (polígono) ou nil (fora de todas)
viber.debug.terrain(x, z)          -- AO VIVO: {height,in_field,on_road,in_water,water_surface?,
                                   --   distance_to_road} (nil sem terreno)
viber.debug.weather_full()         -- {wind,wind_strength,clouds,rain,cycle,scheduler{seed,
                                   --   index,period,timer,target}} — o scheduler é o que o
                                   --   ciclo VAI fazer (set_weather congela-o)
viber.debug.atmosphere()           -- {day,night,golden,fog_density?,fog_color,exposure_scale,
                                   --   bloom_boost} — estado vivo do grading/névoa
viber.debug.border()               -- {radius,warn_seconds,margin} do <WorldBorder>
viber.debug.interior()             -- {active,min?,max?,room_size,room_origin,camera_*} da bolsa
viber.debug.ui_tree()              -- [{id,x,y,w,h,visible,disabled,text?,classes}] — ids
                                   --   declarativos + hud:*/chip:* com RECTS (clique exato)
viber.debug.audio()                -- {buses{master,music,sfx},layers,sinks,total,playing,…}
viber.debug.seeds()                -- {terrain_seed?,world_size?,weather_seed?} (determinismo)
viber.debug.world_hash()           -- hash hex do CONTEÚDO do mundo (soma FNV-1a por entidade,
                                   --   independente de ordem/ids) — A/B "mesma seed, mesmo mundo";
                                   --   `viber debug hash` é o atalho CLI. SÓ é estável com o
                                   --   mundo congelado (step(0)): posições/HP entram no hash
viber.debug.skills()               -- {learned,points,level?,level_points?,cooldowns{dash,heal,
                                   --   strike},bonus_damage?,speed_mult?,max_hp_bonus?,crit_bonus?}
viber.debug.waypoints()            -- {marked,label?,x,y,z?,landmarks[{name,biome}]} (12 marcos)
viber.debug.save_info()            -- {path,exists,bytes?,mtime?} do save deste mundo

-- escrita M2: controlo total (mesmo frame; falhas viram warnings)
viber.debug.set_entity_hp(id, hp)  -- HP absoluto de QUALQUER entidade (clamp [0,max])
viber.debug.set_max_hp(id, max)    -- HP máximo (mín. 1; atual clampado)
viber.debug.quest_force(id, s)     -- força "active"|"ready"|"done"|"not_taken"
viber.debug.quest_progress(id, n)  -- fixa o progresso (kill: contador; visit: N marcos;
                                   --   collect é vault-driven → warning)
viber.debug.vault_set(what, n)     -- valor ABSOLUTO de recurso (gold/wood/stone) ou item
viber.debug.take(what, n)          -- tira do vault (false = stock insuficiente → warning)
viber.debug.skill_learn(id)        -- aprende passiva (pré-requisitos/pontos respeitados;
                                   --   aplica o delta de bónus ao herói, como na UI)
viber.debug.skill_points(n)        -- pontos disponíveis (absoluto)
viber.debug.skill_reset()          -- esquece tudo, devolve pontos, reverte bónus
viber.debug.ai_state(id, s)        -- força "wander"|"chase" (a FSM reavalia por distância;
                                   --   o lever que PERSISTE é ai_aggro)
viber.debug.ai_aggro(id, r)        -- raio de aggro (m)
viber.debug.ai_calm_all()          -- todas as criaturas da FSM → Wander
viber.debug.nav_set{enabled=?, offroad_cost=?, tile_size=?}   -- navmesh ao vivo
viber.debug.postfx{bloom=?, ssao=?, taa=?, dof=?, autoexposure=?, contact_shadows=?,
                   aerial=?, splittone=?, vignette=?, chromatic=?, cas=?,
                   motion_blur=?, volumetrics=?}
                                   -- gates AO VIVO: false corta o efeito (igual a
                                   --   VIBER_NO_*), true restaura — bisseção sem restart
viber.debug.audio_set{master=?, music=?, sfx=?}               -- volumes ao vivo
viber.debug.combat_music("battle"|"boss"|"off")               -- A/B de BGM sem esperar o hold
viber.debug.physics_set{gravity={x,y,z}?, paused=?}           -- Rapier ao vivo
viber.debug.save() / load()        -- pelo mesmo caminho da UI (UiAction)
viber.debug.teleport_to(name)      -- player → primeira entidade com esse nome
viber.debug.spawn(url, x, y, z, {yaw=?, scale=?, color="#hex"?, collider=?, snap=?})
                                   -- primitiva FÍSICA ("box:w,h,d"|"sphere:r"|
                                   --   "cylinder:r,h", collider+corpo fixo por omissão)
                                   --   ou GLB do pool (load assíncrono; snap=true assenta
                                   --   no terreno) → nome debug:spawn:N
viber.debug.spawn_light(x, y, z, {intensity=?, color=?, shadows=?, range=?})
                                   -- PointLight debug:light:N
viber.debug.set_material(id, {base_color="#hex"?, metallic=?, roughness=?, unlit=?,
                              emissive="#hex"?})
                                   -- material PBR ao vivo (só STANDARD: primitivas/GLB —
                                   --   os bindless do terreno não são mutáveis)
viber.debug.set_light(id, {intensity=?, color=?, shadows=?, range=?})
viber.debug.set_camera{distance=?, pitch=?, yaw=?, target=?}  -- yaw NOVO (M2)
viber.debug.clear_markers()        -- remove TODO o namespace debug:* (markers, spawns, luzes)

-- asserção e determinismo (M3)
viber.debug.events(since?)         -- eventos de jogo {seq,time,kind,...} desde o cursor:
                                   --   hurt/damage/death/quest/ui/travel/toast/levelup
                                   --   (viber debug events é o atalho CLI)
viber.debug.step(n)                -- PÁRA e avança EXATAMENTE n frames à speed 1; FICA
                                   --   parado (cada step congela de novo; step(0) só congela)
viber.debug.play()                 -- restaura a speed que estava ANTES da primeira chamada
                                   --   da cadeia de steps

-- auto-descoberta (M4)
viber.debug.apidoc()               -- {debug={nome={signature,description}}, game={}, ui={},
                                   --   profiler={}} — a engine explica-se
                                   --   (viber debug api [--grep x] é o atalho CLI)

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
