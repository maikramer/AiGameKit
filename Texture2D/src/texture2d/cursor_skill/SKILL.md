# Texture2D — Agent Skill

Ferramenta CLI para geração de texturas 2D seamless (tileable) localmente em GPU via pattern-diffusion, com PBR opcional via Materialize.

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
| `--seamless-method` | `late` | `none`, `late` (padding circular, default), `full` (noise-rolling) |
| `--quant` | `none` | Quantização: `none`, `fp8`, `nf4` |
| `--model/-m` | `Arrexel/pattern-diffusion` | Modelo HF |

## Requisitos

- Python 3.10+
- GPU CUDA (inferência local com pattern-diffusion)
- Token HF (env `HF_TOKEN`) — apenas para descarregar os pesos do Hub na primeira execução

## Integração com Materialize

Quando o binário `materialize` está disponível, `texture2d generate` gera os mapas PBR automaticamente após a diffuse. Sem Materialize, use o fluxo explícito em dois passos:

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```
