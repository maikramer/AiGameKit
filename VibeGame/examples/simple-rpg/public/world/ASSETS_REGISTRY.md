# Registo de assets — Vale do Cristal (simple-rpg)

Gerado: 2026-07-29. Inventário factual de `public/assets/` + ligações em `public/world/`.

## Orientação graphify

- Mapa modular: `public/world/context.md`
- Includes no shell: `index.html`
- Manifesto/canónico: `sample-gameassets/game.yaml`
- **Não referenciar** `_intermediate/` em runtime.

## Resumo executivo

- **82 IDs** de mesh entregáveis em `/assets/meshes/` (lod0/lod1/lod2 + collision).
- **143 referências GLB** únicas nos XML (incl. index.html).
- **Onda composition** (`game.yaml` ids 1549–1944): **23 GLBs completos no disco**, **0 ligados** — distritos usam `<Composition>` de primitivas.
- **Kit vegetação** (`/assets/meshes/vegetation/*.glb`): gerado via `npm run generate-vegetation` (bpy); tapetes `<Vegetation>` ativos (erva ×8 density).
- **`shade`**: GLB no disco; cena usa `bogling` escalado (ver `context.md`).
- **`npc_merchant`**: GLB no disco; entidade `merchant` em `market.xml` **sem** `<GLTFLoader>`.

## Árvore XML do mundo

| Ficheiro                                     | Grupo / conteúdo                                                                                   |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `environment.xml`                            | céu, luz, pós-proc, áudio, clima, `BiomeRegion` ×4                                                 |
| `cities/discordia.xml`                       | shell cidade: `SpawnExclusion` r=42, `TerrainPad`, includes distritos                              |
| `cities/discordia/walls.xml`                 | muralha ±32, portões, torres — **Composition**                                                     |
| `cities/discordia/roads.xml`                 | praça pavimentada — **Composition**                                                                |
| `cities/discordia/houses.xml`                | 4 casas — **Composition** (GLB `village_house`/`shepherd_cottage` prontos)                         |
| `cities/discordia/forge.xml`                 | forja — **Composition** + NPC blacksmith GLTF                                                      |
| `cities/discordia/barn.xml`                  | celeiro — **Composition**                                                                          |
| `cities/discordia/chapel.xml`                | capela **GLTF** + healer                                                                           |
| `cities/discordia/longhouse.xml`             | salão **GLTF** + elder + chest                                                                     |
| `cities/discordia/watch.xml`                 | torre vigia **GLTF** + guard                                                                       |
| `cities/discordia/market.xml`                | bancas **GLTF** + merchant (sem mesh)                                                              |
| `cities/discordia/utilities.xml`             | poço **GLTF**, muro, bancos — **Composition**                                                      |
| `cities/discordia/skirts.xml`                | props periurbanos instanciados                                                                     |
| `cities/discordia/grid.xml`                  | `CityGrid` demo interno + prefab campfire                                                          |
| `spawn/ring.xml`                             | anel recursos ±58, rio, pontes GLB (`river_bridge_wood` S + `river_bridge_stone` W), goblins aviso |
| `paths/network.xml`                          | `<RoadNetwork>` cobble ~2 m                                                                        |
| `paths/trails.xml`                           | ramos terra/areia para landmarks                                                                   |
| `vegetation/{forest,desert,swamp,peaks}.xml` | tapete `<Vegetation>` + spawners árvores/rochas                                                    |
| `landmarks/{forest,desert,swamp,peaks}.xml`  | POIs, ruínas, chefes, exclusões                                                                    |
| `frontier/ridges.xml`                        | cristas diagonais entre cunhas                                                                     |
| `creatures/enemies.xml`                      | `<DynamicSpawner>` por bioma                                                                       |
| `creatures/bosses.xml`                       | 4 chefes (`name="boss"` = ogro final)                                                              |
| `ai/npcs.xml`                                | 12 NPCs quest + diálogo                                                                            |
| `atmosphere/ambient-fx.xml`                  | partículas ambiente                                                                                |
| `cities/town-demo.xml`                       | aldeia demo isolada @ (420,420)                                                                    |

### Includes em `index.html` (ordem de carga)

1. `environment.xml` → 2. terreno (`world-base`) → 3. `cities/discordia.xml` → 4. `spawn/ring.xml` → 5. `paths/network.xml` + `paths/trails.xml` → 6. vegetação ×4 → 7. landmarks ×4 → 8. `frontier/ridges.xml` → 9. `atmosphere/ambient-fx.xml` → 10. creatures → 11. `ai/npcs.xml` → 12. UI inline → 13. `cities/town-demo.xml`

## Texturas, ícones, céu, terreno

| Pasta                | Conteúdo                                              | Uso                                   |
| -------------------- | ----------------------------------------------------- | ------------------------------------- |
| `/assets/textures/`  | 10 albedos + mapas PBR (`pbr_*`)                      | Composition cidade, terreno, estradas |
| `/assets/terrain/`   | `heightmap.png`, `heightmap.base.png`, `terrain.json` | `<Terrain>` em index.html             |
| `/assets/sky/`       | `sky.png`                                             | `environment.xml` equirect            |
| `/assets/icons/`     | 23 ícones HUD/inventário (.png + .json)               | HUD, loot                             |
| `/assets/images/`    | 82 previews Text2D por asset id                       | UI/wiki, não runtime 3D               |
| `/assets/particles/` | 52 sprites                                            | `<ParticleSystem preset>`             |
| `/assets/audio/`     | BGM + SFX (.ogg)                                      | `environment.xml`, combate            |

Texturas principais: `vale_grass`, `forest_floor`, `desert_sand`, `swamp_mud`, `snow_peak`, `mountain_stone`, `cobblestone_road`, `wall_plaster`, `wood_planks`, `roof_tiles`.

## Composition vs GLTFLoader — onda composition

