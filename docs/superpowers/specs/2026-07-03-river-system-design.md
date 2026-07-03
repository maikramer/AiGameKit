# River System — Declarative Rivers Sharing Code with Lakes

**Data:** 2026-07-03
**Scope:** VibeGame (`VibeGame/src/plugins/water/`, `VibeGame/src/plugins/terrain/`)
**Estado:** Design aprovado, pronto para plano de implementação
**Specs relacionados:** Adaptive Heightmap (2026-07-03) — rios usam o density boost do carve.

---

## 1. Problema

O VibeGame tem um sistema de água **fortemente lake-centric**: toda a pipeline (`WaterBody`,
`carveBowl`, `makeLakeGeometry`, `makeWaterMaterial`, shader) assume um único disco circular
centrado num ponto. Não há forma de definir um rio (canal alongado que cruza o mapa).

O `terrain.json` já define `rivers: [{id, source, path, length}]` (terrain-data-loader.ts:20-25),
mas é **código morto** — `spawnWaterEntitiesFromTerrainData` referencia um recipe `'Water'` que
não está registado, e `createRiverWaterEntities` (lake-renderer.ts) é um stub sem callers.

O utilizador quer um **rio que cruza o mapa**, com código partilhado com o sistema de lagos.

## 2. Objetivos e Não-Objetivos

### Objetivos

- **O-1.** Rio declarativo via `<River path="...">` em world coords, com canal esculpido no
  terreno, ribbon mesh de água animada, e integração no registry (drag/splash como lake).
- **O-2.** Partilha máxima de código com lake via abstração `WaterShape` — o lago e o rio são
  duas implementações da mesma interface; o fluxo de aplicação (density boost, carve, dirty,
  spawn, registry, cleanup) vive num helper comum.
- **O-3.** Material de água forma-agnóstico: a métrica `t` (0=eixo/centro, 1=margem) vem
  pré-computada na geometria como varying, em vez de ser radial no fragment shader.
- **O-4.** Refactor não muda o comportamento do `<Lake>` existente (regressão zero).
- **O-5.** Emitter de `terrain.json` rivers (pixel→world coords) liga o pipeline Terrain3D → rio.
- **O-6.** Limpeza de código morto obvious (`createRiverWaterEntities`, `createLakeWaterEntities`,
  recipe fantasma `'Water'`).

### Não-objetivos (fora do âmbito)

- **N-1.** Path procedural (desce o heightmap automaticamente). Path é polilinha declarativa.
- **N-2.** Spline/Catmull-Rom para suavizar o path. Polilinha linear com miter joints.
- **N-3.** Largura/profundidade variável ao longo do path. Constantes (atributos únicos).
- **N-4.** Confluências/deltas/meandros procedural. Casos especiais fora do scope v1.
- **N-5.** Grid bin espacial sobre segmentos para queries O(1) no registry. O(n) por query por
  agora (aceitável para poucos rios); grid bin é follow-up se houver muitos rios longos.
- **N-6.** Wave direcional flow ao longo do path. Bónus opcional, não bloqueador.

## 3. Contexto do Código Actual

Toda a camada de água é lake-centric:

| Componente | Lake-specific | Reutilizável |
|---|---|---|
| `WaterBody` (registry.ts:4-17) | disco (`x,z,radius,shoreRadius,waterY`) | `WeakMap` + register/unregister |
| `carveBowl` (carve.ts:113-157) | perfil radial `(1−t²)^1.5`, `t = dist/radius` | mutação in-place do sampler, esqueleto iterativo |
| `makeLakeGeometry` (systems.ts:264-290) | triangle fan 72-seg, disco | — |
| `makeWaterMaterial` / shader (systems.ts:71-246) | `uCenter`, `uShoreRadius`, `lakeShapeT()` radial | waves, shimmer, fresnel, foam toon-step |
| `LakeApplySystem` (systems.ts:297-445) | disco (rimHeight em anel, carveBowl, fan) | fluxo: density boost, mark dirty, spawn, registry, cleanup |
| `<Lake>` component (components.ts) | `radius`, `depth`, `waterOffset` | `color`, `opacity`, `ripple`, `waterY`, `applied` |

**Abstração chave:** a única coisa forma-dependente em toda a pipeline é a métrica `t`
("quão longe da margem", normalizada [0,1]). Para o lago é radial (`dist/shoreRadius`); para
o rio é lateral (offset ao eixo do path / semi-largura). Se `t` vier pré-computado na
geometria (varying), o material fica agnóstico à forma.

**Código morto a remover:** `spawnWaterEntitiesFromTerrainData` (referencia recipe `'Water'`
inexistente), `createRiverWaterEntities` (stub), `createLakeWaterEntities` (sem callers).

