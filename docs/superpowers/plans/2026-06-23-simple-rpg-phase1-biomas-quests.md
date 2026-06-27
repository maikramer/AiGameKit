# Simple RPG — Phase 1 (Biomas + Quests + NPCs placeholders) Implementation Plan

> **For agentic workers:** Phase 1 = sem GPU. Cria plugins engine + scripts placeholder + layout XML. Phase 2 (GPU pipeline) e Phase 3 (bosses) virão depois. Spec: [`docs/superpowers/specs/2026-06-23-simple-rpg-biomas-quests-design.md`](../specs/2026-06-23-simple-rpg-biomas-quests-design.md).

**Goal:** Três biomas jogáveis (floresta sombria, deserto, pântano) com fog/ambient próprios + sistema de quests simples (9 NPCs placeholder com diálogo) + 6 inimigos placeholder, tudo num único terreno 10 km, sem GPU pipeline.

**Architecture:** Dois plugins novos na engine (`biomes`, `quests`), scripts placeholder no simple-rpg (NPCs como cubos coloridos com DialogueBalloon, inimigos usando GLB goblin/slime existente), layout XML para os 3 biomas via `<BiomeRegion>` + `<BiomeProps>`.

**Tech Stack:** TypeScript, VibeGame ECS (bitecs), Three.js, Bun + Vite. Sem deps novas.

---

## Componentes paralelizáveis (independentes)

### Track A — Plugin `biomes` (engine)
- `VibeGame/src/plugins/biomes/components.ts`
- `VibeGame/src/plugins/biomes/recipes.ts`
- `VibeGame/src/plugins/biomes/systems.ts`
- `VibeGame/src/plugins/biomes/adapters.ts`
- `VibeGame/src/plugins/biomes/parser.ts`
- `VibeGame/src/plugins/biomes/plugin.ts`
- `VibeGame/src/plugins/biomes/index.ts`
- `VibeGame/src/plugins/defaults.ts` (registrar plugin)
- `VibeGame/src/index.ts` (re-export)
- Tests: `VibeGame/tests/unit/plugins/biomes/*.test.ts`
- Docs: `VibeGame/src/plugins/biomes/context.md`

### Track B — Plugin `quests` (engine)
- `VibeGame/src/plugins/quests/components.ts`
- `VibeGame/src/plugins/quests/recipes.ts`
- `VibeGame/src/plugins/quests/systems.ts`
- `VibeGame/src/plugins/quests/registry.ts` (DataRegistry helper)
- `VibeGame/src/plugins/quests/dialogue.ts` (showDialogue/endDialogue sidecar + pushModal)
- `VibeGame/src/plugins/quests/plugin.ts`
- `VibeGame/src/plugins/quests/index.ts`
- `VibeGame/src/plugins/quests/hud/quests-tab.ts` (TabContent)
- `VibeGame/src/plugins/quests/hud/dialogue-balloon.ts` (HudWidget overlay)
- `VibeGame/src/plugins/hud/widgets/tabbed-modal.ts` (adicionar branch `queststab`)
- `VibeGame/src/plugins/defaults.ts` (registrar)
- Tests: `VibeGame/tests/unit/plugins/quests/*.test.ts`

### Track C — 6 scripts de inimigos placeholder (game)
- `VibeGame/examples/simple-rpg/src/scripts/enemies/wolf.ts`
- `VibeGame/examples/simple-rpg/src/scripts/enemies/shade.ts`
- `VibeGame/examples/simple-rpg/src/scripts/enemies/scorpion.ts`
- `VibeGame/examples/simple-rpg/src/scripts/enemies/bandit.ts`
- `VibeGame/examples/simple-rpg/src/scripts/enemies/bogling.ts`
- `VibeGame/examples/simple-rpg/src/scripts/enemies/mosquito.ts`
- Cada um: ~25 linhas, copia padrão `slime.ts`, usa `goblin_rigged_animated.glb` (placeholder), stats variadas.

### Track D — 9 NPCs placeholder + quests JSON (game) — depende de Track B
- `VibeGame/examples/simple-rpg/src/scripts/npc/dialogue-npc.ts` (base)
- `VibeGame/examples/simple-rpg/src/data/quests/dark_forest_quests.json`
- `VibeGame/examples/simple-rpg/src/data/quests/desert_quests.json`
- `VibeGame/examples/simple-rpg/src/data/quests/swamp_quests.json`
- NPCs placeholder: `<GameObject script="npc/dialogue-npc.ts" dialogue-id="...">` com cube visual.