| id                      | GLB no disco | Estado XML                                                        | Região alvo      |
| ----------------------- | ------------ | ----------------------------------------------------------------- | ---------------- |
| `village_forge`         | sim          | **Composition** (cities/discordia/forge.xml — substituir por GLB) | Cidade Discordia |
| `village_house`         | sim          | **Composition** (cities/discordia/houses.xml (4 casas))           | Cidade Discordia |
| `shepherd_cottage`      | sim          | **Composition** (cities/discordia/houses.xml (variante))          | Cidade Discordia |
| `village_barn`          | sim          | **Composition** (cities/discordia/barn.xml)                       | Cidade Discordia |
| `city_gate_arch`        | sim          | **Composition** (walls.xml portões ±32)                           | Cidade Discordia |
| `anvil`                 | sim          | **Composition** (forge.xml (bigorna primitiva))                   | Cidade Discordia |
| `weapon_rack`           | sim          | **Composition** (forge.xml)                                       | Cidade Discordia |
| `forge_bellows`         | sim          | **Composition** (forge.xml)                                       | Cidade Discordia |
| `quench_trough`         | sim          | **Composition** (forge.xml)                                       | Cidade Discordia |
| `horseshoe_pile`        | sim          | **Composition** (forge.xml)                                       | Cidade Discordia |
| `campfire_pit`          | sim          | **Composition** (grid.xml prefab `campfire`)                      | Cidade Discordia |
| `notice_board`          | sim          | **Composition** (utilities.xml)                                   | Cidade Discordia |
| `torch_post`            | sim          | **Composition** (walls.xml / vias)                                | Cidade Discordia |
| `iron_brazier`          | sim          | **Composition** (landmarks/peaks.xml)                             | Picos            |
| `stone_cairn`           | sim          | **Composition** (landmarks/peaks.xml mojões)                      | Picos            |
| `chopping_block`        | sim          | **Composition** (landmarks/forest.xml)                            | Floresta         |
| `log_pile`              | sim          | **Composition** (landmarks/forest.xml)                            | Floresta         |
| `wrecked_boat`          | sim          | **Composition** (landmarks/swamp.xml)                             | Pântano          |
| `bone_altar`            | sim          | **Composition** (landmarks/swamp.xml)                             | Pântano          |
| `sandstone_arch`        | sim          | **Composition** (landmarks/desert.xml §1)                         | Deserto          |
| `desert_obelisk`        | sim          | **Composition** (landmarks/desert.xml §3)                         | Deserto          |
| `crystal_mine_entrance` | sim          | **Composition** (landmarks/peaks.xml — substituir por GLB)        | Picos            |
| `druid_stone_altar`     | sim          | **Composition** (landmarks/forest.xml)                            | Floresta         |

### Já em GLTFLoader (fora da onda ou parcial)

- `chapel` → cities/discordia/chapel.xml
- `village_longhouse` → longhouse.xml + landmarks/forest.xml
- `watchtower` → watch.xml + landmarks/peaks.xml
- `witch_hut` → landmarks/forest.xml
- `swamp_shack` → landmarks/swamp.xml
- `market_stall` → market.xml + landmarks/desert.xml
- `scorpion_nest` → landmarks/desert.xml
- `medieval_well` → utilities.xml

## Registo por categoria

### Edifícios

| id                      | paths (lod0→collision)                                                                                                                                                                                        | região sugerida   | XML hoje    | size_m                 | notas                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ----------- | ---------------------- | ---------------------------------------------- |
| `chapel`                | `/assets/meshes/chapel_lod0.glb`<br>`/assets/meshes/chapel_lod1.glb`<br>`/assets/meshes/chapel_lod2.glb`<br>`/assets/meshes/chapel_collision.glb`                                                             | Cidade Discordia  | GLTFLoader  | 6.0×7.0×4.5 m (L×H×W)  | coll=sim; cities/discordia/chapel.xml          |
| `village_longhouse`     | `/assets/meshes/village_longhouse_lod0.glb`<br>`/assets/meshes/village_longhouse_lod1.glb`<br>`/assets/meshes/village_longhouse_lod2.glb`<br>`/assets/meshes/village_longhouse_collision.glb`                 | Cidade + Floresta | GLTFLoader  | 5.5×5.5×10.0 m (L×H×W) | coll=sim; longhouse.xml + landmarks/forest.xml |
| `village_forge`         | `/assets/meshes/village_forge_lod0.glb`<br>`/assets/meshes/village_forge_lod1.glb`<br>`/assets/meshes/village_forge_lod2.glb`<br>`/assets/meshes/village_forge_collision.glb`                                 | Cidade Discordia  | Composition | 6.0×5.5×5.0 m (L×H×W)  | coll=sim; GLB pronto — migrar de Composition   |
| `village_house`         | `/assets/meshes/village_house_lod0.glb`<br>`/assets/meshes/village_house_lod1.glb`<br>`/assets/meshes/village_house_lod2.glb`<br>`/assets/meshes/village_house_collision.glb`                                 | Cidade Discordia  | Composition | 5.0×4.2×6.0 m (L×H×W)  | coll=sim; GLB pronto — migrar de Composition   |
| `shepherd_cottage`      | `/assets/meshes/shepherd_cottage_lod0.glb`<br>`/assets/meshes/shepherd_cottage_lod1.glb`<br>`/assets/meshes/shepherd_cottage_lod2.glb`<br>`/assets/meshes/shepherd_cottage_collision.glb`                     | Cidade Discordia  | Composition | 5.5×4.0×7.5 m (L×H×W)  | coll=sim; GLB pronto — migrar de Composition   |
| `village_barn`          | `/assets/meshes/village_barn_lod0.glb`<br>`/assets/meshes/village_barn_lod1.glb`<br>`/assets/meshes/village_barn_lod2.glb`<br>`/assets/meshes/village_barn_collision.glb`                                     | Cidade Discordia  | Composition | 8.0×6.0×11.0 m (L×H×W) | coll=sim; GLB pronto — migrar de Composition   |
| `watchtower`            | `/assets/meshes/watchtower_lod0.glb`<br>`/assets/meshes/watchtower_lod1.glb`<br>`/assets/meshes/watchtower_lod2.glb`<br>`/assets/meshes/watchtower_collision.glb`                                             | Cidade + Picos    | GLTFLoader  | 6.0×7.0×4.5 m (L×H×W)  | coll=sim; watch.xml + landmarks/peaks.xml      |
| `witch_hut`             | `/assets/meshes/witch_hut_lod0.glb`<br>`/assets/meshes/witch_hut_lod1.glb`<br>`/assets/meshes/witch_hut_lod2.glb`<br>`/assets/meshes/witch_hut_collision.glb`                                                 | Floresta          | GLTFLoader  | 5.0×4.5×5.0 m (L×H×W)  | coll=sim; landmarks/forest.xml                 |
| `swamp_shack`           | `/assets/meshes/swamp_shack_lod0.glb`<br>`/assets/meshes/swamp_shack_lod1.glb`<br>`/assets/meshes/swamp_shack_lod2.glb`<br>`/assets/meshes/swamp_shack_collision.glb`                                         | Pântano           | GLTFLoader  | 5.0×4.0×5.0 m (L×H×W)  | coll=sim; landmarks/swamp.xml                  |
| `market_stall`          | `/assets/meshes/market_stall_lod0.glb`<br>`/assets/meshes/market_stall_lod1.glb`<br>`/assets/meshes/market_stall_lod2.glb`<br>`/assets/meshes/market_stall_collision.glb`                                     | Cidade + Deserto  | GLTFLoader  | 3.0×2.5×2.0 m (L×H×W)  | coll=sim; market.xml + landmarks/desert.xml    |
| `scorpion_nest`         | `/assets/meshes/scorpion_nest_lod0.glb`<br>`/assets/meshes/scorpion_nest_lod1.glb`<br>`/assets/meshes/scorpion_nest_lod2.glb`<br>`/assets/meshes/scorpion_nest_collision.glb`                                 | Deserto           | GLTFLoader  | 2.5×1.2×2.5 m (L×H×W)  | coll=sim; landmarks/desert.xml                 |
| `crystal_mine_entrance` | `/assets/meshes/crystal_mine_entrance_lod0.glb`<br>`/assets/meshes/crystal_mine_entrance_lod1.glb`<br>`/assets/meshes/crystal_mine_entrance_lod2.glb`<br>`/assets/meshes/crystal_mine_entrance_collision.glb` | Picos             | Composition | 4.0×3.5×3.0 m (L×H×W)  | coll=sim; GLB pronto — migrar de Composition   |

