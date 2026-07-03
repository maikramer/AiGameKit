# Adaptive Heightmap — Format + Runtime Sampler (B + C)

**Data:** 2026-07-03
**Scope:** VibeGame (`VibeGame/src/plugins/terrain/`, `VibeGame/src/plugins/water/`)
**Estado:** Design aprovado, pronto para plano de implementação
**Specs relacionados:** Terrain3D generation-adaptive (spec A, *follow-up — fora do âmbito deste documento*)

---

## 1. Problema

O heightfield do VibeGame é um grid **uniforme**: `Float32Array` de dimensão igual à do PNG
(2048² no `simple-rpg`, ≈0,98 m/texel sobre 2000 m). Três dores resultam:

1. **Detalhe perdido em mutações finas.** O carve do `<Lake>` (`water/carve.ts`) opera no grid
   2048 fino, mas o downstream (mesh, collision, gameplay queries) re-amostra a bacia numa
   grade mais grossa (`resolutionForLevel(64, level)`), arredondando a borda fina da bacia.
2. **Memória desperdiçada em zonas planas.** Um planalto de 500 m/texel ocupa o mesmo que um
   canyon de 0,5 m/texel. Não há compressão espacial.
3. **Precisão de 8 bits insuficiente.** O PNG grayscale actual tem 256 níveis; sobre
   `maxHeight=200 m`, isso são passos de ~0,78 m — escadas visíveis em lagos rasos.

O utilizador quer **resolução variável por região**: alta densidade onde há detalhe (lagos,
canyons, costas) e baixa densidade em zonas planas, com compressão ponta-a-ponta
(disco → RAM → caminho para geração adaptive no Terrain3D).

## 2. Objetivos e Não-Objetivos

### Objetivos

- **O-1.** Reduzir memória em disco: heightfield adaptativo comprimido (`.ahgt`), com poucas
  amostras em zonas planas.
- **O-2.** Reduzir RAM em runtime: estrutura de amostragem que não materializa o grid fino
  onde não é preciso.
- **O-3.** Detalhe fino em lagos esculpidos: a borda da bacia de um `<Lake>` deve renderizar e
  colidir em alta resolução, sem arredondamento.
- **O-4.** Caminho aberto para spec A (Terrain3D generation-adaptive): o formato `.ahgt` deve
  poder transportar uma árvore pré-computada no futuro, sem redesenhar o runtime.
- **O-5.** Retrocompatibilidade total: mapas antigos (PNG 8-bit) continuam a funcionar sem
  mudança de markup.

### Não-objetivos (fora do âmbito)

- **N-1.** Geração adaptive no Terrain3D (difusão por região) — **spec A, follow-up**.
- **N-2.** Sistema completo de LOD dinâmico de runtime (mantém-se o LOD por câmera actual; a
  patch tree funde-se com ele mas não o substitui).
- **N-3.** Refactor arbitrário do plugin de terrain — só o necessário para integrar a patch
  tree.

## 3. Contexto do Código Actual

Fonte da verdade hoje: `HeightSampler` (`VibeGame/src/plugins/terrain/height-sampler.ts:13-24`):

```ts
export interface HeightSampler {
  width: number; height: number;
  data: Float32Array | null;  // [0,1] normalizado, row-major
  worldSize: number; maxHeight: number;
}
```

Três grids derivados, todos lidos do mesmo sampler:

| Grid | Resolução | Definido em |
|---|---|---|
| Heightfield lógico | = dim do PNG (2048²) | `loadHeightmapFromUrl` (`height-sampler.ts:109-166`) |
| Render mesh (por chunk) | `resolutionForLevel(64, level)` → 64…4 seg | `buildChunkGeometry` (`chunk-geometry.ts`), `lod-select.ts:traverse` |
| Collision (Rapier, por chunk) | = mesh (`(res+1)²` amostras) | `buildChunkHeightfield` (`systems.ts:983-1006`) |

Amostragem é **bilinear** em todos os casos (`sampleHeightAt`, `height-sampler.ts:202-212`).
`sampleTerrainHeight` (`height-sampler.ts:261-302`) sobe extra para o lattice do mesh via
`surfaceHeightAt`.

O carve do `<Lake>` (`water/carve.ts:carveBowl`) muta `sampler.data` diretamente com operação
`min` (só desce). O `LakeApplySystem` (`water/systems.ts:167-269`) repropaga derivados
(marca `meshDirty`, remove colliders por chunk, invalida BVH).

Componente `Terrain.collisionResolution` (`components.ts:20`) existe mas **não é lido** em
nenhum sistema — ponto morto.