## 4. Arquitetura: `WaterShape` Interface + `applyWaterShape` Helper

### 4.1 Interface `WaterShape`

```ts
// water/water-shape.ts (novo)
import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import type { WaterBody } from './registry';

export interface WaterShapeResult {
  carved: boolean;      // false = sampler flat, retry next frame
  rimY: number;         // crista das margens (para shoreFraction)
  waterY: number;       // superfície da água
}

export interface WaterShape {
  /** AABB em field-local coords (X/Z), para density boost + invalidação chunks/colliders. */
  computeAabb(): WorldAabb;
  /** Esculpe a forma no sampler (mutação in-place, só desce). */
  carve(sampler: HeightSampler): WaterShapeResult;
  /** Geometria da superfície de água (fan para lago, ribbon para rio). */
  buildGeometry(): THREE.BufferGeometry;
  /** Boost de densidade do terrain nesta região (255 para capturar detalhe do carve). */
  densityBoost(): number;
  /** Corpo de água para o registry (queries isPointInWater/waterLevelAt). */
  toWaterBody(): WaterBody;
}
```

### 4.2 Helper `applyWaterShape`

Extrai o fluxo comum do `LakeApplySystem` atual. Vive em `water-shape.ts`:

```ts
export function applyWaterShape(
  state: State,
  entity: number,
  shape: WaterShape,
  waterConfig: { color: number; opacity: number; ripple: number },
): void;
```

Fluxo (passos comuns a lake e river):
1. Espera por terrain field com sampler (data !== null); se flat, retry.
2. `shape.computeAabb()` → `applyOverride(density, aabb, shape.densityBoost())` + `refreshChunkResolutions`.
3. `shape.carve(sampler)` → se `!carved`, retry.
4. `shoreFraction(depth, waterOffset)` para afinar a margem (opcional por forma).
5. Mark dirty: `TerrainChunk.meshDirty`, remove colliders Rapier, `invalidateTerrainBvh`.
6. `shape.buildGeometry()` → material (forma-agnóstico) → mesh → `scene.add`.
7. `registerWaterBody(state, shape.toWaterBody())`.
8. `onDestroy` cleanup: remove mesh, dispose geometry+material, unregister.

### 4.3 `LakeBowl` e `RiverChannel` implementam `WaterShape`

- **`LakeBowl`** (water/lake-bowl.ts): wrappa `carveBowl` + `makeLakeGeometry` + `rimHeight`
  existentes. `computeAabb` = disco AABB. `toWaterBody` = `{kind:'lake',...}`.
- **`RiverChannel`** (water/river-channel.ts): novo `carveChannel` + `makeRiverGeometry` +
  `rimHeightAlongPath`. `computeAabb` = bounding box do path grosso por width×margem.
  `toWaterBody` = `{kind:'river',...}`.

### 4.4 Sistemas ECS

`LakeApplySystem` e `RiverApplySystem` tornam-se thin wrappers que, por entidade não-aplicada:
1. Constroem o `WaterShape` apropriado (`LakeBowl` ou `RiverChannel`) a partir do componente.
2. Chamam `applyWaterShape(state, entity, shape, config)`.

## 5. Registry Genérico

### 5.1 `WaterBody` union

```ts
// registry.ts (refatorado)
export type WaterBody =
  | { kind: 'lake'; x: number; z: number; radius: number; shoreRadius: number; waterY: number }
  | { kind: 'river'; path: ReadonlyArray<readonly [number, number]>; width: number; waterY: number };
```

### 5.2 Queries genéricas com despacho por `kind`

```ts
function containsPoint(body: WaterBody, x: number, z: number): boolean {
  if (body.kind === 'lake') {
    const dx = x - body.x, dz = z - body.z;
    return dx*dx + dz*dz <= body.radius * body.radius;
  }
  // river: distância ao segmento de path mais próximo ≤ width/2
  return distanceToPath(body.path, x, z) <= body.width / 2;
}
```

`isPointInWater`, `waterLevelAt`, `waterBodyAt` despacham via `containsPoint`. `registerWaterBody`
e `unregisterWaterBody` (WeakMap) são agnósticos à forma (já são).

### 5.3 `distanceToPath`

Distância de um ponto à polilinha = mínimo das distâncias ponto-segmento. O(n) por query, n =
número de segmentos. Aceitável para poucos rios; grid bin é follow-up (N-5).

## 6. RiverChannel — Carve + Geometria

### 6.1 `carveChannel(sampler, path, width, depth)`

Análogo ao `carveBowl` mas com perfil transversal ao longo do path:

```
para cada texel (i) dentro do AABB do path:
  1. encontra o segmento do path mais próximo e a distância lateral d (com sinal ignorado)
  2. t = d / (width/2)   // 0 no eixo, 1 na margem
  3. se t >= 1: fora do rio, skip
  4. perfil transversal: bowlY = rimY - depth·(1 - t²)^1.5   // reusa o expoente C1 do lago
  5. data[i] = min(data[i], bowlY / maxHeight)
```

Esqueleto iterativo (loop z/x sobre AABB, mutar `sampler.data` em place) é reutilizado do
`carveBowl`. Só a métrica de distância (lateral ao path em vez de radial ao centro) muda.

### 6.2 `rimHeightAlongPath(sampler, path, width)`

A crista das margens. Probo ao longo das duas margens do path (offset ±width/2 da normal de
cada segmento) em ~32 pontos por segmento, retorna o mínimo. Garante que a água não vaza por
uma margem baixa. Análogo ao `rimHeight` do lago mas com amostragem ao longo do polyline em
vez de anel.

### 6.3 `makeRiverGeometry(path, width)`

Ribbon/strip ao longo do path:
- Em cada nó do path gera 2 vértices offsetados lateralmente por ±width/2 (com a normal do
  segmento).
- **Miter joints** nos nós internos (normal = média das direções dos segmentos adjacentes,
  normalizada) para não haver gap nas curvas.
- **UV.u** = comprimento acumulado ao longo do path (para flow de textura/waves).
- **UV.v** = lateral [0,1].
- **`aWaterT`** = lateral_offset / (width/2) por vértice (0 no eixo, 1 na margem) — o varying
  que o material consome.
- **Oversize das margens:** ribbon gerado a `width × 1.05` para a margem de alpha desvanecer
  sobre a margem carve (análogo ao `pad=1.04` do fan do lago).

## 7. Material Forma-Agnóstico (t como varying)

### 7.1 Mudança no shader

- Remover `lakeShapeT()` do fragment shader (que usa `uCenter`/`uShoreRadius`/`shapeRadius(angle)`).
- Declarar `attribute float aWaterT;` (0 = eixo/centro, 1 = margem) e `varying float vWaterT;`.
  No vertex shader: `vWaterT = aWaterT;`.
- Tudo o que usava `lakeShapeT()` passa a usar `vWaterT`: `lakeDepthNorm`, `shoreAlpha`,
  `lakeFoam`.

### 7.2 Geometrias preenchem `aWaterT`

- **Lago (fan):** `t = dist_do_centro / shoreRadius` por vértice (antes vinha do shader).
  Idêntico visualmente.
- **Rio (ribbon):** `t = lateral_offset / (width/2)` por vértice.

### 7.3 Resultado

O mesmo `makeWaterMaterial` serve ambas as formas sem saber qual é. Uniforms `uCenter`,
`uShoreRadius`, `uSeedX`/`uSeedZ` (só faziam sentido para o disco) são removidos. O
`shapeRadius` orgânico do lago passa a ser pré-aplicado ao `t` na geometria do fan (já é).

## 8. Recipe `<River>`, Componente, Integração terrain.json

### 8.1 Componente `<River>` (bitecs)

```ts
// components.ts (extensão)
export const River = {
  width: new Float32Array(MAX_ENTITIES),
  depth: new Float32Array(MAX_ENTITIES),
  waterOffset: new Float32Array(MAX_ENTITIES),
  color: new Uint32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  ripple: new Float32Array(MAX_ENTITIES),
  waterY: new Float32Array(MAX_ENTITIES),
  applied: new Uint8Array(MAX_ENTITIES),
} as const;
```

O `path` (array de `[x,z]`) é guardado num side-channel `WeakMap<State, Map<entityId, Array<[number,number]>>>`
— bitecs não suporta arrays de arrays. `Transform.posX/posZ` do rio = primeiro ponto do path
(source), para consistência com o modelo de transform.

### 8.2 Recipe XML

```html
<River width="8" depth="2" color="0x3a5a7a" opacity="0.85"
       path="0,0 100,20 200,15 280,40 350,60"></River>
```

`path` é uma string de pares `x,z` separados por espaços. O parser faz split (espaço → ponto;
vírgula → x,z), converte para number[]. Defaults (plugin.ts): `width=6`, `depth=1.5`,
`waterOffset=0.3`, `color=0x3a5a7a`, `opacity=0.85`, `ripple=0.02`.

### 8.3 `RiverApplySystem`

Thin wrapper que, por entidade `<River>` não-aplicada:
1. Lê path do side-channel, width/depth/waterOffset/color/opacity/ripple do componente.
2. Constrói `RiverChannel` (implementa `WaterShape`).
3. Chama `applyWaterShape(state, entity, shape, {color, opacity, ripple})`.