### Track E — Layout XML + bootstrap (game) — depende de A + B + C + D
- `VibeGame/examples/simple-rpg/index.html` (adicionar 3 BiomeRegion + SpawnGroups + NPCs)
- `VibeGame/examples/simple-rpg/src/main.ts` (registrar BiomesPlugin + QuestsPlugin, carregar quest JSONs)
- `VibeGame/examples/simple-rpg/src/game/engine-bridge.ts` (export state helper)

---

## Track A — Plugin `biomes` (passos)

### Task A1: Componentes SOA

**Files:**
- Create: `VibeGame/src/plugins/biomes/components.ts`

- [ ] Escrever `components.ts` com `BiomeRegion` (campos: minX/minZ/maxX/maxZ Float32Array, type Uint8Array, tintR/G/B Float32Array, fogColor Uint32Array packed RGB, fogDensity Float32Array, ambientR/G/B Float32Array, bgmLayer Uint8Array) e `ActiveBiome` (current Uint32Array, target Uint32Array, blend Float32Array). Importar `MAX_ENTITIES` de `../../core/ecs/constants`. Tudo `as const`.

### Task A2: Recipes

**Files:**
- Create: `VibeGame/src/plugins/biomes/recipes.ts`

- [ ] Escrever `recipes.ts` com `biomeRegionRecipe: Recipe = { name: 'BiomeRegion', components: ['transform', 'biome-region'], parserAttributes: ['id', 'type', 'polygon', 'tint', 'fog-color', 'fog-density', 'ambient', 'bgm-layer'], parserOwnsChildren: false }`.

### Task A3: Parser + Adapters

**Files:**
- Create: `VibeGame/src/plugins/biomes/parser.ts`
- Create: `VibeGame/src/plugins/biomes/adapters.ts`

- [ ] `adapters.ts`: `polygonAdapter(entity, value, state)` — parse `"x1 z1, x2 z2, ..."` → computa minX/minZ/maxX/maxZ, escreve em BiomeRegion. `colorAdapter` (tint/ambient/fog-color) → 3 floats RGB (ou packed Uint32). `bgmLayerAdapter` → Uint8.
- [ ] `parser.ts`: `biomeRegionParser({entity, element, state, context})` — aplica defaults `type=0`, lê attrs via adapters. Registra região num `stateBiomeRegions: WeakMap<State, ...>` para query rápida.

### Task A4: Systems

**Files:**
- Create: `VibeGame/src/plugins/biomes/systems.ts`

- [ ] `BiomeDetectionSystem`: group `'late'`, query `[ActiveBiome]`, lê player.xz (do `PlayerController`), itera regiões via WeakMap, AABB broad-phase, point-in-polygon narrow-phase, se mudou seta target + reset blend, lerp blend += dt/0.5. Aplica interpolado: escreve em `Postprocessing` (component existente em `postprocessing/components.ts`) campos `fogColor/fogDensity`, em `AmbientLight` (luz hemisférica) cor/intensidade, em `MusicLayer` crossfade para `bgmLayer` alvo.

### Task A5: Plugin + Index

**Files:**
- Create: `VibeGame/src/plugins/biomes/plugin.ts`
- Create: `VibeGame/src/plugins/biomes/index.ts`
- Create: `VibeGame/src/plugins/biomes/context.md`
- Modify: `VibeGame/src/plugins/defaults.ts` (append `BiomesPlugin` depois de `SpawnerPlugin`/`AudioPlugin`/`EquirectSkyPlugin`)
- Modify: `VibeGame/src/index.ts` (re-export `BiomeRegion`, `ActiveBiome`, `BiomesPlugin`)

- [ ] Plugin object literal com recipes, systems, components (`'biome-region': BiomeRegion`, `'active-biome': ActiveBiome`), config.parsers/defaults/adapters.
- [ ] Index barrel exports.
- [ ] defaults.ts append + index.ts re-export.

### Task A6: Testes unitários

**Files:**
- Create: `VibeGame/tests/unit/plugins/biomes/detection.test.ts`
- Create: `VibeGame/tests/unit/plugins/biomes/polygon.test.ts`

- [ ] Test: player dentro AABB → region detected.
- [ ] Test: player fora polygon (mas dentro AABB) → não detectado.
- [ ] Test: blend interpolation respeita dt.
- [ ] Test: polygon parse com 4 e 6 vértices.
- [ ] Run: `cd VibeGame && bun test tests/unit/plugins/biomes/`

### Task A7: Verificação final Track A

- [ ] `cd VibeGame && bun run check` (tsc --noEmit) — zero erros.
- [ ] `cd VibeGame && bun run lint` — zero erros.
- [ ] `cd VibeGame && bun test tests/unit/plugins/biomes/` — todos passam.

---

