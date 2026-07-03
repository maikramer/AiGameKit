# Adaptive Heightmap — Variable Mesh Resolution (B + C)

**Data:** 2026-07-03 (revisto 2026-07-03 — ver §12 Revisão)
**Scope:** VibeGame (`VibeGame/src/plugins/terrain/`, `VibeGame/src/plugins/water/`)
**Estado:** Design aprovado, pronto para plano de implementação
**Specs relacionados:** Terrain3D generation-adaptive (spec A, *follow-up — fora do âmbito*)

---

## 1. Problema

O heightfield do VibeGame é um grid **uniforme**: `Float32Array` de dimensão igual à do PNG
(2048² no `simple-rpg`, ≈0,98 m/texel sobre 2000 m). O `<Lake>` carve escreve neste grid em
resolução fina. Mas a malha de render, perto da câmera (LOD level 5), usa um chunk de 62,5 m
com apenas **4 segmentos** (`resolutionForLevel(64, 5) = 4`) → **15,6 m/segmento**. Um lago
de 6 m de raio é **menos de 1 segmento** → a malha re-amostra a bacia fina do heightfield
para uma malha grossa e **apaga-a**. O mesmo se passa na collision (`buildChunkHeightfield`
amostra à resolução da malha).

Resultado: a borda fina da bacia do `<Lake>` é arredondada na renderização e na física,
mesmo existindo em alta resolução no heightfield.

## 2. Objetivos e Não-Objetivos

### Objetivos

- **O-1.** Detalhe fino em lagos esculpidos: a malha e o collider dentro/ao redor de um
  `<Lake>` devem capturar a resolução que já existe no heightfield, sem arredondamento.
- **O-2.** Reduzir VRAM de vertex buffers em zonas que não precisam de detalhe (planaltos):
  menos segmentos onde não há features.
- **O-3.** Formato `.ahgt` uint16 em disco (maior precisão que o PNG 8-bit atual) com
  compressão deflate.
- **O-4.** Caminho aberto para spec A (Terrain3D): o `.ahgt` deve poder transportar uma
  árvore de densidade pré-computada no futuro, sem redesenhar o runtime.
- **O-5.** Retrocompatibilidade total: mapas antigos (PNG 8-bit) continuam a funcionar sem
  mudança de markup.

### Não-objetivos (fora do âmbito)

- **N-1.** Geração adaptive no Terrain3D — **spec A, follow-up**.
- **N-2.** Sparse baseGrid para poupar RAM do heightfield (16 MB Float32). A poupança real
  de RAM exigiria sparse baseGrid, que fica para follow-up / spec A.
- **N-3.** Patch tree de amostras de altura (abordagem rejeitada na revisão — ver §12).
- **N-4.** Refactor arbitrário do plugin de terrain.

## 3. Contexto do Código Actual

Fonte da verdade: `HeightSampler` (`VibeGame/src/plugins/terrain/height-sampler.ts:13-24`).

| Grid | Resolução | Definido em |
|---|---|---|
| Heightfield lógico (baseGrid) | = dim do PNG (2048²) | `loadHeightmapFromUrl` (`height-sampler.ts:109-166`) |
| Render mesh (por chunk) | `resolutionForLevel(base, level)` → 64…4 seg | `TerrainLodSelectSystem` (`systems.ts:816`), `lod-select.ts:149` |
| Collision (Rapier, por chunk) | = mesh (`(res+1)²` amostras) | `buildChunkHeightfield` (`systems.ts:1003-1026`) |

`TerrainLodSelectSystem` (`systems.ts:706`) chama `selectChunks` (quadtree por distância de
câmera) e atribui `resolutionForLevel(baseResolution, desc.level)` a cada chunk
(`systems.ts:816`). É **exatamente o ponto** onde a densidade espacial se sobrepõe.

Skirts já existem em `chunk-geometry.ts:93-127` (`addSkirtStrip`) — cobrem T-junctions entre
chunks de níveis diferentes; reutilizam-se sem mudança.

Carve (`water/carve.ts:carveBowl`) muta `sampler.data` (Float32Array) em O(1) por texel.
`LakeApplySystem` (`water/systems.ts:232`) repropaga derivados. **Não muda neste design.**

## 4. Abordagem: Resolução de Malha Variável por Região

