# Texture2D — Agent Skill

Ferramenta CLI para geração de texturas 2D seamless (tileable) via Stable Diffusion v1.5 + circular padding, executando localmente na GPU.

O tiling é por construção: todas as camadas `Conv2d` do UNet e VAE usam `padding_mode="circular"`, pelo que a saída ladrilha sem costuras — sem LoRA nem pós-processamento. Cabe em ~2,5 GB de VRAM (uma GPU de 6 GiB chega).

## Quando usar

- O utilizador quer gerar texturas seamless para chão, rochas, paredes, etc.
- Precisa de texturas tileable para game dev (PBR diffuse maps)
- Quer gerar texturas em batch a partir de uma lista de prompts

## Comandos

```bash
# Gerar uma textura
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# Usar preset
texture2d generate "weathered surface" --preset Stone -o wall.png

# Batch (ficheiro com um prompt por linha)
texture2d batch prompts.txt --output-dir textures/

# Listar presets
texture2d presets

# Info do ambiente
texture2d info
```

## Presets disponíveis

Wood, Fabric, Metal, Stone, Brick, Leather, Concrete, Marble, Grass, Sand, Dirt, Gravel, Tile Floor

## Parâmetros principais

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `--width/-W` | 512 | Largura |
| `--height/-H` | 512 | Altura |
| `--steps/-s` | 30 | Passos de inferência |
| `--guidance/-g` | 7.0 | Guidance scale (CFG real) |
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--preset/-p` | None | Preset de material |
| `--negative-prompt/-n` | "" | Prompt negativo (CFG nativo do SD1.5) |
| `--quality` | medium | Quality tier (`fast`/`low`/`medium`/`high`/`highest`) |
| `--model/-m` | stable-diffusion-v1-5/stable-diffusion-v1-5 | Modelo HF |
| `--ground` | auto | Modo chão top-down (modificadores de viewpoint/iluminação/escala) |
| `--cpu` | false | Forçar CPU |

> Nota: `--negative-prompt` funciona nativamente com o CFG real do SD1.5 (sem o custo 2x do true-cfg do FLUX distilled). O override do modelo via `--model/-m` ou a variável `TEXTURE2D_MODEL_ID`.

## Requisitos

- Python 3.13+
- GPU CUDA recomendada (cabe em ~2,5 GB de VRAM; uma GPU de 6 GiB é suficiente). `--cpu` força CPU (lento).

## Integração com Materialize

Após gerar a textura diffuse, use `materialize` para gerar mapas PBR:

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```
