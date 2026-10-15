# API Luau `viber.*` — mapa por categoria

Fonte canónica: `Viber/docs/LUA_API.md` (assinaturas completas). Auto-descoberta na
engine viva: `viber debug api --json` (guard test garante paridade docs↔API).
Módulos: `Viber/src/luau/`.

## Runtime

- Chunks mlua sandboxed por path; top-level corre 1× por path (globals partilhados
  entre entidades do mesmo script); estado por entidade em `viber.state()`.
- Hooks: `on_update(dt)`, `on_player_attack(px, pz)` (aggro-chain 15 m).
  Sem `on_add`/`on_remove` — o ciclo de vida é top-level na ativação + `on_update`.
- Setters enfileiram comandos aplicados pós-frame. Orçamento 500 ms/entrada,
  heap 512 MiB/VM. Hot-reload ao gravar (`VIBER_HOT_RELOAD=0` desliga).
- LOD de IA: `activation-radius` (default 45 m) — fora dele o script nem corre;
  `tag="always-active"` nos controllers.

## Grupos da API (LUA_API.md)

| Categoria | Destaques |
|---|---|
| Geral | log, `viber.state()`, `viber.self()` |
| Props exportadas | `--@export nome: tipo = default` + `viber.prop(nome)`/`viber.props()` — valor por instância em `prop-<nome>` |
| Sinais / grupos | `--@signal nome(args)` + `viber.emit(nome, …)`; ouvintes em `connect-<nome>` do emissor; `viber.call_group`, `viber.group`, `viber.in_group`, `viber.sender` |
| Perceção do player | posição/distância/direção do player |
| Terreno vivo | `viber.terrain.lower/raise/flatten/crater(x, z, raio, …)` — overlay esparso, EDITS_PER_FRAME=4, raio ≤ 96 m, Δh ≤ 64 m; não persiste no save (v1) |
| Movimento & rotação | terrain-snapped, nominais de locomoção |
| IA primitivas | wander/chase determinístico |
| Gesto & som | `viber.gesture`, `viber.sound(nome)` (registry 36 clips .ogg) |
| Combate & progressão | dano, HP/XP do player |
| Quests | estado/aceitar/completar |
| Economia & inventário | vault (`viber.vault_take`, itens) |
| Input genérico | `viber.input.*` (teclado/rato cru) |
| Eventos engine→Lua | `viber.events()` — kill/hurt/died/collect/quest_done/level_up/ui_action |
| Timers | `viber.after(seg, fn)`, `viber.every(seg, fn)` |
| Vitais genéricas | `viber.entity_hp/damage/heal/set_max_hp` (qualquer entidade) |
| Procura por nome | `viber.find/find_all` |
| Estado de jogo & módulos | `viber.game()` (world-scoped, persiste no save como `world_kv`), `viber.load` (módulos partilhados — `scripts/lib/fsm.lua`), `viber.save()/load_save()` por script |
| Spawn em runtime | `viber.spawn_prototype(id, …)` instancia um `<Prototype>` com callback |
| Combate & FX | `viber.radial_damage/burst/ring/shake/kick/fov_kick/punch/hit_stop/damage_number/play_clip/entity_despawn/status_clear/entity_position/nearby/player_forward` — combate 100% Lua com paridade nativa |
| Controllers sempre ativos | `tag="always-active"` |
| Diálogo & posse | `viber.own_action` (reclama ação da UI; handler nativo cala — loja 100% Lua), `viber.own_system` (diálogo/abilities/bomba/guarda/hotbar/colheita) |
| Interação & UI | `viber.set_interaction(label, tecla, range)`, `viber.interacted(tecla)`, `viber.ui.*` |
| `viber.ui.*` | `set_text`, `events()` (cliques/mudanças/focos/tween_done), `create{…}`/`destroy`, `query(seletor)`/`rect(id)`, `set(nome, valor)`, `list()` (fonte de dados própria), `tween()` |
| `viber.profiler` | `viber.profiler()`/`viber.profiler_cmd()` |
| `viber.debug.*` | SÓ na REPL do bridge (ver skill viber-live-qa) |

## Semântica que apanha agentes

- **Posição de script = `GlobalTransform`**: o `Transform` de um NPC dentro de um
  grupo é LOCAL; usar isso como posição punha cidades 38 m abaixo. `MoveBy`/
  `set_position` convertem de volta para local com o offset do pai.
- Interações: alcance único (`BASE_RANGE_M` × escala 0.5 default = 1.75 m) e "o
  mais próximo ganha" (`InteractionFocus` por tecla) — dois NPCs sobrepostos não
  disparam ambos ao mesmo [E].
- `viber.teleport_player` assenta sempre o destino na superfície sólida
  (`landing_position` + `TeleportSettle`).
- Erros de runtime dão warn 1× por script e a engine segue — verificar `viber debug logs`.

## Exemplos vivos (examples/simple-rpg)

38 scripts: FSMs de criaturas (`enemies/`), NPC de rua (`npc/`), loja 100% Lua
(`game/shop.lua`), diálogo Lua (`npc/forest-wolves.lua`), controllers de gameplay
(`gameplay.xml` + `always-active`). Micro-jogo sem presets de domínio:
`worlds/lua-demo/` (colheita de cristais em ~60 linhas, `gameplay: none`).