A "densidade variável" que resolve o problema não é uma nova estrutura de dados de altura —
é uma **resolução de malha/collision variável por região**: chunks dentro/ao redor de uma
feature importante (lago, canyon) usam mais segmentos; planaltos usam menos.

Isto funde-se com a quadtree de LOD que **já existe** em `lod-select.ts`: sobrepõe-se uma
"densidade espacial" à "densidade por câmera". O heightfield base continua full-res (fonte
do detalhe); a malha é que aprende a amostrá-lo mais finamente onde importa.

### 4.1 Density map

Grelha coarse (default 64×64 tiles sobre o mundo). Cada tile tem um `densityBoost`
(uint8, 0…255) que indica "quanto mais segmentos este tile merece". Fontes:

1. **Densidade-map automática** (construída em runtime na primeira carga): para cada tile,
   score = combinação de (a) gradiente/variância local das alturas, (b) curvatura.
   Tiles high-score → densityBoost alto; low-score → 0.
2. **Overrides declarativos** — recolhidos antes/durante a construção:
   - `<Lake>` força densityBoost máximo na AABB do disco (centro ± radius × margem).
   - Futuro: `<DensityRegion>`, etc.

O density map vive no `TerrainEntityData` (ao lado do sampler) e é **lido pelo
`TerrainLodSelectSystem`** quando atribui resolução a cada chunk.

### 4.2 Resolução efetiva por chunk

```ts
// Pseudo: resolutionForLevel hoje retorna max(4, base >> level).
// Nova versão:
export function effectiveResolution(
  baseResolution: number,
  level: number,
  densityBoost: number   // 0..255 do tile(s) que cobrem o chunk
): number {
  const lodRes = Math.max(4, baseResolution >> level);
  if (densityBoost <= 0) return lodRes;
  // Capped multiplier: boost máx dobra a resolução (ou outro fator calibrável).
  const boostFactor = 1 + (densityBoost / 255);   // 1..2
  return Math.min(baseResolution, Math.max(lodRes, Math.round(lodRes * boostFactor)));
}
```

O `densityBoost` de um chunk = **máximo** sobre os tiles que ele intersetam (garante que um
chunk que toca um lago denso fica denso todo, evitando cracks internos ao chunk). Capado em
`baseResolution` (64) para não exceder a malha mais fina do LOD.

### 4.3 Cracks entre chunks de resolução diferente

Os vizinhos de um chunk podem ter resolução diferente (já acontecia por LOD de câmera;
agrava-se com densidade espacial). **Skirts já existem** (`chunk-geometry.ts:93-127`) e
cobrem isto — não há trabalho novo. Validação visual no browser confirma que não há
regressão.

### 4.4 Justificação vs alternativas

- vs **Patch tree de amostras** (rejeitada na revisão, §12): se a baseGrid já é full-res,
  os patches não adicionam detalhe (só re-amostram). E se a baseGrid fosse coarse, o carve
  ao mutar só a baseGrid produziria bacia coarse. **Nenhuma leitura entrega O-1.**
- vs **Carve directo em patches finos** (opção B do brainstorm): entrega detalhe + RAM
  sparse mas muda `carveBowl` de assinatura e é muito mais arriscado.
- **Resolução de malha variável** é o mais simples, carve inalterado, resolve O-1
  diretamente (a malha aprende a amostrar o heightfield fino onde há features).

## 5. Formato `.ahgt` (B)

A árvore de densidade é construída em runtime, logo o ficheiro guarda o heightfield
**uniforme** comprimido + metadados.

```
Header (16 bytes):
  magic: "AHGT" (4B)
  version: u16 = 1
  size: u16            # grid dim, ex.: 2048
  reserved: u16 = 0
  flags: u16           # bit0 = tem density hints inline

Metadata block (JSON inline, length-prefixed u32):
  worldSize: f32
  maxHeight: f32
  origin: [f32, f32]
  densityHints?: AABB[]   # opcional; default constrói do gradiente

Height data:
  uint16 array (size*size*2 bytes), quantizado [0, maxHeight]
  comprimido com deflate (fflate, já transitivo via three)
```

- **Quantização uint16:** 65536 níveis → ~3 mm sobre 200 m. Resolve escadas visíveis em
  lagos rasos (vs ~0,78 m do uint8 atual).
- **Magic + version** permitem evolução (spec A transporta árvore pré-computada via novo
  flag/version).