### Props — onda composition (GLB prontos, XML ainda primitivas)

| id                   | paths (lod0→collision)                                                                                                                                                                        | região sugerida  | XML hoje    | size_m                   | notas                                        |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ----------- | ------------------------ | -------------------------------------------- |
| `city_gate_arch`     | `/assets/meshes/city_gate_arch_lod0.glb`<br>`/assets/meshes/city_gate_arch_lod1.glb`<br>`/assets/meshes/city_gate_arch_lod2.glb`<br>`/assets/meshes/city_gate_arch_collision.glb`             | Cidade Discordia | GLTFLoader  | 10.0×5.5×1.2 m (L×H×W)   | coll=envelope; walls.xml                     |
| `river_bridge_wood`  | `/assets/meshes/river_bridge_wood_lod0.glb`<br>`…_lod1/_lod2/_collision.glb`                                                                                                                  | Vale S (rio)     | GLTFLoader  | 18.0×2.5×3.6 m (L×H×W)   | coll=envelope; ring.xml `bridge_south`       |
| `river_bridge_stone` | `/assets/meshes/river_bridge_stone_lod0.glb`<br>`…_lod1/_lod2/_collision.glb`                                                                                                                 | Vale W (rio)     | GLTFLoader  | 18.0×2.5×3.6 m (L×H×W)   | coll=envelope; ring.xml `bridge_west`        |
| `anvil`              | `/assets/meshes/anvil_lod0.glb`<br>`/assets/meshes/anvil_lod1.glb`<br>`/assets/meshes/anvil_lod2.glb`<br>`/assets/meshes/anvil_collision.glb`                                                 | Cidade Discordia | Composition | 0.7×0.9×0.45 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `weapon_rack`        | `/assets/meshes/weapon_rack_lod0.glb`<br>`/assets/meshes/weapon_rack_lod1.glb`<br>`/assets/meshes/weapon_rack_lod2.glb`<br>`/assets/meshes/weapon_rack_collision.glb`                         | Cidade Discordia | Composition | 1.2×1.6×0.4 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `forge_bellows`      | `/assets/meshes/forge_bellows_lod0.glb`<br>`/assets/meshes/forge_bellows_lod1.glb`<br>`/assets/meshes/forge_bellows_lod2.glb`<br>`/assets/meshes/forge_bellows_collision.glb`                 | Cidade Discordia | Composition | 1.0×0.7×0.55 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `quench_trough`      | `/assets/meshes/quench_trough_lod0.glb`<br>`/assets/meshes/quench_trough_lod1.glb`<br>`/assets/meshes/quench_trough_lod2.glb`<br>`/assets/meshes/quench_trough_collision.glb`                 | Cidade Discordia | Composition | 1.4×0.55×0.6 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `horseshoe_pile`     | `/assets/meshes/horseshoe_pile_lod0.glb`<br>`/assets/meshes/horseshoe_pile_lod1.glb`<br>`/assets/meshes/horseshoe_pile_lod2.glb`<br>`/assets/meshes/horseshoe_pile_collision.glb`             | Cidade Discordia | Composition | 0.45×0.25×0.45 m (L×H×W) | coll=sim; GLB pronto — migrar de Composition |
| `campfire_pit`       | `/assets/meshes/campfire_pit_lod0.glb`<br>`/assets/meshes/campfire_pit_lod1.glb`<br>`/assets/meshes/campfire_pit_lod2.glb`<br>`/assets/meshes/campfire_pit_collision.glb`                     | Cidade Discordia | Composition | 1.4×0.35×1.4 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `notice_board`       | `/assets/meshes/notice_board_lod0.glb`<br>`/assets/meshes/notice_board_lod1.glb`<br>`/assets/meshes/notice_board_lod2.glb`<br>`/assets/meshes/notice_board_collision.glb`                     | Cidade Discordia | Composition | 1.6×2.0×0.22 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `torch_post`         | `/assets/meshes/torch_post_lod0.glb`<br>`/assets/meshes/torch_post_lod1.glb`<br>`/assets/meshes/torch_post_lod2.glb`<br>`/assets/meshes/torch_post_collision.glb`                             | Cidade Discordia | Composition | 0.7×1.1×0.7 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `iron_brazier`       | `/assets/meshes/iron_brazier_lod0.glb`<br>`/assets/meshes/iron_brazier_lod1.glb`<br>`/assets/meshes/iron_brazier_lod2.glb`<br>`/assets/meshes/iron_brazier_collision.glb`                     | Picos            | Composition | 0.7×1.1×0.7 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `medieval_well`      | `/assets/meshes/medieval_well_lod0.glb`<br>`/assets/meshes/medieval_well_lod1.glb`<br>`/assets/meshes/medieval_well_lod2.glb`<br>`/assets/meshes/medieval_well_collision.glb`                 | Cidade Discordia | GLTFLoader  | 1.4×1.6×1.4 m (L×H×W)    | coll=sim; utilities.xml                      |
| `stone_cairn`        | `/assets/meshes/stone_cairn_lod0.glb`<br>`/assets/meshes/stone_cairn_lod1.glb`<br>`/assets/meshes/stone_cairn_lod2.glb`<br>`/assets/meshes/stone_cairn_collision.glb`                         | Picos            | Composition | 0.9×1.4×0.9 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `chopping_block`     | `/assets/meshes/chopping_block_lod0.glb`<br>`/assets/meshes/chopping_block_lod1.glb`<br>`/assets/meshes/chopping_block_lod2.glb`<br>`/assets/meshes/chopping_block_collision.glb`             | Floresta         | Composition | 0.7×0.55×0.7 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `log_pile`           | `/assets/meshes/log_pile_lod0.glb`<br>`/assets/meshes/log_pile_lod1.glb`<br>`/assets/meshes/log_pile_lod2.glb`<br>`/assets/meshes/log_pile_collision.glb`                                     | Floresta         | Composition | 1.8×0.8×0.9 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `wrecked_boat`       | `/assets/meshes/wrecked_boat_lod0.glb`<br>`/assets/meshes/wrecked_boat_lod1.glb`<br>`/assets/meshes/wrecked_boat_lod2.glb`<br>`/assets/meshes/wrecked_boat_collision.glb`                     | Pântano          | Composition | 3.5×1.2×1.4 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `bone_altar`         | `/assets/meshes/bone_altar_lod0.glb`<br>`/assets/meshes/bone_altar_lod1.glb`<br>`/assets/meshes/bone_altar_lod2.glb`<br>`/assets/meshes/bone_altar_collision.glb`                             | Pântano          | Composition | 1.6×1.0×1.2 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `druid_stone_altar`  | `/assets/meshes/druid_stone_altar_lod0.glb`<br>`/assets/meshes/druid_stone_altar_lod1.glb`<br>`/assets/meshes/druid_stone_altar_lod2.glb`<br>`/assets/meshes/druid_stone_altar_collision.glb` | Floresta         | Composition | 1.8×0.9×1.2 m (L×H×W)    | coll=sim; GLB pronto — migrar de Composition |
| `sandstone_arch`     | `/assets/meshes/sandstone_arch_lod0.glb`<br>`/assets/meshes/sandstone_arch_lod1.glb`<br>`/assets/meshes/sandstone_arch_lod2.glb`<br>`/assets/meshes/sandstone_arch_collision.glb`             | Deserto          | Composition | 8.0×10.0×2.5 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |
| `desert_obelisk`     | `/assets/meshes/desert_obelisk_lod0.glb`<br>`/assets/meshes/desert_obelisk_lod1.glb`<br>`/assets/meshes/desert_obelisk_lod2.glb`<br>`/assets/meshes/desert_obelisk_collision.glb`             | Deserto          | Composition | 8.0×10.0×2.5 m (L×H×W)   | coll=sim; GLB pronto — migrar de Composition |

