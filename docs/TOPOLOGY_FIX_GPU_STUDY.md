# Estudo — Acelerar `text3d topology-fix` (GPU / vetorização)

Data: 2026-07-20 · Caso de estudo: `village_longhouse_shape.glb` (Stage 2 da master pipeline)

## Resumo executivo

O `topology-fix` num mesh grande (7.03M verts / 2.35M tris, 197 MB) é lento não
por falta de GPU, mas porque a implementação atual assenta em **loops Python
por elemento**, **round-trips bmesh repetidos** (cada operação reconstrói o
bmesh do zero) e **uma ordem de operações que obriga a trabalhar o mesh cru de
7M vértices** (normal-split do glTF). Medido no hardware alvo (RTX 4050 6 GB,
16 cores):

- O ganho principal é **vetorização numpy/scipy em CPU (10–100× por op)** — sem
  dependências novas (scipy já é dep do Text3D).
- A **GPU (torch já instalado) dá mais 2–4×** nas mesmas ops e ~2000× no Taubin
  — é um acelerador opcional, não o ganho principal.
- **Bibliotecas novas** (warp-lang, manifold3d) só se justificam para ray-cast
  com semântica exata em ms e watertight por booleans — Fase 3 opcional.

Estimativa: pipeline reordenado + fase vetorizada reduz o stage neste mesh de
**~3–5 min para ~40–70 s** em CPU pura, com a GPU a cortar mais alguns segundos
nas fases de arrays.

## 1. O caso concreto

`village_longhouse` (category=building, `--size-m 10,5,6`, octree 448):

| Propriedade | Valor |
|---|---|
| GLB `_shape` | 197 MB |
| Vértices | 7 028 299 (V/Tri = 3.0 → **normais split por loop**) |
| Triângulos | 2 352 714 |
| Extents | 8.9 × 10 × 8.3 (já em metros, Y-up 0–10) |
| Área de superfície | 423 m², aresta mediana 2.1 cm |
| Vértices reais pós-weld | **1 177 254** (o weld remove 5.85M de cópias split!) |

O facto de o GLB vir com normais split (V/Tri=3) significa que **todas as ops
topológicas correm sobre 6× mais vértices do que o mesh real** até ao primeiro
weld — e que qualquer op que dependa de conectividade (solidify, union-find,
VDB volume) vê 2.35M de triângulos **desconectados** antes do weld.

## 2. Onde o tempo vai (medido, não estimado)

Probe no GLB real (`/tmp/topo_probe.py`, resultados em `/tmp/topo_probe.json`):

| Operação (estado atual) | Tempo | Notas |
|---|---|---|
| Import GLB (bpy) | 10.1 s | I/O + decode |
| bmesh `from_mesh` / `to_mesh` | 1.2 s / 0.25 s | **~15–20× por pipeline** |
| `remove_doubles(1e-5)` (7M verts) | **19.0 s** | bmesh; removeu 5.85M verts |
| `remove_doubles(0.003)` (pós-weld) | 3.7 s | +76k verts |
| `count_boundary_edges` | **1.2 s por chamada** | reconstrói bmesh; chamada ~10–15× no `make_watertight` |
| BVH build | 0.27 s | barato |
| `ray_cast` (mathutils) | 207k raios/s | internal shells: **~21–25 s** (2 passes × 2.35M faces) |
| Loop Python slivers (2.35M faces) | **~14.4 s** | `calc_area`/`calc_length` por face |
| Ciclo EDIT mode (enter/select/fill/exit) | ~2.8 s | ~6–10 ciclos por pipeline |
| `fill_holes(sides=12)` | 1.5 s | op em si barata |
| Morph-close (ordem de produção, raw split) | **~35 s** | ver §4 |

Total estimado do stage neste mesh: **~3–5 min** (com morph-close) — CPU 100%
single-thread, GPU 0%.

## 3. Benchmark: as mesmas ops vetorizadas (`/tmp/topo_gpu_bench.py`)

Sobre os **mesmos arrays** (dump `/tmp/longhouse_arrays.npz`):