## 4. Abordagem: Patch Tree (quadtree de patches com stitch)

Heightfield = quadtree balanceada. Cada leaf guarda um patch retangular `(res+1)×(res+1)`
com `res ∈ {4, 8, 16, 32, 64}` (5 níveis, 2× cada). Regiões planas = poucas leaves coarse;
regiões detalhadas = subdivididas até alta resolução.

**Porquê patch tree e não RLE ou TIN:**

- vs **RLE/sparse sobre uniforme** (Abordagem 2): RLE poupa disco mas não RAM runtime
  (descomprime para o mesmo `Float32Array`) nem dá mais densidade aos lagos. Falha O-2 e O-3.
- vs **TIN** (Abordagem 3): TIN é máxima compressão mas colide com o Rapier (quer grids),
  exige re-triangulação sob carve e não se funde com a quadtree de render existente
  (toda grid-based). Custo/benefício mau neste codebase.
- **Patch tree** é a única que poupa disco **e** RAM **e** deixa caminho aberto para spec A,
  **e** resolve o sintoma do lago, **e** se funde com a quadtree que já existe em
  `lod-select.ts`.

### 4.1 Topologia da árvore

- Quadtree balanceada em XY sobre o plano do terreno.
- Profundidade máxima `D_max = 6` (alinhado com `levels=6` de `lod-select.ts`).
- Leaf mínima (profundidade 6 sobre 2000 m) ≈ 31 m; a `res=64` → ~0,5 m/texel
  (mais fino que o 0,98 m/texel actual).
- Leaf de profundidade 0 sobre 2000 m com `res=4` → ~500 m/texel (essencialmente plano).

### 4.2 Resolução de cracks: skirts

- **Render:** skirts verticais em cada patch (vértices extra que caem abaixo da borda)
  escondem geometricamente o crack entre níveis. Robusto a LOD dinâmico e a mutações
  (carve), não requer re-welding quando um vizinho subdivide. Técnica standard (Cesium,
  Google Earth).
- **Collision:** ignora cracks — os heightfields Rapier são por-chunk independentes;
  pequenas discontinuidades físicas na fronteira são aceitáveis e já existem hoje.

### 4.3 Atribuição de densidade

**Árvore construída em runtime** na carga do terreno (não no export Terrain3D), a partir de:

1. **Densidade-map automática** — para cada tile de uma grelha grosseira (ex.: 64×64 tiles),
   score = combinação de (a) gradiente/variância local das alturas, (b) curvatura,
   (c) proximidade a água marcada. Tiles high-score → subdividir; low-score → manter coarse.
   Thresholds calibráveis.
2. **Overrides declarativos** — `terrain.json` e/ou XML do VibeGame marcam AABBs como
   "important" (lagos via `<Lake>`, cidades, etc.). Um `<Lake>` força a árvore a subdividir
   até ao nível máximo dentro do disco do lago, **antes** do carve acontecer.

**Regra de balanceamento:** restricted quadtree 2-to-1 — um patch pode estar no máximo
1 nível mais fino que o vizinho. Aplicada como post-pass depois de atribuir densidades:
se um tile subdivide, força os 4 vizinhos a pelo menos nível N-1. Reduz complexidade do
stitching (skirt só cobre 1 nível de gap).

**Justificação runtime vs offline:** runtime permite overrides declarativos (`<Lake>`
adicionado no XML), não bloqueia no spec A (Terrain3D), e a construção para 2048² é da
ordem de dezenas de ms — aceitável.

## 5. Formato `.ahgt` (B)

A árvore é reconstruída em runtime, logo o ficheiro guarda o heightfield **uniforme**
comprimido + metadados.

```
Header (16 bytes):
  magic: "AHGT" (4B)
  version: u16 = 1
  size: u16            # grid dim, ex.: 2048
  maxLevels: u16 = 6
  flags: u16           # bit0 = tem density hints, bit1 = reservado

Metadata block (JSON inline, length-prefixed):
  worldSize: f32       # metros X/Z
  maxHeight: f32       # metros
  origin: [f32, f32]   # offset world (default [0,0])
  densityHints?: AABB[] + nivel min   # opcional

Height data:
  uint16 array (size*size*2 bytes), quantizado [0, maxHeight]
  comprimido com deflate (ou zstd se disponível no runtime)
```

- **Quantização uint16:** 65536 níveis → ~3 mm sobre 200 m. Resolve o defeito das escadas
  visíveis em lagos rasos (vs ~0,78 m do uint8 actual). ~2× o tamanho do uint8 mas
  altamente compressível (terreno tem baixa entropia).