### NPCs (humanoid rig+anim)

| id               | paths (lod0→collision)                                                                                                                                                            | região sugerida  | XML hoje   | size_m                  | notas                                      |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---------- | ----------------------- | ------------------------------------------ |
| `hero`           | `/assets/meshes/hero_lod0.glb`<br>`/assets/meshes/hero_lod1.glb`<br>`/assets/meshes/hero_lod2.glb`<br>`/assets/meshes/hero_collision.glb`                                         | Global           | GLTFLoader | 0.55×1.55×0.4 m (L×H×W) | coll=sim; index.html PlayerGLTF            |
| `npc_merchant`   | `/assets/meshes/npc_merchant_lod0.glb`<br>`/assets/meshes/npc_merchant_lod1.glb`<br>`/assets/meshes/npc_merchant_lod2.glb`<br>`/assets/meshes/npc_merchant_collision.glb`         | Cidade Discordia | Sem mesh   | 0.65×1.5×0.5 m (L×H×W)  | coll=sim; adicionar GLTFLoader ao merchant |
| `npc_blacksmith` | `/assets/meshes/npc_blacksmith_lod0.glb`<br>`/assets/meshes/npc_blacksmith_lod1.glb`<br>`/assets/meshes/npc_blacksmith_lod2.glb`<br>`/assets/meshes/npc_blacksmith_collision.glb` | Cidade + quests  | GLTFLoader | 0.75×1.6×0.55 m (L×H×W) | coll=sim; forge.xml + ai/npcs.xml          |
| `npc_scout`      | `/assets/meshes/npc_scout_lod0.glb`<br>`/assets/meshes/npc_scout_lod1.glb`<br>`/assets/meshes/npc_scout_lod2.glb`<br>`/assets/meshes/npc_scout_collision.glb`                     | Quests           | GLTFLoader | 0.6×1.55×0.45 m (L×H×W) | coll=sim; ai/npcs.xml                      |
| `npc_healer`     | `/assets/meshes/npc_healer_lod0.glb`<br>`/assets/meshes/npc_healer_lod1.glb`<br>`/assets/meshes/npc_healer_lod2.glb`<br>`/assets/meshes/npc_healer_collision.glb`                 | Cidade + quests  | GLTFLoader | 0.6×1.55×0.45 m (L×H×W) | coll=sim; chapel.xml + ai/npcs.xml         |
| `npc_guard`      | `/assets/meshes/npc_guard_lod0.glb`<br>`/assets/meshes/npc_guard_lod1.glb`<br>`/assets/meshes/npc_guard_lod2.glb`<br>`/assets/meshes/npc_guard_collision.glb`                     | Cidade + quests  | GLTFLoader | 0.7×1.6×0.5 m (L×H×W)   | coll=sim; watch.xml + ai/npcs.xml          |
| `npc_elder`      | `/assets/meshes/npc_elder_lod0.glb`<br>`/assets/meshes/npc_elder_lod1.glb`<br>`/assets/meshes/npc_elder_lod2.glb`<br>`/assets/meshes/npc_elder_collision.glb`                     | Cidade + quests  | GLTFLoader | 0.6×1.5×0.45 m (L×H×W)  | coll=sim; longhouse.xml + ai/npcs.xml      |