| Operação | Atual (bmesh/Python) | numpy CPU | torch CUDA (4050) | Speedup CPU→GPU |
|---|---|---|---|---|
| Weld 7M verts (grelha 1e-5) | 19.0 s | **0.53 s** | **0.49 s** | 36–39× |
| Slivers (2.35M faces) | ~14.4 s | **0.30 s** | **0.12 s** | 48–120× |
| Boundary edges | 1.2 s/chamada | **0.39 s** | **0.09 s** | 3–13× |
| Debris (componentes conexos) | ~10–20 s (union-find Python) | **0.19 s** (scipy CSR) | — | ~50–100× |
| Taubin 3 iters (7M edges) | ~162 s (loop Python) | — | **0.074 s** | **~2200×** |
| Internal shells (2 passes) | ~21–25 s (BVH ray/face) | **0.75 s** (cKDTree build+query) | 0.04 s (grid hash) | ~30× |

Notas de fidelidade:

- Weld por grelha: removeu 5 850 899 vs 5 851 045 do bmesh (Δ=146 verts,
  efeito de fronteira de célula). Mitigação: verificar as 27 células vizinhas
  ou pós-pass exato só nos pares candidatos — custo marginal.
- Internal shells por cKDTree precisa dos filtros certos (candidato dentro de
  `wall_gap`, alinhado com ±N da face, normal oposta) — todos vetorizáveis; o
  número prova que a parte espacial (a mais cara) fica sub-segundo.
- VRAM necessária p/ GPU: < 500 MB para 7M verts — cabe em qualquer GPU alvo
  sem coordenação UMS (é compute de segundos, não pesos de modelo).

## 4. Achados estruturais (além da velocidade)

### 4.1 Morph-close corre sobre o mesh errado (ordem)

Em produção, `morphological_close` corre **antes** de qualquer weld, sobre o
mesh normal-split. Medido em ordem de produção (`/tmp/morph_probe.py`):

```
solidify: 2.4s → 11 734 064 faces   (!) 2.35M tris → prismas por-triângulo
voxel1:  24.0s → 2 338 472 faces    VDB volta a fundir espacialmente
voxel2:   5.0s → 1 899 070 faces
voxel3:   3.4s →   959 168 faces
TOTAL:   34.9s
```

- O **SOLIDIFY em mesh split cria paredes por triângulo individual**
  (2.35M → 11.7M faces) — trabalho desperdiçado que o VDB depois desfaz.
- O VDB `volumeFromMesh` precisa de mesh fechado para detetar volume; no mesh
  split não há "dentro/fora" → o remesh **preserva as double shells** (959k
  faces). Num probe pós-weld+fill, o mesmo REMESH deu **28 300 faces** (casco
  exterior limpo) — ou seja, **a ordem muda completamente o resultado**, não só
  o custo. Correr morph-close depois do weld+fill é simultaneamente mais
  rápido (remesh ~3–10 s em mesh de 1.2M verts) e mais correto.

### 4.2 Voxel floor vs. distância de fecho

Para o longhouse: morph auto = 0.125 × 10/448 ≈ **2.8 mm**, mas o piso de
grelha `max_dim/800` força voxel de **12.5 mm** — maior que a parede solidificada
(2×2.8=5.6 mm). O morph-close vira na prática um "voxel remesh a 1.25 cm" que
pode apagar telhas/vigas < ~2 cm (detalhe do octree 448) e perfurar paredes
finas. O piso devia ser função da distância (ex. `vox ≤ distance/2` com cap de
grelha mais alto, ex. 2000) — ou migrar para OpenVDB direto (§5, Fase 3).

### 4.3 Três welds consecutivos

`reweld_coincident(1e-6)` → `remove_doubles(1e-5)` → `remove_doubles(0.003)`:
~42 s de bmesh no mesh cru. Um único weld vetorizado no limiar final cobre os
três (0.5 s); manter thresholds separados só onde o perfil o exige.

### 4.4 `count_boundary_edges` reconstrói bmesh por chamada

~1.2 s × 10–15 chamadas dentro do `make_watertight` (ciclos pinch/erode/fill).
Boundary a partir de arrays (unique de arestas ordenadas) custa 0.09–0.39 s —
ou simplesmente reutilizar o mesmo bmesh entre iterações.

## 5. Proposta por fases

### Fase 1 — Fase de arrays vetorizada (CPU, sem deps novas) ⭐ recomendado