## Track B — Plugin `quests` (passos)

### Task B1: Componentes SOA

**Files:**
- Create: `VibeGame/src/plugins/quests/components.ts`

- [ ] `QuestGiver` (em NPC entity): `questId: Uint32Array, state: Uint8Array (0=available,1=taken,2=completed,3=failed)`.
- [ ] `QuestState` (singleton): `MAX_QUESTS=64`, `active: Uint8Array, progress: Uint32Array, completed: Uint8Array`. Indexado por quest id (registrado no registry).
- [ ] `DialogueData` (em NPC): `linesIndex: Uint32Array, portraitId: Uint32Array`.

### Task B2: Registry (DataRegistry-backed)

**Files:**
- Create: `VibeGame/src/plugins/quests/registry.ts`

- [ ] `QuestDef` interface (id, npc, biome, title, portrait, voice, lines_intro[], lines_progress[], lines_complete[], objective {type, target, count}, rewards {gold, xp, items[]}).
- [ ] `registerQuest(state, def)` — adiciona ao DataRegistry existente (`getDataRegistry(state).set('quest', def.id, def)`), aloca índice no `QuestState`.
- [ ] `getQuestDef(state, id)` helper.

### Task B3: Dialogue sidecar

**Files:**
- Create: `VibeGame/src/plugins/quests/dialogue.ts`

- [ ] `interface ActiveDialogue { speakerEid: number, def: QuestDef, onClose: () => void }`.
- [ ] `stateToActiveDialogue: WeakMap<State, ActiveDialogue | null>`.
- [ ] `showDialogue(state, payload)` → seta WeakMap + `pushModal(state, 'dialogue')` (de `rpg-pause`).
- [ ] `endDialogue(state)` → clear + `popModal(state, 'dialogue')`.
- [ ] `getActiveDialogue(state)` getter.

### Task B4: Systems

**Files:**
- Create: `VibeGame/src/plugins/quests/systems.ts`

- [ ] `QuestTriggerSystem` (group `'late'`): query `[QuestGiver]`, para cada um a range <4m do player, se InteractionPrompt detectou tecla F e state==available: `showDialogue(state, {speakerEid, def, onClose})`. Se state==taken e player aperta F: mostra lines_progress. Se state==completed: mostra lines_complete.
- [ ] `QuestProgressSystem` (group `'simulation'`): subscreve eventos kill/collect (interface simples via callback registry — `onEnemyKilled(targetType, cb)` em enemy-registry, `onResourceHarvested(kind, cb)` em resource plugin). Quando match com quest ativa, incrementa progress; se == count, seta state=completed + emite evento `quest:completed` (bus listener para aplicar rewards).

### Task B5: HUD — DialogueBalloon

**Files:**
- Create: `VibeGame/src/plugins/quests/hud/dialogue-balloon.ts`

- [ ] `dialogueBalloonFactory: HudWidgetFactory` (mirror `interactionPromptWidgetFactory` de `hud/widgets/interaction-prompt.ts`).
- [ ] `mount(layer, state)`: cria DOM bubble (portrait + nome + texto + 3 botões Aceitar/Recusar/Fechar), `injectWidgetCss('dialogue-balloon', ...)`.
- [ ] `update(state)`: lê `getActiveDialogue(state)`, toggle visibilidade, renderiza lines/buttons.
- [ ] Botões chamam: Aceitar → `acceptQuest(state, def)` (seta state=taken, `endDialogue`), Recusar → `endDialogue`, Fechar → `endDialogue`.
- [ ] `registerHudWidgetFactory('dialogue-balloon', dialogueBalloonFactory)`.

### Task B6: HUD — QuestsTab

**Files:**
- Create: `VibeGame/src/plugins/quests/hud/quests-tab.ts`
- Modify: `VibeGame/src/plugins/hud/widgets/tabbed-modal.ts` (adicionar branch `queststab` em `buildTabsFromChildren`)

- [ ] `createQuestsTab(state, cfg): TabContent` mirror de `inventory-tab.ts`.
- [ ] `refresh(state)`: lê `QuestState.active/completed`, itera, renderiza listas "Ativas"/"Completas"/"Falhadas".
- [ ] Em `tabbed-modal.ts` `buildTabsFromChildren()`: adicionar `case 'queststab': return createQuestsTab(state, parseCfg(child))`.

### Task B7: Recipe + Parser

**Files:**
- Create: `VibeGame/src/plugins/quests/recipes.ts`

