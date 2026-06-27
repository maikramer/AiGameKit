# Simple RPG — Biomas + Quests + NPCs (Expansão)

**Data:** 2026-06-23
**Status:** Aprovado (design), pendente implementação
**Escopo:** Expandir `VibeGame/examples/simple-rpg/` com 3 biomas novos (floresta sombria, deserto, pântano) num único terreno aberto de 10 km, sistema de quests simples (1 quest por NPC), NPCs com diálogo e nova pipeline de assets via GPU.

---

## 1. Objetivos e não-objectivos

### Objetivos

- Adicionar 3 biomas jogáveis ao terreno existente (10 km, sem loading): floresta sombria, deserto, pântano.
- Sistema de quests simples: 1 quest por NPC, fala única (sem branching), quest log básico no pause menu.
- ~9 NPCs novos (3 por bioma) com retrato, voz, objetivo + recompensa.
- ~13 props GLB novos (árvores/rochas/estruturas por bioma), ~9 inimigos rigged+animated novos, 3 chefes de bioma opcionais.
- Recursos novos coletáveis (`dark-wood`, `cactus-fiber`, `bog-moss`) integrados à economia existente.
- Persistência via `SaveLoadPlugin` (quests + bioma ativo).

### Não-objectivos (fora desta fase)

- Sistema de reputação / branching narrativo / finais múltiplos.
- Multi-jogador.
- Voz real (NPCs usam retrato + texto).
- Transição de cena para interiores (cavernas/cidades muradas têm que esperar).
- Gerar 4º bioma ou oceano/praia.

---

## 2. Arquitetura geral

Dois plugins novos na engine VibeGame, mais dados declarativos no `simple-rpg`.

```
VibeGame/src/plugins/
  biomes/      — novo: detecção de bioma ativo + aplica tint/fog/ambient
  quests/      — novo: dialogue UI + quest state machine

VibeGame/examples/simple-rpg/
  src/data/quests/
    dark_forest_quests.json
    desert_quests.json
    swamp_quests.json
  src/scripts/
    enemies/   — wolf.ts, scorpion.ts, bogling.ts, bandit.ts, shade.ts, mosquito.ts
    bosses/    — witch.ts, sand_wyrm.ts, bog_warden.ts
    npc/       — dialogue-npc.ts (comum), wander.ts
  sample-gameassets/
    manifest_full.csv   — +13 props +9 enemies +3 bosses +3 portraits
    game.yaml           — +3 profiles por bioma
```

Sem dependências npm novas. Usa engine core + plugins existentes (`spawner`, `audio`, `ai-steering`, `save-load`, `hud`).

---

## 3. Plugin `biomes`

### 3.1 Componentes (SOA, MAX_ENTITIES=100000)

```ts
BiomeRegion = {
  // AABB para broad-phase
  polyMinX: Float32Array, polyMinZ: Float32Array,
  polyMaxX: Float32Array, polyMaxZ: Float32Array,
  // Identificação
  type: Uint8Array,   // 0=vale, 1=floresta, 2=deserto, 3=pântano
  // Overlays visuais
  tintR: Float32Array, tintG: Float32Array, tintB: Float32Array,
  fogColor: Float32Array, // RGB packed em ui32 ou 3 floats
  fogDensity: Float32Array,
  ambientR: Float32Array, ambientG: Float32Array, ambientB: Float32Array,
  // Audio
  bgmLayer: Uint8Array, // index no MusicLayer: 0=explore, 1=battle, 2=forest, 3=desert, 4=swamp
} as const;

ActiveBiome (singleton, componente no player) = {
  current: Uint32Array,   // biome region id ativo
  target: Uint32Array,    // alvo para blend
  blend: Float32Array,    // 0..1 progress do crossfade
} as const;
```

### 3.2 Recipe XML

```html
<BiomeRegion
  id="dark-forest"
  type="1"
  polygon="-300 80, 300 80, 300 400, -300 400"
  tint="#1a3320"
  fog-color="#0a1815"
  fog-density="0.04"
  ambient="#3a4a55"
  bgm-layer="2">
</BiomeRegion>
```

Atributos:
- `polygon` — lista `x z` (separados por vírgula); simples quadrilátero por bioma.
- `type` — enum inteiro para spawner matching.
- `tint` — cor que modifica o `base-color` do `<Terrain>` ao entrar (lerp via uniform shader ou troca do material).
- `fog-*`, `ambient` — aplicados ao `<Fog>` e luz hemisférica da cena.
- `bgm-layer` — ativa MusicLayer correspondente com crossfade (T28 já existe).

