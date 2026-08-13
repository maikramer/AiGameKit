# Shared Crystal Vale assets

Manifests canónicos dos packs **forest**, **village** e **infra** (estilo
Crystal Vale, `style_preset: painterly`). Os GLBs gerados **não** vivem aqui —
cada exemplo tem os binários em `public/assets/`. `examples/shared/` é só
TypeScript (i18n, HUD); esta pasta é só GameAssets.

## Happy path de regen

`output_dir: ../../public/assets` nos YAML é relativo ao `cwd`
`sample-gameassets/` de **cada** exemplo. Por isso o comando corre sempre
dentro do exemplo, não desta pasta:

```bash
cd VibeGame/examples/simple-rpg/sample-gameassets
gameassets resume --profile game.yaml --manifest manifests/forest
gameassets resume --profile game.yaml --manifest manifests/village
gameassets resume --profile game.yaml --manifest manifests/infra
```

O racer usa os mesmos três symlinks. Depois de gerar no RPG, copiar os
binários para o racer (sem GPU):

```bash
bash VibeGame/examples/shared-assets/sync-from-rpg.sh
```

## Layout

```
shared-assets/
  README.md
  game.yaml                 # painterly, tom Crystal Vale (referência)
  manifests/forest.yaml     # canónico
  manifests/village.yaml
  manifests/infra.yaml
  sync-from-rpg.sh          # rsync packs RPG → simple-racer
```

Symlinks nos dois exemplos:

- `simple-rpg/sample-gameassets/manifests/{forest,village,infra}.yaml`
- `simple-racer/sample-gameassets/manifests/{forest,village,infra}.yaml`

→ `../../../shared-assets/manifests/<file>.yaml`

## O que o sync copia

De `simple-rpg/public/assets` para `simple-racer/public/assets`:

- `meshes/{forest,village,infra,vegetation}`
- `images/{forest,village,infra}`
- `sky/sky.png` (IBL do vale)
- `rock_mossy_*` em `meshes/props` (o racer usa a rocha do RPG à beira da pista)

Não copia: `_intermediate/`, characters, desert, swamp, interiors, áudio RPG.
O RPG **não** perde os binários — o script copia, não move.
