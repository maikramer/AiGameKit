# Texturas — layout, fontes e licenças

Biblioteca de materiais tileable do pool partilhado, servida por URL
(`/assets/textures/…`) à web e à engine nativa.

## Layout — uma pasta por material

```
textures/<material>/
  albedo.webp      # difuso sRGB (WebP q92)
  normal.webp      # normal map OpenGL (WebP q95)
  ao.webp, height.webp, edge.webp, metallic.webp,
  roughness.webp, smoothness.webp   # consoante a origem (WebP q95)
  gen.json         # metadados de geração (só materiais AI)
```

Tudo a **1024×1024**. Normais em convenção **OpenGL** (a que Bevy e three.js
esperam); variantes DirectX (ambientCG `NOR_DX`) são descartadas no import.
WebP porque é o único formato comprimido que ambos os runtimes decodificam
nativamente (browsers + Bevy com a feature `webp`).

## Materiais

### AI — vramd texture2d + Materialize (regeneráveis; metadados de geração
em `gen.json`)

`asphalt`¹, `cobblestone_road`, `desert_sand`, `forest_floor`,
`mountain_stone`, `roof_tiles`, `snow_peak`, `swamp_mud`, `vale_grass`,
`wall_plaster`, `wood_planks`

¹ só albedo, sem PBR nem `gen.json`.

### Fotográficas — [ambientCG](https://ambientcg.com), **CC0 1.0** (domínio
público; usar, modificar e redistribuir livremente, sem atribuição)

| Pasta        | Asset ambientCG | Página                                  |
| ------------ | --------------- | --------------------------------------- |
| `dirt`       | Dirt01          | https://ambientcg.com/view?id=Dirt01    |
| `grass`      | Grass01         | https://ambientcg.com/view?id=Grass01   |
| `gravel`     | Gravel01        | https://ambientcg.com/view?id=Gravel01  |
| `sand`       | Sand01          | https://ambientcg.com/view?id=Sand01    |
| `dirt_road`  | Ground107       | https://ambientcg.com/view?id=Ground107 |
| `dirt_trail` | Ground086       | https://ambientcg.com/view?id=Ground086 |
| `pebbles`    | Ground021       | https://ambientcg.com/view?id=Ground021 |

Import (2026-09-02): original 2048² (Ground*_1K-JPG 1024²) reamostrado para
1024² (LANCZOS; normais renormalizadas) e re-codificado em WebP — os bytes
já não são os do zip original. Materiais AI: ver `gen.json` (prompt, seed,
data) de cada pasta.

`pebbles` (2026-09-04): leito de rio/lago — Ground021 1K importado já a 1024²
(albedo q92, normal GL/roughness/ao q95) + KTX2 UASTC via
`Viber/scripts/ktx2_compress_pool.py --loose`. Consumido pelo 13.º slot do
splatter de terreno (`pebbles` em `Viber/src/terrain/splat.rs`).