### 3.3 System `BiomeDetectionSystem`

- Grupo: `late` (após player movement).
- Query: player entity com `ActiveBiome`.
- Por frame:
  1. Broad-phase AABB contra todas regiões.
  2. Narrow-phase: point-in-polygon (ray casting) para confirmar dentro.
  3. Se diferente do atual: setar `ActiveBiome.target`, resetar `blend=0`.
  4. Lerp `blend += dt/0.5`; quando atinge 1, `current = target`.
  5. Aplicar visual/audio interpolado: `lerpColor(currentTint, targetTint, blend)` etc.

### 3.4 Hook de spawner (`spawner` plugin existente)

Atributo novo opcional `biome="dark-forest"` em `<StaticSpawner>` / `<DynamicSpawner>`:
- Spawner lê `BiomeRegion` registrado, computa se posição de spawn está dentro do AABB + polygon do bioma alvo.
- Se sim, spawn normal. Se não, re-roll position (até N tentativas).
- Fallback: se spawner não tiver `biome`, comportamento atual (spawn em qualquer lugar dentro do `region-min/max`).

Isso garante que `cactus_lod0.glb` só aparece no deserto, e `pine_dark` só na floresta, mesmo usando o mesmo `StaticSpawner` com grande região.

---

## 4. Plugin `quests`

### 4.1 Componentes

```ts
QuestGiver (em NPC entity) = {
  questId: Uint32Array,
  state: Uint8Array,  // 0=available, 1=taken, 2=completed, 3=failed
} as const;

DialogueData (em NPC entity) = {
  linesIndex: Uint32Array,   // offset no banco de falas
  portraitId: Uint32Array,
  voiceId: Uint32Array,
} as const;

QuestState (global, 1 slot) = {
  MAX_QUESTS: 64, // compile-time
  active: Uint8Array(MAX_QUESTS),
  progress: Uint32Array(MAX_QUESTS),
  completed: Uint8Array(MAX_QUESTS),
} as const;
```

### 4.2 Recipe XML

```html
<GameObject
  name="hunter_npc"
  place="at: -150 150; align-to-terrain: 1"
  script="dialogue-npc.ts"
  dialogue-id="forest_wolves"
  portrait-url="/assets/ui/hunter.png"
  voice-sfx="npc_speak_low">
</GameObject>
```

### 4.3 Diálogo UI

HUD overlay (mesmo grupo do `HudPlugin`):
```
┌─────────────────────────────────────────────┐
│  [portrait 96×96]   Caçador da Cabana        │
│                                              │
│  "Lobos mataram meu gado esta semana..."    │
│  "Mate 5 e te recompenso com ouro."         │
│                                              │
│  [Aceitar quest]   [Recusar]   [Fechar]     │
└─────────────────────────────────────────────┘
```

- Trigger: tecla **F** (já usada para InteractionPrompt) com range 4 m do `QuestGiver`.
- Durante diálogo: input de movimento pausado (`InputSystem.setPaused(true)`), câmera continua.
- Botoes via DOM no `HudScreenLayer` existente (reaproveita infraestrutura do pause modal).

### 4.4 Quest log tab

Novo `<QuestsTab>` no `<TabbedModal id="pause">` existente:

```
Ativas:
  • Caçador da Floresta — lobos: 2/5   [floresta]
  • Nômade do Deserto — escorpiões: 0/3 [deserto]

Completas:
  • Baú do Vale (tutorial)              ✓

Falhadas: (vazias)
```

### 4.5 State machine

```
NPC → press F → abrir diálogo
              → [Aceitar] → state=taken, QuestProgressSystem subscreve
              → [Recusar] → fecha, NPC continua available
              → [Fechar]  → fecha

kill event (enemy-registry) → QuestProgressSystem incrementa progress[target]
                            → se progress == count: state=completed
                            → emite evento quest:completed
                            → NPC fica "completed" → próxima fala = recompensa

Recompensa:
  → ResourceSystem.add(gold/xp)
  → InventorySystem.add(item)
  → playSound('quest_complete')
  → floating FX no player
```

### 4.6 Hooks de eventos

- `enemy-registry.ts` (já existe) emite `kill:{enemyType}` event.
- `ResourceNode` harvest emite `harvest:{kind}` event.
- `QuestProgressSystem` escuta ambos, atualiza `progress` se quest ativa tem objective.type matching.

---

## 5. JSON de quests (declarativo)

Caminho: `src/data/quests/<biome>_quests.json`.