Novo módulo em `aigamekit_shared` (ex. `mesh_repair_arrays.py`) que executa todos
os passos de **filtro/seleção** sobre numpy arrays `(co, tris)` antes de tocar
em bmesh:

```
load GLB → arrays (trimesh process=False; bpy só se houver armature)
sanitize_nonfinite          numpy              (≈0 s)
weld ×3 → 1 weld            np.unique grelha   (0.5 s   vs ~42 s)
long edges / slivers        vetorizado         (0.3 s   vs ~29 s)
debris                      scipy.sparse.csgraph.connected_components (0.2 s vs ~15 s)
internal shells (building)  cKDTree + filtros  (≈1–2 s  vs ~21–25 s)
boundary edges              np.unique counts   (0.4 s   vs 1.2 s/chamada)
```

Os passos genuinamente topológicos ficam em bmesh, **uma única vez**, sobre o
mesh já reduzido (1.2M verts reais): `fill_holes`, `holes_fill` por loop,
`triangulate`, `normals_make_consistent`, pinch/erode (loops pequenos — custo
dominado por arestas de fronteira, que são poucas). Export idem.

Ganhos esperados neste mesh: **~3–5 min → ~60–90 s** (CPU pura). Risco baixo:
mesma semântica, mesmos guards (max_removal_ratio etc.), testes existentes em
`Shared/tests` continuam válidos com adaptação mínima (comparar contagens, não
ordens de vértices).

### Fase 2 — Acelerador GPU opcional (torch já presente)

As mesmas kernels em torch CUDA quando `torch.cuda.is_available()`
(detetar via `aigamekit_shared.gpu`; override `AIGAMEKIT_TOPOFIX_DEVICE=cuda|cpu`):

- weld / boundary / slivers: mais 2–4× sobre numpy (já medido).
- Taubin / smooth: ~2000× sobre o loop Python atual (0.074 s) — viabiliza
  ligar `do_taubin` em meshes grandes sem custo relevante.
- VRAM < 500 MB; duração de segundos → **não precisa de admissão UMS** (não são
  pesos de modelo); manter fora do fluxo UMS mas documentar o env var.

### Fase 3 — Bibliotecas dedicadas (só se a Fase 1–2 não chegar)

| Biblioteca | Estado (py3.13) | Para quê |
|---|---|---|
| `warp-lang` 1.15 | ✅ wheel | BVH GPU com ray-cast exato (ms) — `remove_internal_shell_faces` e `_vertex_gap_weights` com semântica de raio rigorosa; interop zero-copy com torch |
| `manifold3d` 3.5.2 | ✅ wheel | Booleans/LevelSet C++ paralelo — alternativa ao `make_watertight` inteiro e ao morph-close (LevelSet dilate/erode nativo) |
| `pyopenvdb` | ❌ sem wheel (build de fonte) | Level-set morphology direta (1 voxelização + morphology na grelha + 1 meshing, em vez de 3 REMESH + 2 DISPLACE + SOLIDIFY + DECIMATE) |
| `pyembree` 0.1.12 | ⚠️ precisa `libembree` do sistema | Ray-cast CPU SIMD/multi-thread (5–10× sobre mathutils BVH) sem GPU |
| `torchmcubes` | ❌ sem wheel recente | Marching cubes GPU (só se se reimplementar remesh) |

### Mudanças de ordem (independentes de fase)

1. **Weld (1e-5) antes do morph-close** — corrige o solidify por-triângulo,
   reduz o VDB a mesh de 1.2M verts e torna o volume detetável (§4.1).
2. **Boundary count por arrays** dentro do `make_watertight` (§4.4).
3. Rever `voxel_floor` do morph-close (`max_dim/800` ≫ distance — §4.2).

## 6. Estimativa final (este mesh)

| Cenário | Tempo Stage 2 | GPU |
|---|---|---|
| Atual (medido por partes) | ~3–5 min | 0% |
| Fase 1 (arrays CPU + reordenação) | ~60–90 s | 0% |
| Fase 1+2 (GPU nas fases de arrays) | ~45–70 s | picos <500 MB |
| + Fase 3 (warp rays / manifold) | ~30–50 s | idem |

(I/O + export ≈ 20–30 s são o piso irredutível sem mudar formato/intermediários.)

## 7. Reproduzir

