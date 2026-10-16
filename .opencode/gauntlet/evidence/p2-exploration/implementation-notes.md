# P2 — Notas de implementação

## Engine (`Viber/src`) vs conteúdo (`Viber/examples/simple-rpg`)

### Engine — revelação de bioma por atalaia (núcleo da peça)

| Ficheiro | Mudança |
|----------|---------|
| `travel.rs` | `Biome::Tower` ("tower"/"Atalaias", sem quest de traçado, raio 8); `NotaLandmarkOwned.tower_reveals: Option<String>`; recurso `RevealedBiomes` (HashSet); gating puro `landmark_available()` + `RevealedBiomes::keyed_biomes()` — um marco de bioma com atalaia está trancado até `revealed`; atalaisas SEMPRE disponíveis; mundos sem atalaia NUNCA trancam (back-compat). Gates aplicados em: [F] `nota_measure_system` (com toast-guia "procura a atalaia de X"), [G] `travel_menu_system`, F11 `quest_debug_landmark`. Recurso `LandmarkBlips` + sistema `landmark_blip_system` (snapshot a 2 Hz — varredura de nomes SEM alloc de 79k strings; re-calcula também quando `revealed` muda, para os blips acenderem no frame da ativação). Testes novos: tower roundtrip, gating, back-compat, blips. |
| `recipes/mod.rs` | `<Landmark>` parse do attr `tower-reveals`; aviso de bioma desconhecido inclui "tower". |
| `luau/commands.rs` | `ScriptCommand::RevealBiome(String)`, `ScriptCommand::AddSkillPoints(u32)`. |
| `luau/runtime.rs` | `RevealBiome` aplicado via `commands.queue` (insert em `RevealedBiomes` + log; o toast é do script da torre); `AddSkillPoints` soma em `SkillTree.points` + `LevelState.points` (query curto). `LuauRuntimeLocals.revealed: Option<Res<RevealedBiomes>>` alimenta o snapshot `ctx.revealed`. **Fix de change detection**: `ItemAdd`/`VaultAdd`/`VaultTake` atualizam `ctx.vault` incrementalmente no dreno — a mutação feita pelo PRÓPRIO `luau_update` nunca é vista pelo `is_changed()` do frame seguinte (tick da mudança == last_run do sistema) e `viber.vault_get` ficava preso no valor do arranque; sem isto o contador de sementes e o prémio dos 5 não disparavam. |
| `luau/ctx.rs` | `ScriptCtx.revealed: HashSet<String>` (snapshot p/ `viber.biome_revealed`). |
| `luau/api.rs` | `viber.reveal_biome(bioma)`, `viber.biome_revealed(bioma)`, `viber.add_skill_points(n)` (documentadas em `docs/LUA_API.md` — guard test). |
| `save.rs` | `SaveGame.revealed_biomes: Vec<String>` (v2, serde default); `capture()`/`apply_save()` com `revealed`; params RPG agrupados no SystemParam `RpgSaveState` (o sistema estava no teto de 16 params). Roundtrip coberto por teste. |
| `hud/minimap.rs` | `MinimapLandmark` (6 losangos teal 8px) + `MinimapTower` (2 losangos 11px); sistema próprio `hud_minimap_landmarks_update` (sistema SEPARADO do update do minimapa para não crescer as cadeias de `Without` das queries existentes) — blips do `LandmarkBlips`, mais perto 1.º, presos à borda; `set_diamond_blip` preserva a rotação 45°. |
| `hud/compass.rs` | `CompassLandmark` (4 slots): losangos teal que deslizam na faixa com o rumo — os 4 marcos disponíveis MAIS alinhados com o olhar (atalaias ganham empates); `bearing_delta` com wrap testado. |
| `hud/elements.rs` | Spawn dos pools novos (minimap diamonds rotacionados, compass markers). |
| `ui/bind.rs` + `ui/collect.rs` | Bind `korok-seed` no `UiData` → contador SEMENTES na bolsa declarativa do HUD. |
| `docs/LUA_API.md` | As 3 funções novas documentadas. |