```json
{
  "forest_wolves": {
    "id": "forest_wolves",
    "npc": "hunter_npc",
    "biome": "dark-forest",
    "title": "Caçador da Floresta",
    "portrait": "/assets/ui/hunter.png",
    "voice": "npc_speak_low",
    "lines_intro": [
      "Lobos mataram meu gado esta semana.",
      "Mate 5 e te recompensarei."
    ],
    "lines_progress": [
      "Ainda faltam {remaining} lobos."
    ],
    "lines_complete": [
      "Muito obrigado! Toma este ouro."
    ],
    "objective": {
      "type": "kill",
      "target": "wolf",
      "count": 5
    },
    "rewards": {
      "gold": 200,
      "xp": 150,
      "items": ["wolf_pelt:2"]
    }
  }
}
```

Tipos de objective suportados nesta fase:
- `kill` — matar N inimigos do tipo X.
- `collect` — coletar N recursos do tipo X.
- `talk` — falar com outro NPC (chain quest simples, sem deep branching).

---

## 6. NPCs e inimigos por bioma

### 6.1 NPCs (3 por bioma = 9 total)

| Bioma | NPC | Quest | Recompensa |
|---|---|---|---|
| Floresta sombria | Caçador da Cabana | kill 5 wolves | 200 gold, wolf_pelt |
| Floresta sombria | Bruxa da Cabana | kill 3 shades | 250 gold, health_potion:3 |
| Floresta sombria | Lenhador | collect 8 dark-wood | 150 gold, iron_axe |
| Deserto | Nômade | kill 4 scorpions | 200 gold, cactus_fiber:5 |
| Deserto | Mercador nômade | kill 3 bandits | 300 gold, silk_cloth |
| Deserto | Arqueólogo | collect 6 ruin_fragments | 400 gold, ancient_relic |
| Pântano | Eremita | kill 5 boglings | 220 gold, moss_potion:2 |
| Pântano | Pescador | kill bog_warden boss | 500 gold, blessed_rod |
| Pântano | Druida | collect 10 bog-moss | 280 gold, nature_amulet |

NPCs usam `AiSteeringPlugin` (Yuka wander curto, raio ~3 m em volta do ponto de spawn). Sprite/portrait via Text2D.

### 6.2 Inimigos (scripted, animated)

| Bioma | Inimigo | Behavior |
|---|---|---|
| Floresta sombria | `wolf` | Patrulha, persegue rápido quando vê player, ataca melee |
| Floresta sombria | `shade` | Flutua, ataque ranged fraco, teleporta curto |
| Deserto | `scorpion` | Enterrado na areia, emerge quando player < 5 m, ataque melee venenoso |
| Deserto | `bandit` | Melee + bloqueio, pega recursos dropados |
| Pântano | `bogling` | Salta em direção ao player, knockback |
| Pântano | `mosquito_swarm` | Swarm (10 indivíduos), dano DoT |

### 6.3 Chefes (opcional — fase 1.5)

Cada bioma tem 1 chefe dentro de landmark:
- **Floresta:** Witch Boss (na witch_hut).
- **Deserto:** Sand Wyrm (emerges da areia num círculo demarcado por ruínas).
- **Pântano:** Bog Warden (elemental, surge do lago central do pântano).

Dropam item único que abre future content. Nesta fase: drops são só cosméticos/loot.

### 6.4 Scripts

Seguem padrão de `goblin-wander.ts` / `slime.ts` / `boss.ts`:
- BVH ground sampling (já existe no `creature.ts`).
- `aggro tracking` export.
- Hit flash.
- Death event → `enemy-registry` increment.

---

## 7. Assets pipeline (GPU)

### 7.1 Manifest additions

Atualizar `sample-gameassets/manifest_full.csv`:

| Asset | Flags | Categoria |
|---|---|---|
| `pine_dark` | `lod,3d,paint` | foliage |
| `dead_tree` | `lod,3d,paint` | foliage |
| `mushroom_glow` | `3d,paint` | prop |
| `witch_hut` | `3d,paint,collision` | structure |
| `cactus` | `lod,3d,paint` | foliage |
| `dead_bush` | `3d,paint` | prop |
| `ruin_pillar` | `3d,paint,collision` | structure |
| `scorpion_nest` | `3d,paint,collision` | structure |
| `dead_willow` | `lod,3d,paint` | foliage |
| `lily_pad` | `3d,paint` | prop |
| `moss_rock` | `3d,paint,collision` | prop |
| `swamp_shack` | `3d,paint,collision` | structure |
| `wolf` | `3d,rig,animate` | creature |
| `shade` | `3d,rig,animate` | creature |
| `scorpion` | `3d,rig,animate` | creature |
| `bandit` | `3d,rig,animate` | creature |
| `bogling` | `3d,rig,animate` | creature |
| `mosquito` | `3d,rig,animate` | creature |
| `witch_boss` | `3d,rig,animate` | creature |
| `sand_wyrm_boss` | `3d,rig,animate` | creature |
| `bog_warden_boss` | `3d,rig,animate` | creature |
| `portrait_hunter`, `portrait_witch_npc`, `portrait_lumberjack`, `portrait_nomad`, `portrait_merchant_d`, `portrait_archaeologist`, `portrait_hermit`, `portrait_fisherman`, `portrait_druid` | `2d` | ui |

Total: +13 GLB props/structures, +9 GLB creatures rigged+animated, +3 GLB bosses, +9 PNG portraits.

### 7.2 Profile `game.yaml`

Adicionar:
```yaml
profiles:
  dark_forest:
    output_dir: ../public/assets/meshes/dark_forest/
    quality: medium
    category: creature
  desert:
    output_dir: ../public/assets/meshes/desert/
    quality: medium
  swamp:
    output_dir: ../public/assets/meshes/swamp/
    quality: medium
```

Cada profile roda isoladamente via `gameassets batch --profile ... --manifest manifest_full.csv`.

### 7.3 Terrain regen

```bash
terrain3d generate \
  "Valley with green grass in center (z 0 to 50), \
   dense dark pine forest on mountains to the north (z > 80), \
   flat sandy desert plateau to the east (x > 100), \
   low swampy depression with dead trees to the south (z < -80). \
   Equirectangular heightmap 2048x2048, grayscale." \
  -o ../public/assets/terrain/heightmap.png \
  --world-size 10000 --max-height 200
```

Atualizar `terrain.json` com novos bounds/altura. Spawners existentes (vale) continuam funcionando pois XZ dele é inalterado.

### 7.4 Sky

Manter `public/assets/sky/sky.png` atual (céu único). Atmosfera varia via `fog-color`/`fog-density`/`ambient` por bioma.

### 7.5 Audio novo

Text2Sound presets:
- `bgm_forest.ogg` (misterioso, madeiras)
- `bgm_desert.ogg` (desolado, vento)
- `bgm_swamp.ogg` (denso, graves)
- `sfx_wolf_howl.ogg`, `sfx_scorpion_hiss.ogg`, `sfx_bogling_jump.ogg`
- `npc_speak_low.ogg`, `npc_speak_mid.ogg`, `npc_speak_high.ogg` (voz genérica por retrato)

Adicionar ao `<MusicLayer>` e `defineSoundBank` existentes.

---

## 8. Layout final do mundo (XZ, 10 km terrain)

```
Z+
↑
│ FLORESTA SOMBRIA (z∈[80,400], x∈[-300,300])
│   • 3 NPCs (hunter, witch, lumberjack)
│   • Witch Boss (witch_hut em -150 250)
│   • Wolves x12, Shades x6
│   • Dead trees / pine_dark, mushroom_glow
│ ─── escarpa natural (heightmap) ───
│
├──── VALE CENTRAL (existente, z∈[-60,50], x∈[-60,60]) ────┤
│   Aldeia + Boss ogre + merchant + chest + shrines         │ DESERTO
│   (x∈[100,400], z∈[-200,200])
│   • 3 NPCs (nomad, merchant, archaeologist)
│   • Sand Wyrm Boss (ruínas em 250 0)
│   • Scorpions x10, Bandits x6
│   • Cactus, dead_bush, ruin_pillar
│
├──── (oeste: vale estendido, vale padrão) ────┤
│
│ ─── escarpa sul reforçada ───
│ PÂNTANO (z∈[-400,-80], x∈[-300,300])
│   • 3 NPCs (hermit, fisherman, druid)
│   • Bog Warden Boss (lago em 0 -250)
│   • Boglings x12, Mosquito swarms x4
│   • Dead_willow, moss_rock, lily_pad
↓
Z-
```

---

## 9. Data flow detalhado

