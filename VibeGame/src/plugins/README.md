# 📦 Arquitetura de Plugins

Visão geral da arquitetura de plugins do VibeGame, baseada em ECS (Entity Component System) com bitecs.

## Conceitos Fundamentais

### Plugin

Ponto de entrada principal. Um plugin registra **systems**, **components**, **recipes** e **config** em uma única interface `Plugin`:

```ts
export interface Plugin {
  readonly systems?: readonly System[];
  readonly recipes?: readonly Recipe[];
  readonly components?: Record<string, Component>;
  readonly config?: Config;
  readonly initialize?: (state: State) => void | Promise<void>;
}
```

Plugins são registrados em `defaults.ts` via `DefaultPlugins` array.

### Component

Define dados puros (sem lógica) armazenados em SOA (Struct of Arrays) via `bitecs/defineComponent`:

```ts
import { defineComponent, Types } from 'bitecs';

export const Fog = defineComponent({
  mode: Types.f32,
  density: Types.f32,
  colorR: Types.f32,
  colorG: Types.f32,
  colorB: Types.f32,
});
```

**Tipos:** `f32`, `ui8`, `ui32`, `eid` (entity reference), `i8`, `i32`.

### System

Função `update(state: State)` que roda todo frame (ou a cada fixed tick). Sistemas leem/escrevem Components via queries:

```ts
export const FogSystem: System = {
  group: 'draw',        // 'setup' | 'simulation' | 'fixed' | 'draw'
  after: [CameraSyncSystem],  // ordenação opcional
  update(state: State) {
    const entities = fogQuery(state.world);
    // ...
  },
};
```

**Grupos de execução (em ordem):** `setup` → `fixed` (physics) → `simulation` → `draw` (render).

### Recipe

Atalho para criar entidades com um conjunto pré-definido de components + overrides:

```ts
export const playerRecipe: Recipe = {
  name: 'player',
  components: ['player', 'transform', 'body', 'collider', 'character-controller', 'input-state'],
  overrides: {
    'body.type': PLAYER_BODY_DEFAULTS.type,
    'collider.radius': PLAYER_COLLIDER_DEFAULTS.radius,
  },
};
```

### Config

Configuração declarativa do plugin, com 6 seções opcionais:

| Seção         | Tipo                                               | Descrição                                  |
| ------------- | -------------------------------------------------- | ------------------------------------------ |
| `defaults`    | `Record<string, Record<string, number>>`           | Valores padrão para components             |
| `enums`       | `Record<string, Record<string, EnumMapping>>`      | Mapeia strings → números no XML            |
| `adapters`    | `Record<string, Record<string, Adapter>>`          | Converte valores XML/JSON para o component |
| `parsers`     | `Record<string, Parser>`                           | Parsers customizados para elementos XML    |
| `shorthands`  | `Record<string, Record<string, ShorthandMapping>>` | Atalhos de atributos                       |
| `validations` | `ValidationRule[]`                                 | Regras de validação para recipes           |

**Exemplo de adapter** (converte cor hex `#ff0000` em R/G/B float):

```ts
function fogColorAdapter(entity: number, value: string, state: State): void {
  const num = parseInt(value.slice(1), 16);
  Fog.colorR[entity] = ((num >> 16) & 0xff) / 255;
  Fog.colorG[entity] = ((num >> 8) & 0xff) / 255;
  Fog.colorB[entity] = (num & 0xff) / 255;
}
```

## Estrutura de um Plugin

```
plugins/
└── meu-plugin/
    ├── plugin.ts        # Exporta MeuPlugin: Plugin (obrigatório)
    ├── components.ts    # defineComponent() para dados ECS
    ├── systems.ts       # Lógica por frame (queries + update)
    ├── recipes.ts       # Atalhos de criação de entidades
    ├── index.ts         # Re-exports públicos
    └── context.md       # Notas de contexto (opcional, para AI agents)
```

## Template de Novo Plugin

### 1. Component (`components.ts`)