### Inimigos e bosses

| id                | paths (lod0→collision)                                                                                                                                                                | região sugerida | XML hoje   | size_m                   | notas                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | ---------- | ------------------------ | -------------------------------------- |
| `goblin`          | `/assets/meshes/goblin_lod0.glb`<br>`/assets/meshes/goblin_lod1.glb`<br>`/assets/meshes/goblin_lod2.glb`<br>`/assets/meshes/goblin_collision.glb`                                     | Anel + biomas   | GLTFLoader | 0.45×1.15×0.35 m (L×H×W) | coll=sim; spawn/ring.xml + enemies.xml |
| `slime`           | `/assets/meshes/slime_lod0.glb`<br>`/assets/meshes/slime_lod1.glb`<br>`/assets/meshes/slime_lod2.glb`<br>`/assets/meshes/slime_collision.glb`                                         | Anel            | GLTFLoader | 0.7×1.0×0.55 m (L×H×W)   | coll=sim; spawn/ring.xml               |
| `wolf`            | `/assets/meshes/wolf_lod0.glb`<br>`/assets/meshes/wolf_lod1.glb`<br>`/assets/meshes/wolf_lod2.glb`<br>`/assets/meshes/wolf_collision.glb`                                             | Floresta        | GLTFLoader | 1.4×0.75×0.45 m (L×H×W)  | coll=sim; creatures/enemies.xml        |
| `shade`           | `/assets/meshes/shade_lod0.glb`<br>`/assets/meshes/shade_lod1.glb`<br>`/assets/meshes/shade_lod2.glb`<br>`/assets/meshes/shade_collision.glb`                                         | Floresta        | Não ligado | 0.6×1.6×0.4 m (L×H×W)    | coll=sim; substituído por bogling×1.4  |
| `witch_boss`      | `/assets/meshes/witch_boss_lod0.glb`<br>`/assets/meshes/witch_boss_lod1.glb`<br>`/assets/meshes/witch_boss_lod2.glb`<br>`/assets/meshes/witch_boss_collision.glb`                     | Floresta        | GLTFLoader | 0.7×1.8×0.5 m (L×H×W)    | coll=sim; creatures/bosses.xml         |
| `scorpion`        | `/assets/meshes/scorpion_lod0.glb`<br>`/assets/meshes/scorpion_lod1.glb`<br>`/assets/meshes/scorpion_lod2.glb`<br>`/assets/meshes/scorpion_collision.glb`                             | Deserto         | GLTFLoader | 1.2×0.66×0.9 m (L×H×W)   | coll=sim; creatures/enemies.xml        |
| `bandit`          | `/assets/meshes/bandit_lod0.glb`<br>`/assets/meshes/bandit_lod1.glb`<br>`/assets/meshes/bandit_lod2.glb`<br>`/assets/meshes/bandit_collision.glb`                                     | Deserto         | GLTFLoader | 0.55×1.65×0.4 m (L×H×W)  | coll=sim; creatures/enemies.xml        |
| `sand_worm`       | `/assets/meshes/sand_worm_lod0.glb`<br>`/assets/meshes/sand_worm_lod1.glb`<br>`/assets/meshes/sand_worm_lod2.glb`<br>`/assets/meshes/sand_worm_collision.glb`                         | Deserto         | GLTFLoader | 2.6×5.4×1.5 m (L×H×W)    | coll=sim; creatures/bosses.xml         |
| `bogling`         | `/assets/meshes/bogling_lod0.glb`<br>`/assets/meshes/bogling_lod1.glb`<br>`/assets/meshes/bogling_lod2.glb`<br>`/assets/meshes/bogling_collision.glb`                                 | Pântano         | GLTFLoader | 0.5×1.1×0.4 m (L×H×W)    | coll=sim; creatures/enemies.xml        |
| `bog_warden_boss` | `/assets/meshes/bog_warden_boss_lod0.glb`<br>`/assets/meshes/bog_warden_boss_lod1.glb`<br>`/assets/meshes/bog_warden_boss_lod2.glb`<br>`/assets/meshes/bog_warden_boss_collision.glb` | Pântano         | GLTFLoader | 1.4×2.6×1.0 m (L×H×W)    | coll=sim; creatures/bosses.xml         |
| `boss_ogre`       | `/assets/meshes/boss_ogre_lod0.glb`<br>`/assets/meshes/boss_ogre_lod1.glb`<br>`/assets/meshes/boss_ogre_lod2.glb`<br>`/assets/meshes/boss_ogre_collision.glb`                         | Picos           | GLTFLoader | 1.2×2.8×0.9 m (L×H×W)    | coll=sim; creatures/bosses.xml         |

