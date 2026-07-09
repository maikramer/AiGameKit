# Text2Icon

CLI text-to-icon com **Sana Sprint 0.6B** ([NVlabs/Sana](https://github.com/NVlabs/Sana)) — gera ícones de UI para jogos em 1–4 passos (~<1s por imagem em GPU moderna). Suporta fundo transparente (RGBA via `rembg`).

Parte do monorepo **GameDev**. Veja [`../AGENTS.md`](../AGENTS.md) para o guia geral.

## Instalação

```bash
# No monorepo (depois de instalar Shared/)
./install.sh text2icon
# ou
cd Text2Icon && pip install -e ".[dev]"
```

Requer Python 3.13, CUDA (ou CPU, lento) e ~2.5 GB de cache HF para o modelo.

## Uso

```bash
# Ícone opaco (RGB)
text2icon generate "red health potion, fantasy game" -o health.png

# Ícone transparente (RGBA via rembg/U2Net)
text2icon generate "sword icon, medieval RPG" -o sword.png --transparent

# Batch (um prompt por linha)
text2icon batch icons.txt -d icons/ --transparent --quality medium

# Variante standard (20 passos, maior qualidade, ~10x mais lento)
text2icon generate "shield emblem" -m Efficient-Large-Model/Sana_600M_1024px_diffusers
```

## Opções

| Flag | Default | Descrição |
|------|---------|-----------|
| `-o/--output` | `outputs/icons/<slug>_<ts>.png` | Ficheiro de saída |
| `-W/--width` | `512` | Largura (múltiplo de 8) |
| `-H/--height` | `512` | Altura (múltiplo de 8) |
| `-s/--steps` | `2` | Passos de inferência (Sprint: 1–4) |
| `-g/--guidance` | `4.5` | Guidance scale (CFG) |
| `--seed` | aleatório | Seed reprodutível |
| `-n/--negative-prompt` | `""` | Prompt negativo |
| `--transparent/--no-transparent` | off | Remover fundo (rembg/U2Net) |
| `-m/--model` | Sana_Sprint_0.6B | ID do modelo HF |
| `--cpu` | off | Forçar CPU |
| `--low-vram` | auto | CPU offload |
| `--gpu-ids` | auto | Split multi-GPU (ex: `0,1`) |
| `--quality` | `medium` | Tier de qualidade (fast/low/medium/high/highest) |
| `--hw-auto/--no-hw-auto` | on | Auto-detecção de hardware |

## Modelos

- **Default**: `Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers` (1–4 passos)
- **Standard**: `Efficient-Large-Model/Sana_600M_1024px_diffusers` (20 passos, qualidade superior)

## Integração com GameAssets

`text2icon` está integrado no pipeline do `gameassets` (stage scene-level, como o `skymap2d`). Adiciona um bloco `text2icon:` ao `game.yaml`:

```yaml
text2icon:
  prompts:
    - "health potion icon"
    - "mana potion icon"
    - "sword icon"
  transparent: true
  width: 256
  height: 256
```

Depois `gameassets batch` gera os ícones e `gameassets handoff` copia-os para `public/assets/icons/`.

## Variáveis de ambiente

| Variável | Efeito |
|----------|--------|
| `TEXT2ICON_MODEL_ID` | Override do modelo default |
| `TEXT2ICON_BIN` | Caminho do binário (usado pelo GameAssets) |
| `TEXT2ICON_HW_AUTO` | `0` desliga a auto-detecção de hardware |
| `HF_HOME` | Cache do Hugging Face |

## Testes

```bash
make test-text2icon
# ou
cd Text2Icon && pytest tests/
```

Os testes são CPU-only e fazem mock do torch/Sana (sem download de modelo).

## Licença

MIT. Modelo Sana: ver [NVlabs/Sana](https://github.com/NVlabs/Sana).
