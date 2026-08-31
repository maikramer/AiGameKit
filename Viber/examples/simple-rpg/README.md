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
engine. Referência (após terreno Fase 1): **843 entidades** vivas (702 grupos,
71 primitivas, 68 point lights, 1 directional, 1 câmara) + terreno com
heightfield e 32 ground features (8 pads, 7 lagos, 1 rio, 15 estradas);
**710 elementos em 33 tags** ainda em no-op (`GLTFLoader`×450, `ParticleSystem`×105,
`StaticSpawner`×58, …). Cada tag implementada na engine acende mais parte do
mundo sem editar os XMLs daqui.

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