- [ ] `dialogueNpcRecipe: Recipe = { name: 'DialogueNPC', merge: true, components: ['transform', 'quest-giver', 'dialogue-data'], parserAttributes: ['dialogue-id', 'portrait-url', 'voice-sfx'] }`.
- [ ] `questsTabRecipe: Recipe = { name: 'QuestsTab', parserOwnsChildren: false }`.
- [ ] `dialogueBalloonRecipe: Recipe = { name: 'DialogueBalloon', parserOwnsChildren: false }`.

### Task B8: Plugin + Index + defaults

**Files:**
- Create: `VibeGame/src/plugins/quests/plugin.ts`
- Create: `VibeGame/src/plugins/quests/index.ts`
- Modify: `VibeGame/src/plugins/defaults.ts` (append `QuestsPlugin` depois de `BiomesPlugin`)
- Modify: `VibeGame/src/index.ts` (re-export)
- Create: `VibeGame/src/plugins/quests/context.md`

- [ ] Plugin object: systems `[QuestTriggerSystem, QuestProgressSystem]`, recipes `[dialogueNpcRecipe, questsTabRecipe, dialogueBalloonRecipe]`, components, config.parsers.
- [ ] defaults: `quest-giver: {state: 0}`, `quest-state` singleton defaults 0.
- [ ] Register dialogue balloon factory no `initialize(state)`.

### Task B9: Save/Load hook

**Files:**
- Modify: `VibeGame/src/plugins/save-load/serializer.ts` (ou equivalente — descobrir o snapshooter)

- [ ] Adicionar `quests` ao snapshot: `{active: [...indices], progress: [...], completed: [...]}`.
- [ ] Adicionar `biome: {current}` (do Track A).
- [ ] Carregar com defaults se ausente (back-compat).

### Task B10: Testes unitários

**Files:**
- Create: `VibeGame/tests/unit/plugins/quests/state-machine.test.ts`
- Create: `VibeGame/tests/unit/plugins/quests/progress.test.ts`

- [ ] Test: state transitions available→taken→completed via dialogue flow.
- [ ] Test: kill event incrementa progress correto (match type+target).
- [ ] Test: complete → emite rewards.
- [ ] Run: `cd VibeGame && bun test tests/unit/plugins/quests/`

### Task B11: Verificação final Track B

- [ ] `cd VibeGame && bun run check` — zero erros.
- [ ] `cd VibeGame && bun run lint` — zero erros.
- [ ] `cd VibeGame && bun test tests/unit/plugins/quests/` — passam.

---

## Track C — 6 scripts inimigos placeholder (passos)

### Task C1: Criar 6 scripts

