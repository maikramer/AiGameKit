# Lista de Plugins

Plugins registados em `DefaultPlugins` (`src/plugins/defaults.ts`) e plugins opt-in (registar com `withPlugin`). Cada recipe/elemento listado abaixo corresponde a um plugin real com diretório em `src/plugins/`. O inventário foi cruzado com o código-fonte.

## Core

| Plugin          | Pasta            | Descrição                                                                                                                |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `transforms`    | `transforms/`    | Posição, rotação e escala 3D (componente WorldTransform)                                                                 |
| `physics`       | `physics/`       | Física com Rapier (corpos, colliders, sensors). Recipes: `static-part`, `dynamic-part`, `kinematic-part`, `physics-part` |
| `rendering`     | `rendering/`     | Three.js renderer, câmeras, materiais, texturas. Recipes: `MeshRenderer`, `PointLight`, `SpotLight`                      |
| `input`         | `input/`         | Teclado, mouse e gamepad                                                                                                 |
| `startup`       | `startup/`       | Execução deferida pós-inicialização                                                                                      |
| `animation`     | `animation/`     | Sistema genérico de animação (clips, `HasAnimator`)                                                                      |
| `bvh`           | `bvh/`           | Bounding Volume Hierarchy para acelerar raycasts                                                                         |
| `composition`   | `composition/`   | Grupos de entidades com transform partilhado. Recipe: `Composition`                                                      |
| `entity-script` | `entity-script/` | Scripts por entidade estilo Unity. Recipe: `MonoBehaviour`                                                               |

## Câmera

| Plugin              | Pasta                | Descrição                                                              |
| ------------------- | -------------------- | ---------------------------------------------------------------------- |
| `orbit-camera`      | `orbit-camera/`      | Câmera orbital com zoom e rotação. Recipe: `OrbitCamera`               |
| `player-controller` | `player-controller/` | Câmera em terceira pessoa (segue jogador). Recipe: `ThirdPersonCamera` |

> Nota: não existe plugin `follow-camera`. A câmara de terceira pessoa é `player-controller` (`ThirdPersonCamera`).

## Jogador

| Plugin      | Pasta        | Descrição                                                                  |
| ----------- | ------------ | -------------------------------------------------------------------------- |
| `player`    | `player/`    | Movimento, pulo e controlo de personagem. Recipes: `Player`, `PlayerGLTF`  |
| `gltf-anim` | `gltf-anim/` | Animação de modelos GLTF (regista e atualiza instâncias de `GltfAnimator`) |

## Modelo 3D

| Plugin     | Pasta       | Descrição                                                                                  |
| ---------- | ----------- | ------------------------------------------------------------------------------------------ |
| `gltf-xml` | `gltf-xml/` | Carregamento declarativo de modelos GLB/GLTF via XML. Recipes: `GLTFLoader`, `GLTFDynamic` |

## Ambiente

| Plugin    | Pasta      | Descrição                                                                  |
| --------- | ---------- | -------------------------------------------------------------------------- |
| `sky`     | `sky/`     | Céu equirectangular (2:1 PNG/JPG/HDR) com PMREM/IBL. Recipe: `EquirectSky` |
| `terrain` | `terrain/` | Terreno com LOD a partir de heightmaps. Recipe: `Terrain`                  |
| `biomes`  | `biomes/`  | Regiões de bioma que guiam o spawn por terreno. Recipe: `BiomeRegion`      |

### sky

Carrega texturas equirectangulares (2:1 PNG/JPG/HDR) como background e/ou iluminação IBL (Image-Based Lighting) via PMREM.

```html
<Scene canvas="#canvas">
  <EquirectSky url="/assets/sky/sky.hdr" rotation-deg="90" set-background="true"></EquirectSky>
</Scene>
```

**Componentes:** `equirect-sky`
**Systems:** carrega a textura, gera PMREM, aplica environment/background

Para céu só por código (URL dinâmico), use `applyEquirectSkyEnvironment` (`src/extras/sky-env.ts`).

## Visual

| Plugin           | Pasta             | Descrição                                                               |
| ---------------- | ----------------- | ----------------------------------------------------------------------- |
| `postprocessing` | `postprocessing/` | Efeitos de pós-processamento (bloom, SMAA, dithering, tonemapping)      |
| `particles`      | `particles/`      | Partículas **three.quarks**. Recipes: `ParticleSystem`, `ParticleBurst` |
| `floating-text`  | `floating-text/`  | Texto flutuante (dano, combate)                                         |

Veja [`docs/EFFECT-REGISTRY.md`](EFFECT-REGISTRY.md) para detalhes do sistema de efeitos.

## Lógica