### 5.1 Retrocompatibilidade

- URL `.png` → `loadHeightmapFromUrl` (atual) carrega; baseGrid = PNG decodificado
  (convertido a Float32 internamente, sem ganho nem perda). **Comportamento = igual hoje.**
- URL `.ahgt` → novo loader, baseGrid uint16 → Float32.
- `terrain.json` ganha `heightmap_format: "png" | "ahgt"` (default `png`).
- Sintaxe `<Terrain url="...">` não muda.

## 6. Runtime (C)

Sem nova estrutura de altura. O `HeightSampler` (Float32Array full-res) **não muda**.
As mudanças estão no **consumidor** da resolução:

### 6.1 DensityMap (novo)

```ts
// terrain/density-map.ts (novo)
export interface DensityMap {
  tilesX: number;          // ex.: 64
  tilesZ: number;          // ex.: 64
  boost: Uint8Array;       // tilesX*tilesZ, 0..255
  worldSize: number;       // para converter world→tile
}

export function buildDensityMap(
  sampler: HeightSampler,
  tilesPerAxis = 64,
  opts?: { varianceWeight?: number; curvatureWeight?: number; threshold?: number }
): DensityMap;

export function applyOverride(density: DensityMap, aabb: { minX: number; minZ: number; maxX: number; maxZ: number }, boost: number): void;

export function boostAt(density: DensityMap, worldX: number, worldZ: number): number;

// Máximo boost sobre os tiles que um chunk AABB intersetem:
export function maxBoostOverAabb(density: DensityMap, aabb: { minX: number; minZ: number; maxX: number; maxZ: number }): number;
```

### 6.2 Resolução efetiva (modificação em lod-select.ts)

Nova função `effectiveResolution(base, level, densityBoost)` (ver §4.2). `lod-select.ts`
passa a exportá-la; `TerrainLodSelectSystem` lê-a.

### 6.3 LakeApplySystem (toque)

Antes de `carveBowl`, recolhe a AABB do disco do `<Lake>` e faz
`applyOverride(density, aabb, 255)` no density map do field. Isto garante que os chunks que
vão renderizar o lago ficam com resolução alta **antes** de a malha ser construída.
`carveBowl` em si **não muda**.

## 7. Integração com o Existente

### 7.1 `TerrainLodSelectSystem` (`systems.ts:706`)

Ponto de modificação: onde atribui `TerrainChunk.resolution[chunk]` (linha 816). Passa a:

```ts
const lodRes = resolutionForLevel(baseResolution, desc.level);
const chunkAabb = chunkAabbFromDesc(desc);   // origin ± size/2
const boost = data.density ? maxBoostOverAabb(data.density, chunkAabb) : 0;
const res = effectiveResolution(baseResolution, desc.level, boost);
TerrainChunk.resolution[chunk] = res;
```

### 7.2 Build de chunks

`buildChunkGeometry` (`chunk-geometry.ts`) e `buildChunkHeightfield` (`systems.ts:1003`)
**não mudam** — já consomem `TerrainChunk.resolution[chunk]` e `sampleHeightAt`. Herdam a
densidade extra automaticamente. Skirts (`chunk-geometry.ts:93-127`) continuam a cobrir
T-junctions.

### 7.3 `sampleTerrainHeight` (gameplay/spawner)

Não muda — continua multi-amostragem via `sampleHeightAt`. Beneficia automaticamente da
maior densidade de collider nos lagos.

### 7.4 Ficheiros (bounded units)

| Ficheiro | Responsabilidade | Estado |
|---|---|---|
| `terrain/density-map.ts` (novo) | `DensityMap`, `buildDensityMap`, `applyOverride`, `boostAt`, `maxBoostOverAabb` | Novo |
| `terrain/ahgt-format.ts` (novo) | Parser/serializer `.ahgt` (header + uint16 + deflate via fflate) | Novo |
| `terrain/lod-select.ts` (toque) | Adiciona `effectiveResolution(base, level, boost)` | Toque |
| `terrain/utils.ts` (toque) | `TerrainEntityData.density?: DensityMap` | Toque |
| `terrain/systems.ts` (toque) | Bootstrap constrói density map após heightmap; `TerrainLodSelectSystem` usa `effectiveResolution` | Toque médio |
| `water/systems.ts` (toque) | `LakeApplySystem` chama `applyOverride` antes do carve | Toque mínimo |
| `terrain/height-sampler.ts` | **Não muda** | — |
| `water/carve.ts` | **Não muda** | — |
| `terrain/chunk-geometry.ts` | **Não muda** (skirts já existem) | — |