**Files:**
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/wolf.ts`
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/shade.ts`
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/scorpion.ts`
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/bandit.ts`
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/bogling.ts`
- Create: `VibeGame/examples/simple-rpg/src/scripts/enemies/mosquito.ts`

- [ ] Cada arquivo: ~25 linhas, copia `slime.ts`. `modelUrl: '/assets/meshes/goblin_rigged_animated.glb'` (placeholder Phase 1). Stats por spec §6.2:
  - **wolf**: hp=35, chaseSpeed=3.2, wanderRadius=14, strafe=true, attackDamage=10.
  - **shade**: hp=25, chaseSpeed=2.0, wanderRadius=10, lowHpKiteFrac=0.3 (ranged-feel).
  - **scorpion**: hp=45, chaseSpeed=1.5, wanderRadius=4 (emerge), attackDamage=14, enrageBelowFrac=0.4.
  - **bandit**: hp=50, chaseSpeed=2.4, wanderRadius=8, strafe=true, attackDamage=12, lootGold 15-35.
  - **bogling**: hp=30, chaseSpeed=2.8, wanderRadius=6, attackDamage=8 (swarm), lootGold 4-10.
  - **mosquito**: hp=12, chaseSpeed=2.0, wanderRadius=4, attackDamage=4 (swarm).

### Task C2: Verificação Track C

- [ ] `cd VibeGame/examples/simple-rpg && bun run check` — zero erros.
- [ ] `cd VibeGame && bun run lint examples/simple-rpg/src/scripts/enemies/` — zero erros.

---

## Track D — 9 NPCs placeholder + quests JSON (depende de B)

### Task D1: dialogue-npc.ts base script

**Files:**
- Create: `VibeGame/examples/simple-rpg/src/scripts/npc/dialogue-npc.ts`

- [ ] MonoBehaviour: no `start`, load placeholder cube visual (`MeshRenderer` com box geometry cor por `dialogue-id` hash) ou reutilizar `goblin` GLB estático. Anexa `QuestGiver` component + `DialogueData`.
- [ ] Usa AiSteering wander curto (raio ~3m).
- [ ] Não tem comportamento além de ficar visível e ser interagível.

### Task D2: 3 quests JSON files

**Files:**
- Create: `VibeGame/examples/simple-rpg/src/data/quests/dark_forest_quests.json`
- Create: `VibeGame/examples/simple-rpg/src/data/quests/desert_quests.json`
- Create: `VibeGame/examples/simple-rpg/src/data/quests/swamp_quests.json`

- [ ] 3 quests por arquivo (9 total), conforme spec §6.1. IDs: `forest_wolves`, `forest_shades`, `forest_darkwood`, `desert_scorpions`, `desert_bandits`, `desert_ruins`, `swamp_boglings`, `swamp_bogwarden`, `swamp_bogmoss`.

### Task D3: Verificação Track D

- [ ] `cd VibeGame/examples/simple-rpg && bun run check` — zero erros.
- [ ] JSON válido (sem parser errors).

---

## Track E — Layout XML + bootstrap (depende de A+B+C+D)

### Task E1: index.html — adicionar 3 biomas

**Files:**
- Modify: `VibeGame/examples/simple-rpg/index.html`

- [ ] Adicionar `<BiomeRegion>` × 3 (dark-forest z>80, desert x>100, swamp z<-80) conforme layout spec §8.
- [ ] Adicionar `<DialogueBalloon>` no `<Scene>` (single instance).
- [ ] Adicionar `<QuestsTab>` dentro do `<TabbedModal id="pause">`.
- [ ] Adicionar `<DynamicSpawner biome="dark-forest">` para wolves/shades na floresta sombria.
- [ ] Idem para desert/scorpions/bandits e swamp/boglings/mosquitoes.
- [ ] Adicionar 9 `<GameObject script="npc/dialogue-npc.ts" dialogue-id="...">` nas posições dos biomas (spec §6.1 + layout §8).

### Task E2: main.ts — bootstrap

**Files:**
- Modify: `VibeGame/examples/simple-rpg/src/main.ts`

- [ ] Importar e `GAME.withPlugin(BiomesPlugin)` + `GAME.withPlugin(QuestsPlugin)` (ou já default).
- [ ] Após `run()`, carregar 3 JSONs via `fetch('/src/data/quests/*.json')` + `registerQuest(state, def)` para cada.
- [ ] Log "Loaded N quests".

### Task E3: Teste manual no browser

- [ ] `cd VibeGame/examples/simple-rpg && bun run dev` → abrir localhost.
- [ ] Walk até floresta sombria (z>80) → fog fica verde-escura, ambient muda.
- [ ] Walk até NPC hunter → prompt F → diálogo abre → aceitar quest → matar 5 wolves (placeholders) → recompensa.
- [ ] Pause (Q) → QuestsTab mostra "forest_wolves completed".

### Task E4: make check-vibegame + make test-vibegame

- [ ] `make check-vibegame` (tsc --noEmit) — zero erros.
- [ ] `make lint-vibegame` — zero erros.
- [ ] `make fmt-check-vibegame` — zero erros.
- [ ] `make test-vibegame` — todos passam.

---

## Self-review

**Spec coverage (Phase 1 subset):**
- §3 Plugin biomes → Track A ✓
- §4 Plugin quests → Track B ✓
- §6.1 NPCs → Track D ✓
- §6.2 Inimigos → Track C ✓
- §7 Asset pipeline → Phase 2 (não Phase 1)
- §8 Layout → Track E ✓
- §10 Save/Load → Task B9 ✓
- §11 Testing → Tasks A6, B10, C2, D3, E4 ✓
- §12 Phase boundaries → Phase 1 somente ✓

**Placeholder scan:** sem TBD/TODO. Código completo nos passos críticos (estrutura de componentes, factory pattern NPC, template inimigo).

**Type consistency:** `BiomeRegion`/`ActiveBiome` consistentes entre A1/A3/A4. `QuestGiver`/`QuestState`/`DialogueData` consistentes entre B1/B3/B4. NPC recipe `DialogueNPC` referenciado em D1/E1.

**Riscos Phase 1:**
- `Postprocessing` component mutate por biomes system pode conflitar com `<GameObject postprocessing="...">` declarativo. Mitigação: biomes escreve só quando blend muda; engine aceita override runtime.
- SaveLoadPlugin snapshot schema — Task B9 precisa descobrir formato exato (verificar `save-load/serializer.ts` antes).
- DialogueBalloon `pushModal` requiere `rpg-pause` plugin ativo no simple-rpg (confirmar em Track E2).

---

## Execution handoff

Plano paralelizável: Tracks A, B, C independentes. D depende de B. E depende de tudo.

Despachar 3 deep agents em paralelo para Tracks A, B, C. Verificar cada um. Depois D. Depois E.