```ts
import { defineComponent, Types } from 'bitecs';

export const MeuComponent = defineComponent({
  ativo: Types.ui8,
  valor: Types.f32,
  alvo: Types.eid,
});
```

### 2. System (`systems.ts`)

```ts
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { MeuComponent } from './components';

const query = defineQuery([MeuComponent]);

export const MeuSystem: System = {
  group: 'simulation',
  update(state: State) {
    for (const eid of query(state.world)) {
      if (MeuComponent.ativo[eid] === 1) {
        MeuComponent.valor[eid] += state.time.deltaTime;
      }
    }
  },
};
```

### 3. Recipe (`recipes.ts`)

```ts
import type { Recipe } from '../../core';

export const meuRecipe: Recipe = {
  name: 'meu-component',
  components: ['meu-component', 'transform'],
  overrides: {
    'meu-component.ativo': 1,
    'meu-component.valor': 0,
  },
};
```

### 4. Plugin (`plugin.ts`)

```ts
import type { Plugin } from '../../core';
import { MeuComponent } from './components';
import { MeuSystem } from './systems';
import { meuRecipe } from './recipes';

export const MeuPlugin: Plugin = {
  systems: [MeuSystem],
  recipes: [meuRecipe],
  components: { 'meu-component': MeuComponent },
  config: {
    defaults: {
      'meu-component': { ativo: 1, valor: 0, alvo: 0 },
    },
  },
};
```

### 5. Registro (`defaults.ts`)

Adicione `MeuPlugin` ao array `DefaultPlugins`.

### 6. Re-export (`index.ts`)

```ts
export { MeuComponent } from './components';
export { MeuPlugin } from './plugin';
export { meuRecipe } from './recipes';
export { MeuSystem } from './systems';
```

## Plugins Existentes

| Plugin           | Descrição                                                                        | Complexidade |
| ---------------- | -------------------------------------------------------------------------------- | ------------ |
| `transforms`     | Posição/rotação/escala 3D                                                        | Baixa        |
| `physics`        | Física (Rapier) + colliders                                                      | Alta         |
| `rendering`      | Three.js renderer, câmeras, cenas                                                | Alta         |
| `player`         | Controle de jogador (movimento, pulo, câmera)                                    | Média        |
| `input`          | Teclado/mouse/gamepad                                                            | Média        |
| `orbit-camera`   | Câmera orbital com zoom                                                          | Média        |
| `follow-camera`  | Câmera em terceira pessoa                                                        | Média        |
| `fog`            | Neblina volumétrica + fog exp/linear                                             | Média        |
| `water`          | Água com física, nado, reflexos                                                  | Alta         |
| `terrain`        | Terreno procedural com heightmaps                                                | Alta         |
| `gltf-xml`       | Carregamento de modelos GLB/GLTF                                                 | Alta         |
| `animation`      | Sistema de animação                                                              | Média        |
| `tweening`       | Interpolações suaves (tweens)                                                    | Baixa        |
| `spawner`        | Spawn de entidades                                                               | Baixa        |
| `respawn`        | Respawn de entidades                                                             | Baixa        |
| `lod`            | Level of Detail (near/far)                                                       | Baixa        |
| `startup`        | Execução deferida pós-inicialização                                              | Baixa        |
| `debug`          | Debug overlays (wireframes, etc.)                                                | Baixa        |
| `scene-manifest` | Carregamento de cenas XML                                                        | Média        |
| `text-3d`        | Modelos GLB do Text3D (Hunyuan)                                                  | Baixa        |
| `sky`            | Skybox equirectangular + IBL (PMREM)                                             | Média        |
| `audio`          | Áudio espacial (Howler, `<audio-clip>`) — [`docs/AUDIO.md`](../../docs/AUDIO.md) | Média        |
| `sprite`         | Sprites 2D                                                                       | Baixa        |
| `line`           | Linhas 3D                                                                        | Baixa        |
| `postprocessing` | Bloom, SMAA, dithering, tonemapping (registry)                                   | Alta         |
