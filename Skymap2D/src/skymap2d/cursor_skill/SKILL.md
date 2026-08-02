# Skymap2D — Agent Skill

Gera skymaps equirectangular 360° para game dev, localmente na GPU (FLUX.1-dev + LoRA).

## Quando usar

- O utilizador pede um skybox, skymap, panorama 360°, HDRI ou environment map.
- Contexto de game dev: céu, fundo de cena, ambiente exterior/interior.

## Comando

```bash
skymap2d generate "sunset over mountains, warm golden light" -o sky_sunset.png
skymap2d generate "starry night sky, milky way" --preset "Night Sky" -o sky_night.png
skymap2d batch prompts.txt --output-dir skymaps/
skymap2d presets
```

## Parâmetros importantes

- `--width/-W` (default: 2048) e `--height/-H` (default: 1024): ratio 2:1 recomendado.
- `--preset/-p`: Sunset, Night Sky, Overcast, Clear Day, Storm, Space, Alien World, Dawn, Underwater, Fantasy.
- `--guidance/-g` (default: 6.0): guidance scale.
- `--steps/-s` (default: 40): passos de inferência.
- `--model/-m`: override do modelo HF.

## Variáveis de ambiente

- `HF_TOKEN`: token Hugging Face (obrigatório).
- `SKYMAP2D_MODEL_ID`: override do modelo (default: `MultiTrickFox/Flux-LoRA-Equirectangular-v3`).