```
[Player move]
  → PlayerController update position
  → BiomeDetectionSystem (late):
      query player.xz
      AABB vs BiomeRegion
      point-in-polygon
      if region changed: ActiveBiome.target = newId, blend = 0
      lerp blend → apply tint/fog/ambient/bgm to scene
  → Audio crossfade (MusicLayer layer=bgm)

[Player press F near QuestGiver]
  → InteractionPrompt triggers
  → DialogueTriggerSystem:
      open DialogueUI
      InputSystem.setPaused(true) (movimento)
      load lines_intro do quests.json
  → User clica [Aceitar]
      QuestGiver.state = taken
      QuestProgressSystem subscribe to kill/collect events

[Enemy death (wolf)]
  → enemy-registry emits kill:wolf
  → QuestProgressSystem:
      find active quest with objective.type=kill, target=wolf
      progress[questId]++
      if progress == count: completed, emit quest:completed

[quest:completed]
  → NPC QuestGiver.state = completed
  → ResourceSystem.add(gold, xp)
  → InventorySystem.add(item)
  → playSound('quest_complete')
  → floating FX
  → UI: toast "Quest completa: {title}"

[Save game]
  → SaveLoadPlugin serializa QuestState + ActiveBiome.current
  → localStorage + msgpackr
```

---

## 10. Save/Load

Estado serializado:
```ts
{
  // existente
  hero: { hp, level, xp, gold, wood, stone, inventory, position, ... },
  // novo
  quests: {
    active: number[],
    progress: Record<number, number>,
    completed: number[],
  },
  biome: {
    current: number,
  },
}
```

Compatibilidade: saves antigos (sem `quests`/`biome`) carregam com defaults (`active: []`, `current: 0`).

---

## 11. Testing strategy

### Unit (VibeGame tests)

- `biomes` AABB test (player dentro/fora).
- `biomes` polygon-in-region (concave edge case).
- `biomes` blend interpolation.
- `quests` dialogue state machine (available → taken → completed).
- `quests` quest progress matching (kill vs collect).

### Integration (VibeGame tests)

- Spawn player em bioma → `ActiveBiome.current` atualiza.
- Spawner com `biome="swamp"` só spawna dentro do polygon do swamp.
- Dialogue UI pausa input.
- Save/load preserva `quests` + `biome`.

### E2E (Playwright)

- Abrir diálogo, aceitar quest, matar 5 wolves, verificar recompensa.
- Salvar em bioma, recarregar página, fazer load, verificar bioma preservado.

### simple-rpg-specific

- Test manual: entrar cada bioma, validar fog/ambient/tint diferente do vale.
- Test manual: completar 1 quest por bioma.

---

## 12. Implementation phases

Fase 1 (sem GPU — provar conceito):
1. Plugin `biomes` (componentes + system + recipe + dialogue UI).
2. Plugin `quests` (state machine + JSON loader + UI).
3. Layout dos 3 biomas em `index.html` (compositions para landmarks + spawners reutilizando assets atuais).
4. 9 NPCs placeholder (cubos coloridos + diálogo funcional).
5. 6 scripts de inimigo (wolves/shades/scorpions/bandits/boglings/mosquitoes) usando `capsule` ou `goblin` placeholder.

Fase 2 (GPU pipeline):
6. Atualizar `manifest_full.csv` + `game.yaml`.
7. Rodar `gameassets batch` por bioma.
8. `terrain3d generate` com prompt regional.
9. `text2sound` para BGM/SFX novos.
10. Swap placeholders por assets reais.

Fase 3 (bosses):
11. 3 chefes com patterns próprios.
12. Loot único + acheivements no quest log.

---

## 13. Open questions / riscos

- **Terrain tint blending**: Three.js `Terrain` plugin atual usa `base-color` único. Precisa de uniform shader injection ou troca de material. Avaliar em Fase 1 se cabe nesta fase.
- **Polygon-in-polygon cost**: 4 biomas × ~4 vertices = trivial, sem otimização necessária.
- **Quest overlap**: kills de wolf podem contar para múltiplas quests? Sim, se ambas têm objective matching. Considerado OK.
- **NPC sprites (portraits)**: Text2D gera PNG, mas precisamos consistência visual entre retratos. Considerar batch com prompt prefix fixo ("fantasy RPG character portrait, painterly style, square 512x512").

---

## 14. Dependencies

- Engine VibeGame: `biomes` + `quests` plugins novos (sem deps npm).
- simple-rpg: +9 scripts, +3 boss scripts, +3 quests JSON files, atualizações no `index.html`/`main.ts`.
- Pipeline GPU: Text3D + Paint3D + Animator3D + Terrain3D + Text2Sound + GameAssets batch.
- Sem breaking changes no engine. Recetas existentes (`PlayerGLTF`, `Terrain`, etc.) intactas.
