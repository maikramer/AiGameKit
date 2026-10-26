---
name: text2d
description: Gera imagens 2D a partir de texto com FLUX.3 Image Klein (SDNQ runtime), incluindo referências, silhuetas e layout por boxes. Use quando o utilizador pedir text-to-image, FLUX, imagens por prompt, consistência de personagem/poses (animação 2D), TEXT2D_MODEL_ID, HF_HOME, memory-efficient mode, ou integração com GameAssets/Text3D.
---

# Text2D — text-to-image (FLUX.3 Image Klein)

## Quando usar

- Gerar **uma ou várias** imagens a partir de **prompt** em texto.
- Afinar **resolução**, **steps**, **seed**, ou correr em **CPU / pouca VRAM**.
- O utilizador menciona **FLUX Klein**, **SDNQ**, ou pipelines que alimentam **Text3D** / **GameAssets**.
- Precisa de **consistência de personagem/poses** (referências `--ref`, silhuetas `--ref-silhouette`) ou **layout explícito** (`--boxes`) — útil para animação 2D.

## O que é

CLI **text-to-2D** com [FLUX.3 Image Klein](https://huggingface.co/black-forest-labs/flux-3-image-klein) (5.6B distilled + text encoder Qwen3-VL) em quantização **SDNQ** runtime, pensado para GPUs modestas (**group offload + CUDA streams** via hw-auto). Aceita até **10 imagens de referência**, **boxes de layout** e **silhuetas**; 16 steps (faixa 4-16), **sem guidance**.

## Pré-requisitos

- Python e dependências do pacote (ver `docs/INSTALL.md`).
- Espaço em disco e rede para **primeiro download** dos pesos (vários GB).

## Comandos principais

| Comando | Função |
|---------|--------|
| `text2d generate PROMPT [-o ficheiro.png]` | Gera uma imagem (com `--ref` vira edit/composição) |
| `text2d silhouette IMG [-o sil.png]` | Extrai a silhueta (rembg) para usar como referência |
| `text2d info` | Sistema, CUDA, VRAM, cache HF |
| `text2d models` | Modelos suportados / notas |
| `text2d skill install` | Instala esta skill em `.cursor/skills/text2d/` do projeto alvo |

**Opções frequentes em `generate`:** `--width` / `--height` (auto com `--ref`), `--steps` (4-16), `--seed`, `-r/--ref` (até 10), `--boxes` (JSON inline ou ficheiro), `--ref-silhouette`, `-n/--num-images`, `--upsample` (pup 9B, lento), `--cpu`, `--model` / `TEXT2D_MODEL_ID`, `-v` (verbose). `-g/--guidance` é **ignorado** (modelo distilled sem CFG). O **hw-auto** escolhe o preset SDNQ e o modo de colocação (GO+streams em GPUs apertadas).

## Exemplos

```bash
text2d generate "um gato com um cartaz" -o saida.png
text2d generate "paisagem" --width 768 --height 768 --steps 12
text2d generate "retrato" --seed 42 -o retrato.png
text2d generate "o mesmo personagem a correr" --ref heroi.png -o corrida.png
text2d generate "heroi aos saltos" --ref-silhouette heroi.png -o salto.png
text2d generate "dois balões" --boxes '[{"id":"b1","desc":"um balão vermelho","bbox":[0.1,0.1,0.5,0.4]}]'
text2d silhouette heroi.png -o heroi_sil.png
text2d -v generate "teste"
```

## Variáveis de ambiente

| Variável | Função |
|----------|--------|
| `TEXT2D_MODEL_ID` | Repo Hugging Face alternativo compatível com o ModularPipeline FLUX.3 (default = `black-forest-labs/flux-3-image-klein`; alternativa `-base`) |
| `HF_HOME` | Raiz do cache Hugging Face |

## Notas importantes

- **Primeira execução:** download de pesos — pode parecer “parado” durante rede/disco.
- Pesos **GGUF** são para fluxos tipo ComfyUI-GGUF, **não** este CLI Diffusers.
- **Guidance**: o FLUX.3 Klein é **guidance-distilled** — `-g` é ignorado. Para aderência ao prompt, aumentar steps (4-16) ou usar `--upsample`.
- **Licenças:** default = [flux-3-image-klein](https://huggingface.co/black-forest-labs/flux-3-image-klein) (**gated** — aceitar a FLUX Kommunity License v1.0 no Hub + `HF_TOKEN`) + SDNQ runtime MIT. Text encoder Qwen3-VL Apache-2.0; pup opcional público — ver `Text2D/README.md` e `AiGameKit/README.md`.
- Em **GameAssets**, resolução 2D elevada + outras apps na mesma GPU (ex.: **Godot** + editor 3D) aumenta risco de **OOM**; reduzir `width`/`height` no bloco `text2d` do `game.yaml` ou libertar VRAM.

## Prompt — boas práticas para imagens limpas (especialmente para 3D)

Quando a imagem 2D vai alimentar **Text3D** (image-to-3D), sombras e iluminação direcional viram **geometria fantasma** (placas/discos) no mesh. O Text3D aplica prompt enhancement automático, mas ao usar Text2D directamente convém seguir estas regras:

### Palavras/frases a **EVITAR** (causam sombras/artefactos)

| Categoria | Termos a evitar |
|-----------|----------------|
| **Posição/chão** | "on the ground", "on the floor", "on a pedestal", "standing on", "sitting on", "on a surface" |
| **Sombras** | "contact shadow", "drop shadow", "ground shadow" |
| **Iluminação direcional** | "dramatic lighting", "harsh lighting", "rim light", "spotlight", "volumetric light", "god rays", "backlit", "chiaroscuro" |
| **Flutuação** | "floating" (trigger de sombra — o modelo adiciona sombra para indicar flutuação) |

### Estrutura recomendada para prompts limpos

```
[enquadramento render] + [descrição do objeto] + [estilo visual] + [reforço de limpeza]
```

**Exemplo:**
```
3D game asset reference render, flat ambient lighting, white seamless background, a cute dragon, vibrant flat colors, completely shadowless, matte surface finish
```

### Nota sobre guidance

O FLUX.3 Image Klein é **step-distilled** e **não usa guidance scale**. Aumentar `--guidance` não tem efeito. Para melhor aderência ao prompt, aumentar `--steps` (8-16) e/ou usar `--upsample` (expande o prompt numa caption de cena completa).

## Ferramentas relacionadas

| Ferramenta | Ligação |
|------------|---------|
| **GameAssets** | Chama `text2d generate` por linha do manifest com prompts compostos. |
| **Text3D** | Pode usar Text2D no fluxo texto→imagem→3D, ou só imagem com `--from-image`. |

## Referências no repositório

- `src/text2d/cli.py` — CLI
- `src/text2d/generator.py` — `KleinFluxGenerator`
- `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`
