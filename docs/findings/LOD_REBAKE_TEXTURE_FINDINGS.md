# LOD rebake — textura despedaçada (village, ago/2026)

**Sintoma:** LODs da village (`examples/shared-assets/public/assets/meshes/village/`)
com texturas quebradas (~40-50% da superfície com cor errada) enquanto o
`*_painted.glb` está correto. Reproduzível a 7 ago **e** com o código atual —
não eram artefactos velhos, eram 2 bugs vivos na rota de rebake do
`text3d lod` (`Text3D/src/text3d/utils/mesh_remesh_textured.py`).

## Bug 1 — epsilon absoluto no closest-point (distâncias garbage)

`_closest_point_kdtree` usava `|denom| < 1e-6 → 1.0` para proteger divisões.
Em malhas métricas com faces milimétricas, um triângulo de ~5 mm tem
`d00·d11 ≈ 1e-10` — muito abaixo do limiar — e o denominador de TODAS as
candidatas pequenas colapsava para 1.0. As distâncias saíam garbage e o
argmin escolhia faces a centímetros com UVs de outra região do atlas.

**Fix:** epsilon relativo — `|denom| < 1e-9 · max(d00·d11) → 1.0` (idem na
fase 4 do `_transfer_texture_direct`). Round-trip da fonte consigo própria:
~15% de amostras erradas → **0.3%**.

## Bug 2 — clip de baricêntricas sem renormalizar (UV extrapolada)

Fase 4 do `_transfer_texture_direct`: quando o closest cai fora da face
escolhida (~27% das queries em meshes decimadas), a bary crua sai do simplexo
(ex.: `(0.91, 0.99, -0.90)`). O código fazia `clip(0,1)` por componente mas
**não renormalizava** — os pesos somavam >1 e a `interp_uv` extrapolava para
fora do triângulo UV (caso medido: UV a 3× fora), amostrando texels não
pintados do atlas → speckles pretos/errados por todo o LOD.

**Fix:** renormalizar por `s_bary_u+s_bary_v+s_bary_w` após o clip.
Sintético ground-truth (esfera, cor = função contínua do mundo):
44.6% → **5.2%** de amostras erradas.

**Nota:** todos os testes flip-invariantes (fonte vs ela própria) escondem o
bug 2 — só um ground truth contínuo no mundo (ou o texture-check em GLBs
finais) o denuncia.

## Gate V/Tri do meshopt: 1.35 → 1.39

O meshopt (atlas preservado, textura exacta por construção) atingia o alvo
do LOD0 no market_stall (V/Tri 1.36) mas era rejeitado pelo tecto 1.35 —
mandava um resultado perfeito para um rebake com perdas. 1.39 aceita meshes
tipo market_stall e mantém o invariante `cap × 1.15 (pior inflação do
re-export) < 1.6` das regras LOD do GameAssets
(`tests/test_lod_textured_routes.py::test_v_per_tri_cap_leaves_headroom_for_export_inflation`).

Também: `_closest_point_kdtree` recebe `query_normals` (normais das faces
decimadas) e pontua `dist² + (1-dot)·penalty` — atravessar gaps finos
(toldo↔balcão) deixa de pintar UVs da superfície vizinha. Gate duro só para
normais opostas (casca do lado de lá).

## Resultado (market_stall, `aigamekit-lab debug texture-check` vs painted)

| nível | antes | depois | rota |
|-------|-------|--------|------|
| lod0  | FAIL 40.2% mau | **PASS 3.1%** | meshopt (atlas preservado) |
| lod1  | FAIL 45.5% mau | **PASS 17.4%** | rebake (fixes) |
| lod2  | FAIL 49.1% mau | **PASS 24.8%** | rebake (fixes) |

## Ferramenta — `aigamekit-lab debug texture-check REF CANDIDATO`

Amostra a superfície da referência, lê o basecolor no atlas próprio, e
**projeta cada ponto na malha do candidato** (BVHTree `find_nearest`,
normal-gated ≤60°) avaliando a textura do candidato no ponto projetado.
Comparação por posição ⇒ re-bake/atlas novo não gera falsos positivos.
Exit 1 quando a fração de amostras acima da tolerância excede
`--fail-above` (default 0.25). Ruído do caso «igual a si próprio» ≈ 0
(mediana 0.0); um LOD via meshopt mede 3%; LODs rebakeados 15-25%.

Calibração: `--samples 4000` em props de ~100k faces demora segundos;
`matched_ratio` < 0.8 indica transformes/origem incompatíveis entre os GLBs.

Regenerar a village:

```bash
cd Viber/examples/shared-assets
# apagar *_lod{0,1,2}.glb força a stage de LOD (resume salta ladders "ok")
gameassets resume --profile game.yaml --manifest manifests/village
# os exemplos leem o pool diretamente (plugin vibegame({ sharedAssets })) — sem passo de cópia
```