### Vegetação (pipeline split stump/top)

| id              | paths (lod0→collision)                                                                                                                                                        | região sugerida      | XML hoje   | size_m                 | notas                             |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ---------- | ---------------------- | --------------------------------- |
| `pine_dark`     | `/assets/meshes/pine_dark_lod0.glb`<br>`/assets/meshes/pine_dark_lod1.glb`<br>`/assets/meshes/pine_dark_lod2.glb`<br>`/assets/meshes/pine_dark_collision.glb`                 | Floresta + fronteira | GLTFLoader | 2.0×9.0×2.0 m (L×H×W)  | coll=sim; vegetation/forest.xml   |
| `dead_tree`     | `/assets/meshes/dead_tree_lod0.glb`<br>`/assets/meshes/dead_tree_lod1.glb`<br>`/assets/meshes/dead_tree_lod2.glb`<br>`/assets/meshes/dead_tree_collision.glb`                 | Floresta + fronteira | GLTFLoader | 2.5×7.0×1.7 m (L×H×W)  | coll=sim; vegetation/forest.xml   |
| `cactus`        | `/assets/meshes/cactus_lod0.glb`<br>`/assets/meshes/cactus_lod1.glb`<br>`/assets/meshes/cactus_lod2.glb`<br>`/assets/meshes/cactus_collision.glb`                             | Deserto              | GLTFLoader | 1.8×3.5×0.71 m (L×H×W) | coll=sim; vegetation/desert.xml   |
| `dead_bush`     | `/assets/meshes/dead_bush_lod0.glb`<br>`/assets/meshes/dead_bush_lod1.glb`<br>`/assets/meshes/dead_bush_lod2.glb`<br>`/assets/meshes/dead_bush_collision.glb`                 | Deserto              | GLTFLoader | 1.5×1.0×1.5 m (L×H×W)  | coll=sim; vegetation/desert.xml   |
| `dead_willow`   | `/assets/meshes/dead_willow_lod0.glb`<br>`/assets/meshes/dead_willow_lod1.glb`<br>`/assets/meshes/dead_willow_lod2.glb`<br>`/assets/meshes/dead_willow_collision.glb`         | Pântano              | GLTFLoader | 3.9×6.0×3.0 m (L×H×W)  | coll=sim; vegetation/swamp.xml    |
| `lily_pad`      | `/assets/meshes/lily_pad_lod0.glb`<br>`/assets/meshes/lily_pad_lod1.glb`<br>`/assets/meshes/lily_pad_lod2.glb`<br>`/assets/meshes/lily_pad_collision.glb`                     | Pântano              | GLTFLoader | 1.2×0.15×1.2 m (L×H×W) | coll=sim; vegetation/swamp.xml    |
| `mushroom_glow` | `/assets/meshes/mushroom_glow_lod0.glb`<br>`/assets/meshes/mushroom_glow_lod1.glb`<br>`/assets/meshes/mushroom_glow_lod2.glb`<br>`/assets/meshes/mushroom_glow_collision.glb` | Floresta             | Não ligado | 5.0×4.5×5.0 m (L×H×W)  | coll=sim; GLB existe; sem ref XML |
| `mushroom_red`  | `/assets/meshes/mushroom_red_lod0.glb`<br>`/assets/meshes/mushroom_red_lod1.glb`<br>`/assets/meshes/mushroom_red_lod2.glb`<br>`/assets/meshes/mushroom_red_collision.glb`     | Floresta             | GLTFLoader | 0.5×0.7×0.5 m (L×H×W)  | coll=sim; vegetation/forest.xml   |
| `tree_oak`      | `/assets/meshes/tree_oak_lod0.glb`<br>`/assets/meshes/tree_oak_lod1.glb`<br>`/assets/meshes/tree_oak_lod2.glb`<br>`/assets/meshes/tree_oak_collision.glb`                     | Anel vale            | GLTFLoader | 4.0×8.0×4.0 m (L×H×W)  | coll=sim; spawn/ring.xml          |
| `tree_pine`     | `/assets/meshes/tree_pine_lod0.glb`<br>`/assets/meshes/tree_pine_lod1.glb`<br>`/assets/meshes/tree_pine_lod2.glb`<br>`/assets/meshes/tree_pine_collision.glb`                 | Picos + anel         | GLTFLoader | 2.0×9.0×2.0 m (L×H×W)  | coll=sim; vegetation/peaks.xml    |

### Rochas, cristais e formações