### Conteúdo (exemplo)

| Ficheiro | Conteúdo |
|----------|----------|
| `world/towers.xml` (novo) | 5 atalaisas em `<Composition>` (1 corpo fixed, colisor exato por primitiva): base de pedra, 4 montantes, coluna central, 3 anéis, RAMPAS duplas (ponta da A enterrada!), deck, plataforma +11.2 m, parapeitos, teodolito, cristal emissivo, PointLight 120000 lm sem sombras + sparkle. Entidades-âncora `atalaia-<bioma>` com `poi/survey-tower.lua`. `<SpawnExclusion>` r18. |
| `world/koroks.xml` (novo) | 22 sementes (4 modos de puzzle) + 11 baús escondidos. |
| `world/landmarks.xml` | +5 `<Landmark biome="tower" tower-reveals=...>`. |
| `world.xml` | includes `towers.xml` + `koroks.xml`. |
| `world/hud.xml` | linha SEMENTES na bolsa (bind `korok-seed`). |
| `scripts/poi/survey-tower.lua` | [E] "Sondar a terra" → `viber.reveal_biome` + XP + burst + SFX; idempotente (`viber.biome_revealed`), prompt muda para "A atalaia já falou". |
| `scripts/poi/song-seed.lua` | 4 modos (rock/odd/ring/hide) via `--@export`; persistência em `viber.game()`; `item_add("korok-seed")`; a cada 5 → `viber.add_skill_points(1)` + toast; `despawn_self` ao coletar (e auto-despawn na sessão seguinte). |

## Iterações de geometria (o que o playtest ao vivo apanhou)

1. **Canto em L (2 rampas a 26.6°)** — o patamar de viragem cruzava o topo da
   rampa A e criava um degrau de 1.2 m (CCT sem step-up → travava em x=33.4).
2. **Rampa única a 32.5°** — a ponta da rampa tem uma face ~vertical (perpendicular
   ao declive): o CCT não sobe a face mesmo com `HERO_MAX_SLOPE_DEG=55`. Travado
   na base.
3. **FINAL — zigue-zague com ponta enterrada**: rampa A (17.5°) com a ponta
   0.5 m abaixo do solo (a superfície sai do chão num cunho suave, zero degraus)
   → deck de viragem → rampa B (15.3°) com a boca RENTE à aresta este do deck
   (sem hover — B sobre o deck era head-bonk) → plataforma oeste a +11.2 m, com
   pilares próprios e SEM hover sobre rampa nenhuma. Subida verificada ao vivo
   por caminhada real (chão → topo, 61.1 m de cota).

## Perf

A/B interleaved no mesmo processo (despawn ao vivo das 5 torres via bridge):

```
A (com conteúdo): 48.18 / 48.10 / 48.20 ms
B (sem atalaisas): 48.73 / 48.72 / 51.02 ms
```

Delta dentro do ruído da sessão — o conteúdo novo (5 luzes sem sombras + ~29
emissores leves + ≤3 scripts ativos a qualquer momento pelo LOD de 45 m) não
custa nada mensurável; gate de 5 % cumprido com folga. Entre BOOTS o ambiente
varreu 45–48 ms com e sem conteúdo (composiciones de streaming/compositor) —
por isso o A/B interleaved é o único que conta (regra da casa).

## Resíduos respeitados

- Alcance de interação efetivo = `interact::BASE_RANGE_M` × 0.5 → koroks usam
  range autoral 3.0 (1.5 m efetivo), mesmo padrão dos baús (2.8→1.4).
- Exclusão de spawn da vila (r52): atalaia do vale a 90 m do centro.
- Contratos autorais intactos (`name="player"`, `dialogue-id`, quest JSON ids).
- Nenhum processo alheio morto; instância de teste sob o lock
  (`scripts/instance-lock.sh`), reinícios só da instância própria.
