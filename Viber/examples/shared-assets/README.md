# Shared asset pool

**Uma** pasta com todos os assets gerados do repo — manifests canónicos e os
binários finais (GLB/PNG/JSON). Nenhum exemplo guarda cópias nem symlinks: o
plugin `vibegame({ sharedAssets })` serve `public/assets/` daqui a todos eles.

## Packs

`audio-bgm` · `audio-sfx-combat` · `audio-sfx-creatures` · `audio-sfx-ui` ·
`audio-sfx-vehicles` · `audio-sfx-world` · `characters` · `desert` · `farm` ·
`forest` · `infra` · `interiors` · `props` · `swamp` · `terrain` ·
`vegetation` · `vehicles` · `village`

### Áudio (BGM + SFX)

Os clips vivem em `public/assets/audio/` com o rid do manifest como caminho:

```
audio/bgm/*.ogg              # loops seamless (SA3 Music; -d exacto, @120 BPM)
audio/sfx/combat/*.ogg       # hits/swings/colheita/arco/escudo
audio/sfx/creatures/*.ogg    # roars/ferimentos/mortes/slime/lobo
audio/sfx/player/*.ogg       # hurt/heal do herói
audio/sfx/ui/*.ogg           # moedas/menus/save/notificações/eventos de corrida
audio/sfx/vehicles/*.ogg     # motor/nitro/skid/crash/buzina
audio/sfx/world/*.ogg        # portas/baús/fogo/passos/itens
```

Regen curado (gate de costura para BGM — re-rola seeds até cauda ≥70%):

```bash
cd Text2Sound && .venv/bin/python regen_audio.py          # só o que falta
.venv/bin/python regen_audio.py --force                   # tudo
.venv/bin/python regen_audio.py --only bgm/boss           # itens específicos
```

Ou pelo caminho GameAssets (sem gate): `gameassets resume --profile
game.yaml --manifest manifests/audio-bgm` (um por pack). Os manifests são a
fonte única de verdade (prompt, categoria, duração, seed); os sidecars
`.ogg.json` ao lado de cada clip trazem a proveniência da geração.

## Happy path de regen (GPU)

O batch corre a partir **desta** pasta e escreve em `public/assets/` daqui:

```bash
cd Viber/examples/shared-assets
gameassets resume --profile game.yaml --manifest manifests/village
gameassets resume --profile game.yaml --manifest manifests/characters
# … um por pack; `ls manifests/` lista todos
```

`resume` é idempotente: detecta por item o estado em disco (PNG/shape/paint) e
só gera o que falta — com os binários já presentes, o batch valida e faz skip.

> `output_dir` de cada manifest resolve-se contra a pasta **do manifest**, não
> contra a do `game.yaml`. Como os manifests vivem em `manifests/`, o valor
> correto é `../public/assets`; com `public/assets` o resume aponta para
> `manifests/public/assets`, não encontra nada e marca tudo como `need_image`.

## Como os exemplos consomem isto

Cada `vite.config.ts` passa o pool ao plugin da engine:

```ts
const sharedAssets = path.join(vibegameRoot, 'examples/shared-assets/public/assets');
export default defineConfig({ plugins: [vibegame({ sharedAssets })] });
```

- **dev**: o `public/` do próprio exemplo responde primeiro; o que faltar vem do
  pool. Um jogo pode portanto sobrepor um asset largando um ficheiro com o mesmo
  caminho no seu `public/`.
- **build**: o pool é copiado para `dist/assets/`, sem `_intermediate/`.

Sem symlinks e sem binários duplicados em disco. Não há passo de sync: editar
aqui chega a todos os exemplos no reload seguinte.

## O que **não** vive aqui

Media específico de cada jogo — `icons/`, `particles/`, `terrain/`
(heightmaps), `world/*.xml`, `data/` — fica no `public/` do respetivo exemplo.
O áudio ** vive** aqui desde 2026-08 (packs `audio-*`): os jogos referenciam
`/assets/audio/...` e o plugin serve do pool; para variante própria de um
jogo, larga um ficheiro com o mesmo caminho no `public/` desse jogo (o
`public/` local responde primeiro em dev e build).
