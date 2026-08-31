# simple-rpg (migrado para Viber)

Porta do exemplo `VibeGame/examples/simple-rpg` para a engine nativa Viber.
O mundo foi convertido por `Viber/scripts/migrate_from_vibegame.py` — tags
ainda não implementadas **passam verbatim e fazem no-op**, por isto o jogo
já corre (parcialmente) enquanto a engine cresce por incrementos.

## Correr

```bash
cd Viber
cargo run -- run examples/simple-rpg/world.xml          # janela
cargo run -- analyze examples/simple-rpg/world.xml       # headless + cobertura
```

(ou, com o CLI instalado: `viber run examples/simple-rpg/world.xml`)

## Estado atual

O `analyze` imprime o relatório de cobertura — é o roteiro de trabalho da
engine. Referência (após glTF + spawners + partículas + exclusões): **1398
entidades** declaradas (702 grupos, 71 primitivas, 68 point lights, 1
directional, 1 câmara, **450 cenas glTF**, **58 static + 12 dynamic spawn
groups**, **8 vegetation groups** (cap 800 instâncias/tag), **105 emissores
de partículas**, **24 zonas de exclusão** respeitadas por todos os
spawners) + heightfield com 32 ground features; **53 elementos em 27 tags**
ainda em no-op (`DialogueNPC`×17, `MusicLayer`×6, `BiomeRegion`×4,
`ResourceChip`×3, HUD/áudio/clima…). ~28 mil entidades vivas na janela.
Cada tag implementada na engine acende mais parte do mundo sem editar os
XMLs daqui.

Assets: os GLBs do pool partilhado vêm com meshopt + KTX2/BasisU +
quantização (bevy 0.19 não lê nenhuma das três sintaxes). O espelho
`assets/` (regenerável com `scripts/sync_assets.py`, não versionado) guarda
cópias decomprimidas; a asset root do mundo é a pasta que contém `assets/`.

## Estrutura

- `world.xml` — raiz ( porta do `index.html` original; `<Scene>` → `<world>` )
- `world/**.xml` — módulos migrados, espelham `public/world/` do original
- `public` — symlink para `VibeGame/examples/simple-rpg/public` (assets GLB/
  texturas partilhados; quando glTF chegar, os caminhos `/assets/...` resolvem
  aqui sem duplicar GBs)

## Re-migrar

Depois de mudanças no mundo original (ou no conversor):

```bash
python3 Viber/scripts/migrate_from_vibegame.py \
  VibeGame/examples/simple-rpg/index.html \
  --public VibeGame/examples/simple-rpg/public \
  -o Viber/examples/simple-rpg/
```

Cada ficheiro de saída leva um cabeçalho com os attrs descartados e as tags
passadas verbatim. Regras de mapeamento: docstring do conversor.

## Próximos incrementos (por ordem de impacto visual)

1. `GLTFLoader` → `GltfScene` (450 modelos: casas, criaturas, vegetação)
2. `StaticSpawner` (58 spawners de vegetação/props)
3. `ParticleSystem` (105 emissores: fogueiras, poeira, clima)
4. Player + `ThirdPersonCamera` com input (câmara anda, mundo segue)
