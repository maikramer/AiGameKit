---
name: text3d
description: Gera meshes 3D a partir de texto ou imagem (Text2D + Hunyuan3D-2.1 SDNQ INT4), só geometria; textura/PBR via Paint3D ou GameAssets. Use quando o utilizador pedir 3D, GLB/PLY/OBJ, Hunyuan, text-to-3D, image-to-3D, TEXT3D_BIN, PAINT3D_BIN, ou integração com GameAssets.
---

# Text3D — Text2D + Hunyuan3D-2.1 SDNQ

## Quando usar

- **Texto → 3D** ou **imagem → 3D** (mesh GLB/PLY/OBJ), **só geometria**.
- **Textura e PBR no GLB**: encadear **`paint3d texture`** (Hunyuan3D-Paint 2.1) ou **GameAssets** com `text3d.texture` no perfil.
- Diagnosticar GPU/PyTorch (`doctor`, `info`), converter formatos, listar modelos.

## O que é

Pipeline **text-to-3D**: geração de imagem (**Text2D** / FLUX Klein) + **Hunyuan3D-2.1** SDNQ INT4 (imagem → mesh). Entrada **só por imagem** (`--from-image` / `-i`) sem correr Text2D.

## Pré-requisitos

- Python e dependências (ver `docs/INSTALL.md`).
- Para textura/PBR no GLB: projeto **Paint3D**. O **`materialize`** (`MATERIALIZE_BIN`) é para mapas a partir de **imagem difusa** (Texture2D / GameAssets), não para o GLB 3D.

## Comandos principais

| Comando | Função |
|---------|--------|
| `text3d generate "prompt" [-o mesh.glb]` | Texto → imagem → mesh |
| `text3d generate --from-image img.png -o mesh.glb` | Só Hunyuan (sem Text2D); alias `-i` |
| `text3d generate … --preset fast\|balanced\|hq` | Perfis (substituem steps/octree/chunks por defeito) |
| `text3d generate … --save-reference-image` | Guarda PNG de referência (útil antes de `paint3d texture`) |
| `paint3d texture mesh.glb -i ref.png -o tex.glb` | Textura + material PBR no GLB (Paint3D 2.1); smooth bilateral ativo por defeito |
| `text3d doctor` | PyTorch, CUDA |
| `text3d info` | Sistema e GPU |
| `text3d convert entrada.ply -o saida.glb` | Conversão PLY/OBJ/GLB |
| `text3d models` | Lista componentes (Text2D, Hunyuan) |
| `text3d skill install` | Instala esta skill em `.cursor/skills/text3d/` do projeto alvo |

## Exemplos

```bash
text3d generate "uma cadeira de madeira" -o cadeira.glb
text3d generate "robô" --preset hq -o robo_shape.glb --save-reference-image
paint3d texture robo_shape.glb -i robo_shape_text2d.png -o robo_tex.glb  # smooth + bake_exp=6 por defeito
text3d generate --from-image referencia.png -o so_mesh.glb
text3d doctor
```

## Prompt — boas práticas e palavras a evitar

O Hunyuan3D interpreta silhuetas e contrastes da imagem 2D como geometria. Sombras, iluminação direcional e planos de chão na imagem viram **discos/placas** no mesh 3D.

O sistema aplica **prompt enhancement automático** (v2, framing positivo) que envolve o prompt do utilizador num enquadramento de render limpo. Usa `--no-prompt-optimize` só se quiseres controlo total.

### Palavras/frases a **EVITAR** no prompt (causam sombras → placas no 3D)

| Categoria | Termos tóxicos |
|-----------|---------------|
| **Posição/chão** | "on the ground", "on the floor", "on a pedestal", "on a platform", "standing on", "sitting on", "on a surface", "on a table" |
| **Sombras** | "contact shadow", "drop shadow", "ground shadow", "ambient occlusion on ground" |
| **Iluminação direcional** | "dramatic lighting", "harsh lighting", "rim light", "strong directional light", "spotlight", "volumetric light", "god rays", "backlit", "side lit", "chiaroscuro" |
| **Flutuação** | "floating" (trigger de sombra de flutuação — o modelo desenha sombra para indicar que o objeto flutua) |

### Termos que **AJUDAM** (framing positivo — o enhancement automático já os adiciona)

- "3D game asset reference render" — enquadra como render de referência
- "flat ambient lighting from all directions equally" — iluminação uniforme
- "pure white seamless infinite void background" — fundo branco sem horizonte
- "vibrant flat colors" — cores preservadas, sem shading
- "completely shadowless" — reforço positivo
- "matte surface finish" — evita reflexos especulares
- "single isolated object centered in frame" — composição limpa

### Se as placas persistirem

1. Verificar com `--save-reference-image` se a imagem 2D tem sombras
2. O pós-processo de mesh já remove placas na base (`--ground-shadow-aggressive` ou `--ground-shadow-very-aggressive`)
3. Aumentar `--t2d-steps` para 8+ dá melhor aderência ao prompt de iluminação

## Parâmetros úteis

- **VRAM baixa:** `--low-vram` (Hunyuan em CPU; **muito** mais lento), ou reduzir `--octree-resolution`, `--num-chunks`, `--steps`, ou usar `--preset fast`.
- **Qualidade:** `--preset hq` ou valores altos de steps/octree/chunks (ver `src/text3d/defaults.py`).
- **Text2D (quando aplicável):** `-W`/`-H`, `--t2d-steps`, `--t2d-guidance`, `--t2d-full-gpu`, `--model`.
- **Mesh:** `--mc-level`.

## Variáveis de ambiente (resumo)

| Variável | Função |
|----------|--------|
| `TEXT3D_BIN` | Caminho ao `text3d` se não estiver no `PATH` (útil para GameAssets) |
| `PAINT3D_BIN` | Caminho ao `paint3d` (batch GameAssets com textura/PBR) |
| `MATERIALIZE_BIN` | Caminho ao `materialize` para fluxos com imagem difusa (não usado pelo `text3d` em si) |
| `HF_HOME` | Cache Hugging Face |

## Licenças e pesos

- **Hunyuan3D-2.1** e ecossistema Tencent: ver model cards e licença **Tencent Hunyuan Community** (restrições de uso).
- Primeira execução: downloads grandes para `~/.cache/huggingface/`.

## Ferramentas relacionadas

| Ferramenta | Ligação |
|------------|---------|
| **Paint3D** | `paint3d texture` após o shape; GLB com PBR do pipeline 2.1. Suavização bilateral por defeito (`--smooth`, `--bake-exp 6`). |
| **GameAssets** | `game.yaml` com `text3d` + `texture` orquestra shape + `paint3d texture`. |
| **Materialize** | Mapas PBR a partir de difusa (ex. `texture2d.materialize` no GameAssets). |

## Documentação no repositório

- `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`, `docs/PAINT_SETUP.md`, `docs/API.md`, `docs/EXAMPLES.md`, `docs/PBR_MATERIALIZE.md`
- `src/text3d/defaults.py` — valores por defeito e presets HQ