- **Magic + version** permitem evolução futura (ex.: transportar árvore pré-computada no
  spec A via um novo flag/version).

### 5.1 Retrocompatibilidade

- Se o URL aponta para `.png` → `loadHeightmapFromUrl` (actual) carrega como hoje; envolve
  num `AdaptiveHeightSampler` cuja baseGrid é o PNG decodificado (convertido para uint16
  interno, sem ganho de precisão mas sem perda), árvore = trivial (1 leaf coarse).
  **Comportamento = igual ao de hoje.**
- Se o URL aponta para `.ahgt` → carrega o novo formato, constrói árvore.
- `terrain.json` ganha campo opcional `heightmap_format: "png" | "ahgt"` (default `png`).
- Sintaxe `<Terrain url="...">` não muda.

## 6. Runtime Sampler (C)

### 6.1 Estrutura

```ts
export interface AdaptiveHeightSampler {
  // Fonte uniforme (O(1) por texel):
  baseGrid: Uint16Array;     // size×size, quantizado [0, maxHeight]
  size: number;
  worldSize: number;
  maxHeight: number;

  // Árvore de densidade (construída na carga):
  tree: PatchTree;           // quadtree de leaves; cada leaf = {aabb, resolution, patch?}

  // Overrides declarativos (recolhidos antes da construção):
  densityOverrides: DensityHint[];
}
```

### 6.2 Amostragem

`sampleHeightAt(x, z)`:
1. Desce a quadtree até à leaf que contém (x, z) — O(log D) ≈ 6 hops.
2. Se a leaf tem patch próprio (densa): interpola bilinear no patch.
3. Se a leaf é "virtual" (só uma região coarse da baseGrid): amostra bilinear da baseGrid.

**Equivalência de interface:** o `HeightSampler` actual é consumido em N sítios
(`carveBowl`, `buildChunkGeometry`, `buildChunkHeightfield`, `sampleTerrainHeight`,
`getTerrainHeightAt`). O `AdaptiveHeightSampler` **expõe a mesma interface** — consumidores
não mudam. A `baseGrid` continua acessível para quem precisar (carve usa-a quando muta).

### 6.3 Mutação: carve do lago + patch tree

Sequência (resolve o sintoma O-3):

1. Na carga: heightmap + overrides `<Lake>` → construir árvore (lago já denso).
2. `LakeApplySystem` corre `carveBowl`:
   - **(baseGrid)** para cada texel na AABB do disco: `baseGrid[i] = min(baseGrid[i], bowlY)`.
     Igual ao hoje, O(1) por texel.
   - **(invalidação)** para cada leaf que intersectar o disco: `leaf.dirty = true`.
   - **(lazy)** no próximo sample/render da leaf dirty: re-amostra patch da baseGrid mutada.
3. Render/collision/gameplay veem a bacia em alta resolução porque a leaf do lago estava
   subdividida ao nível máximo (passo 1).

**Caso dinâmico** (`<Lake>` adicionado depois da carga, scripting sandbox): `carveBowl`
adiciona um passo 0 — `forceRefine(diskAABB, maxLevel)` — que subdivide as leaves no disco
*antes* de mutar. Barato (O(log D)) e mantém o contrato.

## 7. Integração com o Existente

### 7.1 Render mesh — fundir com `lod-select.ts`

A quadtree de LOD actual torna-se a **mesma** quadtree da patch tree. Um leaf é selecionado
para render quando: (i) está dentro do range da câmera (regra actual) **e** (ii) a sua
resolução de densidade ≥ resolução de LOD pedida.

- Densidade local alta (leaf fina): LOD-render usa-a diretamente — detalhe nativo.
- Densidade local baixa (leaf coarse) mas câmera perto: LOD-render **upsample** a leaf coarse
  para a resolução pedida (interpola). Sem detalhe extra, mas sem crack.

A densidade espacial e a densidade de câmera combinam-se naturalmente.

### 7.2 Collision (Rapier) — por-chunk, como hoje

`buildChunkHeightfield` não muda quase nada: `sampleHeightAt` agora desce a quadtree
internamente, mas o output é o mesmo `(res+1)²` grid para o Rapier. Opcional: em leaves
densas coincidentes com o chunk, amostrar diretamente do patch (pulo de interpolação).

### 7.3 `sampleTerrainHeight` (gameplay/spawner)

Não muda — continua multi-amostragem via `sampleHeightAt`, agora adaptativo por baixo.
Spawners beneficiam automaticamente da maior densidade nos lagos.