## 8. Testes (Bun)

Padrão: `tests/unit/terrain/*.test.ts` (existem 6 ficheiros deste género).

**Casos críticos:**

- **`density-map.test.ts`** (novo): `buildDensityMap` dá boost alto em zona rugosa sintética,
  baixo em plana; `applyOverride` força máximo numa AABB; `maxBoostOverAabb` retorna o
  máximo correto sobre tiles intersectados; thresholds não regressam.
- **`effective-resolution.test.ts`** (novo, em `tests/unit/terrain/`): sem boost → igual a
  `resolutionForLevel`; boost=255 dobra (capado em baseResolution); boost intermédio escala.
- **`ahgt-format.test.ts`** (novo): round-trip uint16 sem perda; deflate round-trip;
  retrocompat lê PNG antigo (via `loadHeightmapFromUrl`, snapshot dos valores).
- **Regressão `terrain-height-sampler.test.ts`** (existe): continua a passar.
- **Regressão `carve.test.ts`** (existe em `tests/unit/water/`): continua a passar.

**Validação visual (manual, alinhado com preferência de iterar no browser):** depois de
implementado, correr `simple-rpg` no Chrome MCP e comparar a borda de um `<Lake>` antes/
depois.

## 9. Definição de Pronto

- `make test-vibegame` passa (testes novos + existentes não regressam).
- `simple-rpg` carrega com PNG antigo (retrocompat) sem mudanças de markup.
- Um `<Lake>` renderiza com borda de bacia mais nítida que antes (validação visual browser).
- `.ahgt` uint16 round-trip sem perda (teste automatizado).

## 10. Riscos e Mitigações

| Risco | Prob | Impacto | Mitigação |
|---|---|---|---|
| Cracks entre chunks de resolução diferente agrava-se | Média | Médio | Skirts já existem; validação visual no browser |
| Overdraw/excesso de segmentos perto de features | Média | Baixo | Cap em `baseResolution`; boost máx dobra apenas |
| Density map mal calibrada (over/under-refine) | Média | Baixo | Thresholds calibráveis; overrides declarativos como escape hatch |
| Regressão em `TerrainLodSelectSystem` (caminho crítico) | Média | Alto | Sem boost → comportamento idêntico ao de hoje; tests de regressão |
| Performance: `maxBoostOverAabb` no hot path de LOD select | Baixa | Médio | Tiles coarse (64×64), iteração sobre poucos tiles; cache por chunk key |

## 11. Follow-ups (fora do âmbito)

- **Spec A — Terrain3D generation-adaptive:** `.ahgt` (novo flag/version) transporta árvore
  pré-computada; runtime lê-a em vez de construir.
- **Sparse baseGrid:** poupar os 16 MB Float32 do heightfield (RAM runtime). Exige patch
  tree ou sparse storage — follow-up.
- **Ligar `collisionResolution`** (componente morto): collider com resolução independente
  do mesh LOD.
- **`<DensityRegion>`** declarativa para forçar densidade em cidades/estradas/etc.

## 12. Revisão (2026-07-03, antes do plano)

Durante o mapeamento do código para o plano, descobriu-se uma **contradição no design
original (patch tree de amostras)**:

> Se a baseGrid é full-res (2048²), os patches não adicionam detalhe (só re-amostram). Se a
> baseGrid fosse coarse, o carve ao mutar só a baseGrid produziria bacia coarse, e o
> re-sample dos patches dela não criaria detalhe fino. Nenhuma leitura entrega O-1.

**Causa-raiz real do sintoma:** o detalhe do lago **já existe** no heightfield (0,98 m/texel);
é a **malha de render** que o deita fora (15,6 m/segmento no LOD 5). Um lago de 6 m de raio
cabe em menos de 1 segmento.

**Decisão:** reenquadrar para "resolução de malha variável por região" — mais simples, carve
inalterado, resolve O-1 diretamente. baseGrid full-res Float32 (sem poupança de RAM nesta
fase; fica para sparse baseGrid / spec A). O utilizador aprovou o reenquadramento.

 Este documento reflete o design revisto. A versão anterior (patch tree) está obsoleta.
