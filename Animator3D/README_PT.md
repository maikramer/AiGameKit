# Animator3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI de **animação 3D** com [Blender Python API](https://docs.blender.org/api/current/) (`bpy`), pensada para encaixar depois do **Rigging3D** (mesh rigado → keyframes → export GLB/FBX).

## Requisitos

- **Python 3.13** — o wheel PyPI `bpy>=5.2.0` exige 3.13 e alinha com **Blender 5.2 LTS**.
- Blender embutido no pacote `bpy` (sem abrir janela; execução em background).

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev (pasta com `install.sh` e `Shared/`):

```bash
cd /caminho/para/GameDev
./install.sh animator3d
```

Equivalente: `gamedev-install animator3d`. Documentação geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
cd Animator3D
python3.13 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Uso

```bash
animator3d check
animator3d inspect rigged.glb
animator3d inspect rigged.glb --json-out
animator3d export rigged.glb copia.glb
animator3d wave-idle rigged.glb animado.glb --frames 60
animator3d wave-idle rigged.glb animado.glb --bone mixamorig:Spine
animator3d game-pack rigged.glb animado.glb --preset humanoid
```

| Comando | Descrição |
|---------|-----------|
| **Utilitários** | |
| `check` | Confirma `bpy` e mostra a versão do Blender |
| `inspect` | Importa e lista armatures, amostra de ossos e acções |
| `export` | Re-exporta (teste de roundtrip de import/export) |
| `list-clips` | Lista JSON dos clips de animação num ficheiro |
| `screenshot` | Renders PNG em várias vistas (Workbench por defeito; `--engine eevee`, `--ortho`, `--no-transparent-film`) |
| `inspect-rig` | Vistas do rig com ossos visíveis; mapa de calor de pesos opcional (`--show-weights`) |
| **Animação** | |
| `wave-idle` | Animação de teste (oscilação num osso) |
| `breathe-idle` | Idle multi-osso (respiração, asas, cauda, pescoço, braços) |
| `attack` | Animação de golpe/mordida |
| `walk` | Ciclo de caminhada com alternância das pernas e braços em contrafase |
| `run` | Ciclo de corrida (cadência mais rápida, maior amplitude) |
| `jump` | Salto: agachar → extensão → ar → aterragem (não em ciclo) |
| `fall` | Pose de queda com oscilação ao vento (não em ciclo) |
| `hover` | Pairar (batimento de asas) |
| `soar` | Planar / voo em deslize |
| `dive` | Mergulho / ataque em picado |
| `fire` | Sopro de fogo |
| `land` | Aterragem |
| `roar` | Rugido de vitória |
| **Lote** | |
| `game-pack` | Gera todas as animações de jogo num único comando (`--preset humanoid` / `creature` / `flying`; filtro opcional `--clips`) |

### `game-pack`

Gera todos os clips definidos por um preset de uma vez. Presets:

- **`humanoid`** — idle, walk, run, jump, fall
- **`creature`** — idle, walk, attack, roar
- **`flying`** — idle, hover, soar, dive, land

Usa `--clips` com uma lista separada por vírgulas de nomes curtos (correspondência nos nomes dos clips no GLB, por exemplo `idle`, `walk`, `run`) para executar só um subconjunto do preset.

```bash
animator3d game-pack rigged.glb animated.glb --preset humanoid
animator3d game-pack dragon.glb dragon_anim.glb --preset flying
animator3d game-pack monster.glb monster_anim.glb --preset creature
animator3d game-pack hero.glb hero_anim.glb --preset humanoid --clips idle,walk,run
```

## Fluxo com Rigging3D

1. `rigging3d pipeline --input mesh.glb --output rigged.glb`
2. `animator3d wave-idle rigged.glb com_animacao.glb` (ou pipeline próprio em Python usando `animator3d.bpy_ops`)

## Licença

MIT — ver [`LICENSE`](LICENSE).
