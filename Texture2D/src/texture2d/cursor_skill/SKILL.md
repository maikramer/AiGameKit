# Texture2D — Agent Skill

Ferramenta CLI para geração de texturas 2D seamless (tileable) via HF Inference API.

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
| `--width/-W` | 1024 | Largura |
| `--height/-H` | 1024 | Altura |
| `--steps/-s` | 50 | Passos de inferência |
| `--guidance/-g` | 7.5 | Guidance scale |
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--preset/-p` | None | Preset de material |
| `--negative-prompt/-n` | "" | Prompt negativo |
| `--lora-strength` | 1.0 | Força do LoRA |
| `--model/-m` | Flux-Seamless-Texture-LoRA | Modelo HF |

## Requisitos

- Python 3.10+
- Token HF (env `HF_TOKEN` ou `HUGGINGFACEHUB_API_TOKEN`) — recomendado para modelos gated
- Sem GPU local necessária (geração via API cloud)

## Integração com Materialize

Após gerar a textura diffuse, use `materialize` para gerar mapas PBR:

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```