```bash
# Probe de custos no GLB real (bpy)
Text3D/.venv/bin/python /tmp/topo_probe.py      # → /tmp/topo_probe.json
# Benchmark vetorizado/GPU sobre os mesmos arrays
Text3D/.venv/bin/python /tmp/topo_gpu_bench.py  # → /tmp/topo_gpu_bench.json
# Morph-close em ordem de produção
Text3D/.venv/bin/python /tmp/morph_probe.py
```

Datasets: `VibeGame/examples/simple-rpg/public/assets/meshes/_intermediate/village_longhouse_shape.glb`
(7.03M verts / 2.35M tris), arrays em `/tmp/longhouse_arrays.npz`.

---

## 8. Implementação (2026-07-20) — Fase 1+2 feita, sem GPU

O estudo foi implementado **sem precisar de GPU**: o gargalo era CPU single-thread
e cedeu a vetorização numpy/scipy + reordenação + bibliotecas C++ já presentes.

### O que mudou

| Mudança | Onde | Efeito |
|---|---|---|
| Fase vetorizada (weld exacto cKDTree+CC, slivers, long edges, debris scipy CSR, shells k-NN+Möller–Trumbore, boundary por unique, Taubin bincount) | `Shared/src/aigamekit_shared/mesh_repair_arrays.py` (novo) | filtros 10-100x mais rápidos |
| `topology-fix --engine auto\|arrays\|bpy` (auto = arrays quando mesh sem UVs/weights/shape-keys/armature) | `Text3D/src/text3d/cli.py`, `Text3D/src/text3d/utils/mesh_lod.py` | adopção transparente no batch |
| Weld **antes** do morph-close (ordem nova no engine arrays) | `mesh_lod._repair_topology_arrays_phase` | solidify deixa de criar paredes por-triângulo (11.7M faces inúteis); VDB passa a ver volume |
| `count_boundary_edges_fast` (foreach_get + np.unique) nos loops do `make_watertight` | `mesh_repair.py` | boundary ~2x por chamada |
| Morph clamped: `dist_eff`/`wall` sobem para a escala do voxel + **cadeia reduzida** (1 remesh em vez de 3 + 2 displaces — dilate/erode sub-voxel era no-op) | `mesh_repair.morphological_close` | morph ~3x mais rápido em assets grandes |
| **Decimate-back via `fast_simplification`** (quadric C++; já era dep do Text3D) com fallback DECIMATE | `mesh_repair._decimate_back` + `mesh_repair_arrays.simplify_faces_arrays` | **63.7s → 4.4s** (2M → 198k faces) |

### Resultados medidos (pipeline completa, CLI)

| Mesh | Legacy (`--engine bpy`) | Novo (`--engine auto`) | Speedup | Qualidade |
|---|---|---|---|---|
| `dead_bush_shape` (399k tris, vegetation) | 89 s | **26.8 s** | 3.3x | SSIM 0.997 vs legacy; boundary=0 |
| `village_longhouse_shape` (2.35M tris, building) | >16 min (**não terminou**) | **3m21s** | >4.8x | SSIM 0.999 vs run arrays anterior; boundary=0 |

Breakdown do novo engine no longhouse: import 12s + arrays 38s + morph ~150s
(solidify+voxel+decimate rápido) + topo 7s + export 2.4s. O morph-close é agora
o custo dominante mas **produtivo** (sela as 2402 rachas; sem ele o
`make_watertight` demora mais e deixa boundary residual — medido: 5m19s e
boundary=10 no longhouse).

### Lições / notas

- A GPU (torch) daria mais 2-4x nos filtros, mas estes passaram a ser segundos
  — **não se justifica** (Fase 2 do plano original arquivada; warp-lang/manifold3d
  ficam como opção futura só para ray-exacto/booleans).
- `fast_simplification` estava declarado no Text3D mas sem uso — agora usado
  via lazy import com fallback (`simplify_faces_arrays`).
- Legacy preservado: `--engine bpy` corre o caminho antigo byte-a-byte
  (morph primeiro), para meshes com UVs/weights/armature e para A/B.
- Testes: `Shared/tests/test_mesh_repair_arrays.py` (35 testes; paridade weld/
  boundary com bmesh; guards; simplify com e sem lib).