| id               | paths (lod0→collision)                                                                                                                                                            | região sugerida | XML hoje   | size_m                  | notas                                                |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | ---------- | ----------------------- | ---------------------------------------------------- |
| `moss_rock`      | `/assets/meshes/moss_rock_lod0.glb`<br>`/assets/meshes/moss_rock_lod1.glb`<br>`/assets/meshes/moss_rock_lod2.glb`<br>`/assets/meshes/moss_rock_collision.glb`                     | Todos biomas    | GLTFLoader | 1.5×1.0×1.2 m (L×H×W)   | coll=sim; vegetation + landmarks                     |
| `rock_mossy`     | `/assets/meshes/rock_mossy_lod0.glb`<br>`/assets/meshes/rock_mossy_lod1.glb`<br>`/assets/meshes/rock_mossy_lod2.glb`<br>`/assets/meshes/rock_mossy_collision.glb`                 | Todos biomas    | GLTFLoader | 1.4×1.0×1.2 m (L×H×W)   | coll=sim; vegetation + landmarks                     |
| `stone_pillar`   | `/assets/meshes/stone_pillar_lod0.glb`<br>`/assets/meshes/stone_pillar_lod1.glb`<br>`/assets/meshes/stone_pillar_lod2.glb`<br>`/assets/meshes/stone_pillar_collision.glb`         | Landmarks       | GLTFLoader | 0.8×0.8×0.8 m (L×H×W)   | coll=sim; landmarks/*.xml menires/ruínas             |
| `ruin_pillar`    | `/assets/meshes/ruin_pillar_lod0.glb`<br>`/assets/meshes/ruin_pillar_lod1.glb`<br>`/assets/meshes/ruin_pillar_lod2.glb`<br>`/assets/meshes/ruin_pillar_collision.glb`             | Landmarks       | GLTFLoader | 1.2×4.0×1.2 m (L×H×W)   | coll=sim; landmarks/desert/swamp.xml                 |
| `crystal_blue`   | `/assets/meshes/crystal_blue_lod0.glb`<br>`/assets/meshes/crystal_blue_lod1.glb`<br>`/assets/meshes/crystal_blue_lod2.glb`<br>`/assets/meshes/crystal_blue_collision.glb`         | Picos           | GLTFLoader | 0.35×0.6×0.35 m (L×H×W) | coll=sim; landmarks/peaks.xml mina                   |
| `form_arch_3`    | `/assets/meshes/form_arch_3_lod0.glb`<br>`/assets/meshes/form_arch_3_lod1.glb`<br>`/assets/meshes/form_arch_3_lod2.glb`<br>`/assets/meshes/form_arch_3_collision.glb`             | Deserto         | Não ligado | 3.2×2.8×1.6 m (L×H×W)   | coll=sim; GLB completo no disco; XML usa Composition |
| `form_cliff_1`   | `/assets/meshes/form_cliff_1_lod0.glb`<br>`/assets/meshes/form_cliff_1_lod1.glb`<br>`/assets/meshes/form_cliff_1_lod2.glb`<br>`/assets/meshes/form_cliff_1_collision.glb`         | Terreno         | Não ligado | 2.1×3.0×2.15 m (L×H×W)  | coll=sim; GLB completo; relevo do heightmap          |
| `form_cliff_20`  | `/assets/meshes/form_cliff_20_lod0.glb`<br>`/assets/meshes/form_cliff_20_lod1.glb`<br>`/assets/meshes/form_cliff_20_lod2.glb`<br>`/assets/meshes/form_cliff_20_collision.glb`     | Terreno         | Não ligado | 2.1×3.0×2.16 m (L×H×W)  | coll=sim; GLB completo; relevo do heightmap          |
| `form_outcrop_2` | `/assets/meshes/form_outcrop_2_lod0.glb`<br>`/assets/meshes/form_outcrop_2_lod1.glb`<br>`/assets/meshes/form_outcrop_2_lod2.glb`<br>`/assets/meshes/form_outcrop_2_collision.glb` | Biomas          | Não ligado | 2.4×1.6×1.8 m (L×H×W)   | coll=sim; GLB completo; substituto moss_rock         |
| `form_outcrop_5` | `/assets/meshes/form_outcrop_5_lod0.glb`<br>`/assets/meshes/form_outcrop_5_lod1.glb`<br>`/assets/meshes/form_outcrop_5_lod2.glb`<br>`/assets/meshes/form_outcrop_5_collision.glb` | Biomas          | Não ligado | 2.0×2.2×1.7 m (L×H×W)   | coll=sim; GLB completo; substituto moss_rock         |
| `form_outcrop_8` | `/assets/meshes/form_outcrop_8_lod0.glb`<br>`/assets/meshes/form_outcrop_8_lod1.glb`<br>`/assets/meshes/form_outcrop_8_lod2.glb`<br>`/assets/meshes/form_outcrop_8_collision.glb` | Biomas          | Não ligado | 2.8×1.8×2.2 m (L×H×W)   | coll=sim; GLB completo; substituto moss_rock         |
| `form_stack_6`   | `/assets/meshes/form_stack_6_lod0.glb`<br>`/assets/meshes/form_stack_6_lod1.glb`<br>`/assets/meshes/form_stack_6_lod2.glb`<br>`/assets/meshes/form_stack_6_collision.glb`         | Biomas          | Não ligado | 1.4×2.4×1.4 m (L×H×W)   | coll=sim; GLB completo; substituto stone_pillar      |
| `form_stack_11`  | `/assets/meshes/form_stack_11_lod0.glb`<br>`/assets/meshes/form_stack_11_lod1.glb`<br>`/assets/meshes/form_stack_11_lod2.glb`<br>`/assets/meshes/form_stack_11_collision.glb`     | Biomas          | Não ligado | 1.1×3.2×1.1 m (L×H×W)   | coll=sim; GLB completo; substituto stone_pillar      |

### Props utilitários / loot

| id               | paths (lod0→collision)                                                                                                                                                            | região sugerida | XML hoje   | size_m                   | notas                                   |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | ---------- | ------------------------ | --------------------------------------- |
| `wooden_barrel`  | `/assets/meshes/wooden_barrel_lod0.glb`<br>`/assets/meshes/wooden_barrel_lod1.glb`<br>`/assets/meshes/wooden_barrel_lod2.glb`<br>`/assets/meshes/wooden_barrel_collision.glb`     | Global clutter  | GLTFLoader | 0.6×0.9×0.6 m (L×H×W)    | coll=sim; landmarks + market            |
| `wooden_bench`   | `/assets/meshes/wooden_bench_lod0.glb`<br>`/assets/meshes/wooden_bench_lod1.glb`<br>`/assets/meshes/wooden_bench_lod2.glb`<br>`/assets/meshes/wooden_bench_collision.glb`         | Global clutter  | GLTFLoader | 1.6×0.5×0.45 m (L×H×W)   | coll=sim; landmarks + forest            |
| `wooden_crate`   | `/assets/meshes/wooden_crate_lod0.glb`<br>`/assets/meshes/wooden_crate_lod1.glb`<br>`/assets/meshes/wooden_crate_lod2.glb`<br>`/assets/meshes/wooden_crate_collision.glb`         | Global clutter  | GLTFLoader | 0.8×0.8×0.8 m (L×H×W)    | coll=sim; landmarks + market            |
| `treasure_chest` | `/assets/meshes/treasure_chest_lod0.glb`<br>`/assets/meshes/treasure_chest_lod1.glb`<br>`/assets/meshes/treasure_chest_lod2.glb`<br>`/assets/meshes/treasure_chest_collision.glb` | Cidade + quests | GLTFLoader | 1.0×0.65×0.65 m (L×H×W)  | coll=sim; longhouse.xml (script chest)  |
| `sword_hero`     | `/assets/meshes/sword_hero_lod0.glb`<br>`/assets/meshes/sword_hero_lod1.glb`<br>`/assets/meshes/sword_hero_lod2.glb`<br>`/assets/meshes/sword_hero_collision.glb`                 | Inventário      | Não ligado | 0.12×1.0×0.04 m (L×H×W)  | coll=sim; GLB existe; equip via main.ts |
| `axe`            | `/assets/meshes/axe_lod0.glb`<br>`/assets/meshes/axe_lod1.glb`<br>`/assets/meshes/axe_lod2.glb`<br>`/assets/meshes/axe_collision.glb`                                             | Inventário      | Não ligado | 0.35×0.9×0.05 m (L×H×W)  | coll=sim; GLB existe                    |
| `spear`          | `/assets/meshes/spear_lod0.glb`<br>`/assets/meshes/spear_lod1.glb`<br>`/assets/meshes/spear_lod2.glb`<br>`/assets/meshes/spear_collision.glb`                                     | Inventário      | Não ligado | 0.08×1.8×0.05 m (L×H×W)  | coll=sim; GLB existe                    |
| `felling_axe`    | `/assets/meshes/felling_axe_lod0.glb`<br>`/assets/meshes/felling_axe_lod1.glb`<br>`/assets/meshes/felling_axe_lod2.glb`<br>`/assets/meshes/felling_axe_collision.glb`             | Colheita        | Não ligado | 0.35×0.85×0.05 m (L×H×W) | coll=sim; GLB existe                    |
| `pickaxe`        | `/assets/meshes/pickaxe_lod0.glb`<br>`/assets/meshes/pickaxe_lod1.glb`<br>`/assets/meshes/pickaxe_lod2.glb`<br>`/assets/meshes/pickaxe_collision.glb`                             | Colheita        | Não ligado | 0.4×0.9×0.06 m (L×H×W)   | coll=sim; GLB existe                    |
| `bomb`           | `/assets/meshes/bomb_lod0.glb`<br>`/assets/meshes/bomb_lod1.glb`<br>`/assets/meshes/bomb_lod2.glb`<br>`/assets/meshes/bomb_collision.glb`                                         | Combate         | Não ligado | 0.25×0.3×0.25 m (L×H×W)  | coll=sim; GLB existe                    |

### Peças split árvore (stump/top collision)

| id  | path | notas |
| --- | ---- | ----- |

## GLBs em falta (referenciados, ausentes no disco)

Kit vegetação (`vegetation/*.glb`) — **presente** (bpy). Regenerar: `npm run generate-vegetation`.

## Proposta de layout por região (sem rewrite ainda)

Manter `index.html` como shell fino. Agrupar cada cunha cardeal numa pasta `regions/`:

```
public/world/
  ASSETS_REGISTRY.md          ← este ficheiro
  context.md
  environment.xml             ← global (céu, biomas, áudio)
  shared/
    paths/network.xml
    paths/trails.xml
    creatures/enemies.xml
    creatures/bosses.xml
    ai/npcs.xml
    atmosphere/ambient-fx.xml
  regions/
    discordia/                  ← cidade murada (±32)
      index.xml                 ← shell: SpawnExclusion + TerrainPad + includes
      walls.xml
      roads.xml
      houses.xml                ← migrar village_house/shepherd_cottage GLB
      forge.xml                 ← migrar village_forge + props forja
      barn.xml                  ← migrar village_barn GLB
      chapel.xml | longhouse.xml | watch.xml | market.xml | utilities.xml
      skirts.xml | grid.xml
    valley/                     ← anel central
      ring.xml                  ← hoje spawn/ring.xml
    forest/                     ← bioma N (+Z)
      vegetation.xml
      landmarks.xml             ← witch_hut, druid_stone_altar GLB, etc.
    desert/                     ← bioma E (+X)
      vegetation.xml
      landmarks.xml             ← sandstone_arch, desert_obelisk GLB
    swamp/                      ← bioma S (-Z)
      vegetation.xml
      landmarks.xml             ← swamp_shack, bone_altar, wrecked_boat GLB
    peaks/                      ← bioma O (-X)
      vegetation.xml
      landmarks.xml             ← crystal_mine_entrance, stone_cairn GLB
    frontier/
      ridges.xml
  demo/
    town-demo.xml               ← isolado @ (420,420)
```

**Includes sugeridos em `index.html`:**

```xml
<Include src="/world/environment.xml" />
<!-- world-base terreno -->
<Include src="/world/regions/discordia/index.xml" />
<Include src="/world/regions/valley/ring.xml" />
<Include src="/world/shared/paths/network.xml" />
<Include src="/world/shared/paths/trails.xml" />
<Include src="/world/regions/forest/vegetation.xml" />
<Include src="/world/regions/forest/landmarks.xml" />
<!-- … desert, swamp, peaks … -->
<Include src="/world/regions/frontier/ridges.xml" />
<Include src="/world/shared/atmosphere/ambient-fx.xml" />
<Include src="/world/shared/creatures/enemies.xml" />
<Include src="/world/shared/creatures/bosses.xml" />
<Include src="/world/shared/ai/npcs.xml" />
```

## Checklist para agentes de rewrite regional

1. Substituir `<Composition>` por `<GameObject>` + `<GLTFLoader>` + `mesh-url: …_collision.glb` onde o GLB existe.
2. Ligar `merchant` a `npc_merchant_lod0.glb`; avaliar `shade_lod0` vs bogling.
3. Trocar arco/obelisco/deserto por `sandstone_arch` / `desert_obelisk` GLBs.
4. Correr `vibegame analyze examples/simple-rpg/index.html` após cada região.
