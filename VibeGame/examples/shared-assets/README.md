# Shared Crystal Vale assets

Manifests canónicos dos packs **forest**, **village**, **infra**, **terrain** e
**props** (estilo Crystal Vale, `style_preset: painterly`) **e** os binários
finais (GLB/PNG/JSON, sem `_intermediate/`) em `public/assets/`. Esta pasta é a
fonte canónica: os exemplos recebem cópias via `sync.sh` e nunca regeneram os
packs partilhados. `examples/shared/` é só TypeScript (i18n, HUD); esta pasta é
só GameAssets.

## Happy path de regen (GPU)

O batch corre a partir **desta** pasta e escreve em `public/assets/` daqui:

```bash
cd VibeGame/examples/shared-assets
gameassets resume --profile game.yaml --manifest manifests/forest
gameassets resume --profile game.yaml --manifest manifests/village
gameassets resume --profile game.yaml --manifest manifests/infra
gameassets resume --profile game.yaml --manifest manifests/terrain
gameassets resume --profile game.yaml --manifest manifests/props
```

`resume` é idempotente: detecta por item o estado em disco (PNG/shape/paint) e
só gera o que falta — com os binários já presentes, o batch valida e faz skip.

Distribuir para o simple-racer (sem GPU):

```bash
bash VibeGame/examples/shared-assets/sync.sh
```

## Layout

```
shared-assets/
  README.md
  game.yaml                 # painterly, tom Crystal Vale (perfil dos packs)
  manifests/                # canónico; symlinks nos sample-gameassets/ dos exemplos
    forest.yaml             # árvores, cogumelos, cabana da bruxa
    village.yaml            # edifícios e props da aldeia / Discordia
    infra.yaml              # muralha, portão, pontes
    terrain.yaml            # formações rochosas genéricas
    props.yaml              # props genéricos (rock_mossy)
  public/assets/            # binários + _intermediate de resume (~2,6 GB; gitignored)
  sync.sh                   # rsync pool → simple-racer
```

O `_intermediate/` de cada pack (cache de geração) vive no pool: um
`gameassets resume` futuro salta shape/paint dos itens já completos.

## Como cada exemplo aponta para cá

**Manifests** (symlinks em `sample-gameassets/manifests/`):

- `simple-rpg/…/{forest,village,infra,terrain,props}.yaml`
- `simple-racer/…/{forest,village,infra,props}.yaml`

→ `../../../shared-assets/manifests/<file>.yaml`

**Binários**:

- **simple-rpg** aponta direto ao pool, sem cópias: `public/assets/meshes/{forest,village,infra,terrain}`, `images/{forest,village,infra,terrain}`, `sky/sky.png` e `meshes/props/rock_mossy_*` são symlinks → `../../../../shared-assets/public/assets/…`. (`meshes/vegetation` fica real — bpy carpet versionado.) Fresh clone: `bun run setup` instala a Release num staging e encaminha os packs shared para o pool em modo fill-if-missing — a Release **nunca** sobrescreve o pool (ele é canónico) — e (re)cria os symlinks.
- **simple-racer** versiona as próprias cópias (clone-friendly) e recebe
  atualizações do pool via `sync.sh`.

## O que o sync distribui

De `shared-assets/public/assets` para o **simple-racer**:

- `meshes/{forest,village,infra,vegetation}` + `images/{forest,village,infra}`
- `sky/sky.png` (IBL do vale)
- `rock_mossy_*` em `meshes/props` + `images/props/rock_mossy.png`

Sem `--delete`: os props de corrida versionados no racer ficam intactos.
Não distribui: characters, desert, swamp, interiors, armas, veículos, áudio —
identidade de cada jogo nos manifests próprios (`props-rpg.yaml`,
`vehicles.yaml`, …).

O pack `vegetation` (grama/flores, GLBs leves) é binário-only, sem manifest
GameAssets: cada exemplo tem a própria cópia (versionada no git).
