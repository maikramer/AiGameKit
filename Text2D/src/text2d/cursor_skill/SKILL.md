---
name: text2d
description: Gera imagens 2D a partir de texto com FLUX.2 Klein (SDNQ via Disty0). Use quando o utilizador pedir text-to-image, FLUX, imagens por prompt, TEXT2D_MODEL_ID, HF_HOME, memory-efficient mode, ou integração com GameAssets/Text3D.
---

# Text2D — text-to-image (FLUX.2 Klein)

## Quando usar

- Gerar **uma ou várias** imagens a partir de **prompt** em texto.
- Afinar **resolução**, **steps**, **seed**, ou correr em **CPU / pouca VRAM**.
- O utilizador menciona **FLUX Klein**, **Disty0**, **SDNQ**, ou pipelines que alimentam **Text3D** / **GameAssets**.

## O que é

CLI **text-to-2D** com [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) em quantização **SDNQ** ([Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic)), pensado para GPUs modestas (**CPU offload** via hw-auto / modo memory-efficient).

## Pré-requisitos

- Python e dependências do pacote (ver `docs/INSTALL.md`).
- Espaço em disco e rede para **primeiro download** dos pesos (vários GB).

## Comandos principais

| Comando | Função |
|---------|--------|
| `text2d generate PROMPT [-o ficheiro.png]` | Gera uma imagem |
| `text2d info` | Sistema, CUDA, VRAM, cache HF |
| `text2d models` | Modelos suportados / notas |
| `text2d skill install` | Instala esta skill em `.cursor/skills/text2d/` do projeto alvo |

**Opções frequentes em `generate`:** `--width` / `--height`, `--steps`, `--guidance` (SDNQ tipicamente **~1.0**), `--seed`, `--cpu`, `--model` / `TEXT2D_MODEL_ID`, `-v` (verbose). Em GPUs pequenas, o **hw-auto** (modo memory-efficient, ativo por defeito) seleciona o modelo 4B + CPU offload automaticamente.

## Exemplos

```bash
text2d generate "um gato com um cartaz" -o saida.png
text2d generate "paisagem" --width 768 --height 768 --steps 4 --guidance 1.0
text2d generate "retrato" --seed 42 -o retrato.png
text2d -v generate "teste"
```

## Variáveis de ambiente

| Variável | Função |
|----------|--------|
| `TEXT2D_MODEL_ID` | Repo Hugging Face alternativo compatível com o pipeline Klein (default = base oficial BFL `FLUX.2-klein-4B`/`-9B` fp16 + SDNQ runtime; mirrors pré-quantizados Disty0 via esta var) |
| `HF_HOME` | Raiz do cache Hugging Face |

## Notas importantes

- **Primeira execução:** download de pesos — pode parecer “parado” durante rede/disco.
- Pesos **GGUF** são para fluxos tipo ComfyUI-GGUF, **não** este CLI Diffusers.
- **Guidance** padrão **1.0** para a base FLUX.2 Klein (distilled; valores maiores costumam ser ignorados).
- **Licenças:** default = base oficial BFL (`4B` Apache 2.0 público; `9B` gated — aceitar termos no Hub) + SDNQ runtime MIT. Mirrors pré-quantizados Disty0 (`TEXT2D_MODEL_ID`) declara `flux-non-commercial-license` — ver `Text2D/README.md` e `AiGameKit/README.md`.
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

O modelo FLUX.2 Klein SDNQ é **step-wise distilled** e **ignora guidance scale** (funciona sempre como ~1.0). Aumentar `--guidance` não tem efeito. Para melhor aderência ao prompt, aumentar `--steps` (8+ recomendado).

## Ferramentas relacionadas

| Ferramenta | Ligação |
|------------|---------|
| **GameAssets** | Chama `text2d generate` por linha do manifest com prompts compostos. |
| **Text3D** | Pode usar Text2D no fluxo texto→imagem→3D, ou só imagem com `--from-image`. |

## Referências no repositório

- `src/text2d/cli.py` — CLI
- `src/text2d/generator.py` — `KleinFluxGenerator`
- `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`