### 8.4 Emitter de terrain.json rivers

`spawnWaterEntitiesFromTerrainData` (terrain-data-loader.ts) é reescrito para emitir entidades
`<River>` a partir de `rivers`, convertendo pixel→world coords:
`worldX = (pixelX / size) * worldSize - worldSize/2`. Isto liga o pipeline Terrain3D → rio
declarativo.

## 9. Ficheiros (bounded units)

| Ficheiro | Responsabilidade | Estado |
|---|---|---|
| `water/water-shape.ts` (novo) | `WaterShape` interface, `applyWaterShape` helper | Novo |
| `water/lake-bowl.ts` (novo) | `LakeBowl implements WaterShape` | Novo |
| `water/river-channel.ts` (novo) | `RiverChannel implements WaterShape`, `carveChannel`, `makeRiverGeometry`, `rimHeightAlongPath` | Novo |
| `water/registry.ts` (refatorado) | `WaterBody` union lake\|river, `containsPoint`, `distanceToPath`, queries genéricas | Refactor |
| `water/systems.ts` (refatorado) | Material forma-agnóstico (t varying); `LakeApplySystem` → thin wrapper; novo `RiverApplySystem` | Refactor médio |
| `water/components.ts` (extensão) | Componente `River` | Extensão |
| `water/plugin.ts` (extensão) | `riverRecipe`, parser de `path`, defaults | Extensão |
| `terrain/terrain-data-loader.ts` (refactor) | Reescrever `spawnWaterEntitiesFromTerrainData` para emitir `<River>` (pixel→world) | Refactor |
| `terrain/lake-renderer.ts` (limpeza) | Remover stubs mortos `createRiverWaterEntities`/`createLakeWaterEntities` | Limpeza |

## 10. Testes (Bun, TDD)

- **`water-shape.test.ts`** (novo): `applyWaterShape` fluxo com mock shape (verifica density
  boost, dirty flags, registry, cleanup).
- **`river-channel.test.ts`** (novo): `carveChannel` (perfil transversal, só desce, AABB
  cobre o path), `makeRiverGeometry` (vértices, miter joints, UV, `aWaterT`), `rimHeightAlongPath`.
- **`registry.test.ts`** (extensão): `WaterBody` river, `distanceToPath`, `containsPoint` para
  rio (ponto dentro/fora do canal, margens).
- **`plugin.test.ts`** (extensão): parse de `<River path="...">`, defaults aplicados.
- **Regressão:** testes existentes de lake (`carve.test.ts`, `terrain-height-sampler.test.ts`)
  não regressam.

## 11. Definição de Pronto

- `make test-vibegame` passa (testes novos + regressão de lake).
- `simple-rpg` com `<River>` a cruzar o mapa renderiza canal esculpido + ribbon de água animada.
- `<Lake>` existente visualmente idêntico (refactor não muda comportamento).
- `isPointInWater`/`waterLevelAt` detetam o rio (drag/splash em effects.ts funcionam).
- `spawnWaterEntitiesFromTerrainData` emite `<River>` a partir de `rivers` no terrain.json.

## 12. Riscos e Mitigações

| Risco | Prob | Impacto | Mitigação |
|---|---|---|---|
| Refactor do material parte o lago visualmente | Média | Alto | `t` no fan do lago = dist/shoreRadius (idêntico ao atual); regressão visual no browser |
| Miter joints degenerados em curvas fechadas (180°) | Baixa | Médio | Clamp do miter length; fallback para bevel join se miter > limite |
| `carveChannel` O(texels × segmentos) lento para rios longos | Média | Médio | AABB apertado por path; só texels dentro do AABB; benchmark em rio real |
| `distanceToPath` O(n) por query em rios longos | Baixa | Baixo | Grid bin follow-up (N-5); aceitável para poucos rios |
| Path em side-channel diverge do componente (race) | Baixa | Médio | Path é set-once na criação da entidade, lido no apply (single-writer) |
| Refactor do LakeApplySystem introduz regressão de timing (grupo 'setup') | Média | Alto | Manter grupo 'setup' e ordem; regressão de spawn (árvores não em água) |

## 13. Follow-ups (fora do âmbito)

- **Wave direcional flow** ao longo do path (UV.u longitudinal) — bónus opcional.
- **Grid bin espacial** sobre segmentos para queries O(1) no registry (N-5).
- **Path procedural** (desce o heightmap), **spline Catmull-Rom**, **largura/profundidade
  variável**, **confluências** (N-1 a N-4).
- **Pântano / coastline / delta** como novas implementações de `WaterShape` (a arquitetura
  suporta-as sem mudanças ao sistema).