### 7.4 Ficheiros (bounded units)

| Ficheiro | Responsabilidade | Estado |
|---|---|---|
| `terrain/adaptive-sampler.ts` (novo) | `AdaptiveHeightSampler`, `sampleHeightAt` adaptativo | Novo |
| `terrain/patch-tree.ts` (novo) | Quadtree de densidade, construção, balanceamento 2-to-1, query de leaf, `forceRefine` | Novo |
| `terrain/ahgt-format.ts` (novo) | Parser/serializer `.ahgt` (header + uint16 + deflate) | Novo |
| `terrain/density-source.ts` (novo) | Score densidade (gradiente/curvatura), merge overrides declarativos | Novo |
| `terrain/height-sampler.ts` (refactor) | Mantém interface; delega para `AdaptiveHeightSampler` | Refactor leve |
| `terrain/chunk-geometry.ts` (refactor) | Geração de skirts; consome `sampleHeightAt` adaptativo | Refactor médio |
| `terrain/systems.ts` (toques) | Bootstrap lê `.ahgt` vs `.png`; `LakeApplySystem` chama `forceRefine` antes do carve | Toques |
| `water/carve.ts` (toque) | `forceRefine(diskAABB)` no início do `carveBowl`; resto igual | Toque mínimo |

## 8. Testes (Bun)

```
VibeGame/src/plugins/terrain/
  patch-tree.test.ts         # novo
  adaptive-sampler.test.ts   # novo
  ahgt-format.test.ts        # novo
  density-source.test.ts     # novo
```

**Casos críticos:**

- **`patch-tree`**: construção de density-map sintético; regra 2-to-1 (vizinhos ≤1 nível de
  diferença); `forceRefine(AABB)` subdivide e re-balanceia; query de leaf por ponto em
  cantos/bordas.
- **`adaptive-sampler`**: equivalência com bilinear atual para árvore trivial (snapshot dos
  valores atuais); amostragem em patch denso retorna altura correta; continuidade na
  fronteira de níveis (sem salto > tolerância); mutação carve invalida e re-amostra.
- **`ahgt-format`**: round-trip uint16 sem perda; deflate round-trip; retrocompat PNG 8-bit.
- **`density-source`**: score alto em zona rugosa, baixo em plana; merge override `<Lake>`
  força nível máximo; thresholds não regressam.

**Validação visual (manual, alinhado com preferência de iterar no browser):** depois de
implementado, correr `simple-rpg` no Chrome MCP e comparar a borda de um `<Lake>` antes/depois.

## 9. Definição de Pronto

- `make test-vibegame` passa (testes novos + existentes não regressam).
- `simple-rpg` carrega com PNG antigo (retrocompat) sem mudanças de markup.
- Um `<Lake>` renderiza com borda de bacia mais nítida que antes (validação visual browser).
- Memória: baseGrid uint16 de 2048² ≈ 8 MB (vs 16 MB Float32 atual) — poupança ~50% em RAM só
  com a migração de formato, antes mesmo da patch tree comprimir.

## 10. Riscos e Mitigações

| Risco | Prob | Impacto | Mitigação |
|---|---|---|---|
| Bugs de stitching (cracks, normais erradas na fronteira) | Alta | Médio | Skirts (robustez); testes de continuidade; validação visual no browser |
| Performance de descida da quadtree em hot path (cada sample) | Média | Médio | O(log D) ≈ 6 hops; cache da leaf no chunk; benchmarks |
| Regressão em consumidores do `HeightSampler` antigo | Média | Alto | Interface idêntica; snapshot test de equivalência |
| Densidade-map automática mal calibrada (over/under-refine) | Média | Baixo | Thresholds calibráveis; overrides declarativos como escape hatch |
| `collisionResolution` morto — ligá-lo adiciona complexidade extra | Baixa | Baixo | Não obrigatório neste spec; follow-up opcional |

## 11. Follow-ups (fora do âmbito)

- **Spec A — Terrain3D generation-adaptive:** o `.ahgt` (com novo flag/version) transporta
  árvore pré-computada; o runtime lê-a em vez de construir. Reduz trabalho de construção e
  permite difusão por região no Terrain3D.
- **Ligar `collisionResolution`:** heightfield de colisão com resolução independente do
  mesh LOD (componente já existe, está morto).
- **LOD dinâmico completo:** subsituir o LOD por câmera por LOD por densidade+erro screen.
  Fora do âmbito; a patch tree funde-se com o LOD actual mas não o substitui.