| Plugin         | Pasta           | Descrição                                                                                                                                                                                                                   |
| -------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tweening`     | `tweening/`     | Interpolações suaves (GSAP). Recipe: `Tween`                                                                                                                                                                                |
| `spawner`      | `spawner/`      | Spawn de entidades no terreno. Recipes: `SpawnGroup`, `StaticSpawner`, `DynamicSpawner`, `SpawnExclusion`, `GameObject`                                                                                                     |
| `audio`        | `audio/`        | Áudio espacial (Howler). Recipes: `AudioSource`, `MusicLayer`, `AudioMixer`                                                                                                                                                 |
| `hud`          | `hud/`          | Painéis e widgets em ecrã. Recipes: `HudPanel`, `HudScreenLayer`, `HudWidget`, `Compass`, `Minimap`, `HealthBar`, `XpBar`, `ResourceChip`, `Mission`, `Timer`, `BossBar`, `ControlsBar`, `InteractionPrompt`, `TabbedModal` |
| `quests`       | `quests/`       | Diálogo e quests. Recipes: `DialogueNPC`, `QuestsTab`, `DialogueBalloon`                                                                                                                                                    |
| `destructible` | `destructible/` | Props destrutíveis e objetos quebráveis                                                                                                                                                                                     |

### audio

Áudio via **Howler**: `AudioListener` na câmara; recipe **`<AudioSource>`** com `url`, opcionalmente `loop`, `playing`, `spatial`, `name`, etc.

```html
<Scene canvas="#canvas" resume-audio-on-user-gesture="true">
  <AudioSource url="/assets/audio/bgm.wav" name="bgm" loop="true" playing="true"></AudioSource>
  <AudioSource url="/assets/audio/jump.wav" name="sfx-jump"></AudioSource>
</Scene>
```

**Exports úteis:** `playAudioEmitter`, `resumeAudioContextIfSuspended`, `resumeAudioContextOnFirstUserGesture`, `AudioSystem`.

Documentação completa: [`docs/AUDIO.md`](AUDIO.md).

## Engine features (gameplay)

| Plugin        | Pasta          | Descrição                                                                              |
| ------------- | -------------- | -------------------------------------------------------------------------------------- |
| `raycast`     | `raycast/`     | Raycast Rapier (`castRayAndGetNormal`). Recipe: `RaycastSource`                        |
| `navmesh`     | `navmesh/`     | Navmesh com `three-pathfinding`. Recipes: `NavMesh`, `NavMeshWalkable`, `NavMeshAgent` |
| `ai-steering` | `ai-steering/` | Steering **yuka** (seek / wander / flee). Recipe: `NPC`                                |

## Opcionais (registar com `withPlugin`)

Estes plugins não estão em `DefaultPlugins`.

| Plugin              | Pasta                | Descrição                                                                        |
| ------------------- | -------------------- | -------------------------------------------------------------------------------- |
| `save-load`         | `save-load/`         | Snapshot `msgpackr` em localStorage + componente `serializable`                  |
| `i18n`              | `i18n/`              | Chaves i18n com auto-deteção de locale. Recipes: `I18nText`, `I18n`              |
| `loading`           | `loading/`           | Ecrã de loading e progresso de assets                                            |
| `debug`             | `debug/`             | Overlays de debug (wireframes, stats). Recipe: `PostFxDebugToggle`               |
| `combat`            | `combat/`            | Sistema de combate (fações, projéteis). Recipes: `Faction`, `ProjectileTemplate` |
| `spawn-gate`        | `spawn-gate/`        | Spawners ativados por gatilho. Recipe: `SpawnGate`                               |
| `rpg-core`          | `rpg-core/`          | Contentores de dados RPG e loot. Recipes: `RpgData`, `LootTable`                 |
| `rpg-ai`            | `rpg-ai/`            | IA inimiga RPG. Recipe: `MeleeAi`                                                |
| `rpg-economy`       | `rpg-economy/`       | Lojas e tabelas de preços. Recipe: `PriceTable`                                  |
| `rpg-inventory`     | `rpg-inventory/`     | Inventário. Recipe: `Inventory`                                                  |
| `rpg-pause`         | `rpg-pause/`         | Coordenação de pausa. Recipe: `PauseCoordinator`                                 |
| `rpg-progression`   | `rpg-progression/`   | XP e níveis. Recipe: `Progression`                                               |
| `rpg-resource-node` | `rpg-resource-node/` | Recursos colhíveis. Recipe: `ResourceNode`                                       |
| `rpg-status`        | `rpg-status/`        | Efeitos de estado (veneno, regen, buffs). Recipe: `StatusApplication`            |
| `rpg-vault`         | `rpg-vault/`         | Armazenamento persistente de itens. Recipe: `Vault`                              |

## Planeados (ainda não implementados)

Os plugins abaixo aparecem em revisões antigas da documentação ou notas de roadmap, mas **não têm diretório** em `src/plugins/`. São trabalho futuro, não features distribuídas.

- `follow-camera` — usar `player-controller` (`ThirdPersonCamera`)
- `fog` — neblina volumétrica
- `water` — água com física e reflexos
- `joints` — joints de física
- `lod` — Level of Detail dinâmico
- `network` — multiplayer (Colyseus)
- `respawn` — respawn de entidades
- `sprite` — sprites 2D
- `line` — linhas 3D
- `text` — texto 2D em 3D (troika)
- `text-3d` — integração de modelos do Text3D
- `scene-manifest` — manifests de assets são carregados via GLTF bridge e `gameassets handoff`

## Texture Recipe

O plugin `rendering` inclui o sistema `TextureRecipe` que integra texturas procedurais do Texture2D:

```html
<GameObject
  renderer
  textureRecipe="url: textures/wood.png; repeatMode: 1; repeatX: 4; repeatY: 4; channel: 0">
</GameObject>
```

**Canais:** 0=map (diffuse), 1=normalMap, 2=roughnessMap, 3=metalnessMap

Veja a [arquitetura de plugins](../src/plugins/README.md) para detalhes de como criar novos plugins.
