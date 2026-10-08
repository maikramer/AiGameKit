# Consolidação de modelos — inventário e plano de redução

**Data:** 2026-10-08 · **Estado:** estudo concluído, **nada executado ainda** — cortes aguardam aprovação.

Objetivo: reduzir o número de modelos/variantes em uso, o espaço em disco e a
complexidade de vendors. Método: `du` sobre todos os caches (`~/.cache/huggingface`,
`~/.cache/aigamekit`, `~/.cache/rigging3d`, `~/.cache/torch`) + varrimento de todas as
referências a modelos no código (repo ids, `from_pretrained`, env vars, `backends.yaml`,
calibrações).

## 1. Fotografia do disco (modelos)

| Local | Tamanho | Conteúdo |
|-------|---------|----------|
| `~/.cache/huggingface/hub` | ≈166 GB | 25 modelos (ver §2) |
| `~/.cache/aigamekit/models` | 7,0 GB | HY-Motion lite+full (5,7) + T2MGPT legado (1,3) |
| `~/.cache/aigamekit/{quaternius,itch}` | 230 MB | packs UAL1/UAL2/villager (zips + extraídos) |
| `~/.cache/rigging3d` | 24 KB | SkinTokens vazio nesta máquina (pesos não presentes) |
| `~/.cache/torch` | 0 | Intrinsic não descarregado nesta máquina |

Sem revisões duplicadas nos modelos grandes (1 snapshot cada). Fora do âmbito
"modelos" mas medido: venvs por tool **126 GB** e `Viber/target` **25 GB** (ver §6).

## 2. Inventário HF hub — dono, uso, estado

| Modelo | Disco | Tool | Estado |
|--------|-------|------|--------|
| `tencent/Hunyuan3D-Omni` | 24 GB | Text3D | ATIVO (shape) |
| `black-forest-labs/FLUX.2-klein-4B` | 23 GB | Text2D | ATIVO (base nesta GPU 6 GB; 9B é default high-VRAM, não está em disco) |
| `tencent/Hunyuan3D-2.1` | 17 GB | Paint3D | ATIVO (paint-PBR v2-1) |
| `Qwen/Qwen3-8B` | 16 GB | Motion3D | ATIVO (encoder de texto, lite **e** full) |
| `stabilityai/stable-audio-open-1.0` | 15 GB | Text2Sound | **LEGADO** (só via `--model open-1.0`/`1.0`) |
| `Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32` | 13 GB | Skymap2D | ATIVO (única base compatível com a LoRA equirect) |
| `tencent/Hunyuan3D-Part` | 12 GB | Part3D | ATIVO (+ `spaces--…-Part` 115 MB: pipeline vs quantizador) |
| `Efficient-Large-Model/Sana_600M_512px_diffusers` | 9,4 GB | — | **ÓRFÃO** (resto do Text2Icon, removido 807b6c69) |
| `stable-diffusion-v1-5/stable-diffusion-v1-5` | 6,6 GB | Texture2D | ATIVO |
| `Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic` | 5,2 GB | Text2D | REDUNDANTE (alternativa `TEXT2D_MODEL_ID`; SDNQ runtime é o caminho default) |
| `stabilityai/stable-audio-open-small` | 4,7 GB | Text2Sound | **LEGADO** (só via `--model open-small`) |
| `facebook/dinov2-giant` | 4,3 GB | Paint3D | ATIVO (conditioning DINO) |
| `clark-labs/clark-air-sana-1.6b-1.58bit` | 3,4 GB | — | **ÓRFÃO** (Text2Icon) |
| `stabilityai/stable-audio-3-small-{music,sfx}` | 3,3+3,3 GB | Text2Sound | ATIVO (gated) |
| `openai/clip-vit-large-patch14` | 1,6 GB | Motion3D | ATIVO (encoder) |
| `facebook/dinov2-large` | 1,2 GB | Text3D | ATIVO (Omni encoder) |
| `xandergos/terrain-diffusion-30m` | 1,1 GB | Terrain3D | ATIVO |
| `t5-base` | 853 MB | — | **ÓRFÃO** (zero refs; Disty0 SDNQ embute os encoders) |
| `ZhengPeng7/BiRefNet` | 425 MB | Text3D | ATIVO (bg removal) |
| `MultiTrickFox/Flux-LoRA-Equirectangular-v3` | 328 MB | Skymap2D | ATIVO (LoRA 360°) |
| `stabilityai/sd-vae-ft-mse` | 320 MB | Texture2D | ATIVO (VAE swap) |
| restantes (Real-ESRGAN 64 MB, taesdxl 9 MB, Qwen3-0.6B 28 KB, IRs 12 KB, stubs) | <100 MB | vários | triviais |

Cache local `~/.cache/aigamekit/models`: `HY-Motion-1.0-Lite` **1,8 GB** (default) e
`HY-Motion-1.0` full **3,9 GB** (só `--model full` / `--quality highest`);
`motius-t2mgpt-humanml3d` **1,3 GB** — pipeline T2MGPT reformulado para HY-Motion.

## 3. Cortes propostos (por tier)

### Tier 1 — Órfãos, zero risco (~15,0 GB)

Nenhuma referência em código/yaml/docs. Restos do Text2Icon (removido) e do T2MGPT.

```bash
rm -rf ~/.cache/huggingface/hub/models--Efficient-Large-Model--Sana_600M_512px_diffusers
rm -rf ~/.cache/huggingface/hub/models--clark-labs--clark-air-sana-1.6b-1.58bit
rm -rf ~/.cache/huggingface/hub/models--t5-base
rm -rf ~/.cache/aigamekit/models/motius-t2mgpt-humanml3d
rm -f  ~/.cache/aigamekit/ums-worker-text2icon.log   # resíduo do worker removido
```

### Tier 2 — Legado declarado (~19,7 GB)

Text2Sound Open é legado documentado (READMEs/docstring), só alcançável por alias
`--model open-1.0` / `open-small` / `1.0`. A família SA3 (music/sfx) cobre o uso atual.

```bash
rm -rf ~/.cache/huggingface/hub/models--stabilityai--stable-audio-open-1.0
rm -rf ~/.cache/huggingface/hub/models--stabilityai--stable-audio-open-small
```

⚠️ Nota: SA3 é gated; o Open legado era ungated. Cortar remove o único fallback local
se o token HF perder acesso SA3 (re-download continua possível com token válido).

### Tier 3 — Redundância optativa (~9,1 GB)

- `Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic` (5,2 GB): pré-quantizado alternativo via
  `TEXT2D_MODEL_ID`. Desde que GO+streams+SDNQ runtime é o caminho default
  ([TEXT2D findings](../findings/TEXT2D_GROUP_OFFLOAD_FINDINGS.md)), é duplicação.
- `HY-Motion-1.0` full (3,9 GB): default é lite. Manter só se `--quality highest` /
  `--model full` for usado em trabalho sério.

**Total dos três tiers: ≈43,8 GB (≈26% do hub HF).**

## 4. Simplificações de código / vendor (sem impacto de disco relevante)

1. **Text2Sound** — remover aliases legados `open-1.0`/`open-small`/`1.0`
   (`Text2Sound/src/text2sound/models.py:22-23,119-123`) e as menções "legado" nos
   READMEs. O footprint `stable-audio-open` em `backends.yaml` serve **SA3** — renomear
   para `stable-audio-3` (arrasta `calibrated/backends-6g.yaml`; opcional, cosmético).
2. **Fonte única dos repo ids SA3** — `Shared/src/aigamekit_shared/quality.py:29-30`
   duplica as constantes do Text2Sound; mover a tabela de aliases para Shared.
3. **Motion3D** — apagar alias `T2MGPTPipeline = HYMotionPipeline`
   (`Motion3D/src/motion3d/pipeline.py:279`), footprint `motius-t2mgpt`
   (`Shared/.../lowvram.py:146`, "retired path") e o footprint órfão
   `hunyuan3d-2.1-dit` (`lowvram.py:133`).
4. **Paint3D — um só Real-ESRGAN**: hoje há dois (`ai-forever/Real-ESRGAN` HF no
   postprocess `--upscale` vs URL GitHub `xinntao` no enhance multiview,
   `hy3d21_paths.py:16`/`painter.py:1043` → 67 MB em `hy3dpaint/ckpt/`). Unificar no
   HF (spandrel, já em cache) e apagar o caminho xinntao.
5. **Text3D — um só bg-remover**: wrapper usa `BiRefNet`, mas o vendor
   `hy3dshape/preprocessors.py:165-166` ainda tem default `briaai/RMBG-2.0` (não está
   em cache — alinhar o default evita download acidental futuro).
6. **Quantizador Hunyuan3D-2.1 duplicado**: `Paint3D/.../quantize_unet.py` vs
   `AiGameKitLab/.../pre_quantize.py` — manter um, importar do outro.
7. **Text2D** — após corte do Disty0 klein, remover a alternativa documentada em
   `Text2D/src/text2d/cli.py:908-910`.
8. **Quaternius** — apagar os zips UAL1/2 (34 MB; `extracted/` fica, lockfiles têm
   sha256 para re-download). Trivial.

## 5. O que NÃO cortar (e porquê)

- **Três Hunyuan distintos** (Omni 24 / 2.1 17 / Part 12 GB): papéis diferentes
  (shape / paint / parts), todos ativos no DAG Round 3 — não são variantes entre si.
- **Duas famílias FLUX**: klein-4B (Text2D) e FLUX.1-dev-SDNQ (Skymap2D). A LoRA
  equirect é FLUX.1 — incompatível com klein. Consolidação futura só se surgir LoRA
  equirect para klein/FLUX.2.
- **Qwen3-8B 16 GB + CLIP**: encoders do HY-Motion, partilhados por lite e full.
- **SD1.5 + VAE ft-mse**: Texture2D (seamless 2.0).
- **SA3 music+sfx, dinov2-{giant,large}, BiRefNet, terrain-diffusion, LoRA equirect**:
  ativos.

## 6. Achados secundários de disco (fora do âmbito "modelos")

- **Venvs por tool: 126 GB** — o stack torch CUDA está replicado ×17. O pip cache
  (`~/.cache/pip`, em `/home`) e os venvs (em `/media/…/GitClones`) estão em
  filesystems diferentes → sem hardlinks, cópias completas. Mover o cache para o mesmo
  FS e reinstalar os venvs (ou `uv` com cache no mesmo FS) deduplica ~5-6 GB/venv.
  Iniciativa separada e arriscada (workers vramd dependem dos venvs) — fazer tool a
  tool com `vramd stop`.
- **`Viber/target`: 25 GB** — `cargo clean` (ou manter só o profile release).
- **`outputs/` do Motion3D**: ~123 MB de NPZ de teste — limpeza trivial.

## 7. Sequência recomendada

1. Aprovar tiers → executar `rm` (Tier 1 é incondicional; 2 e 3 por decisão de uso).
2. Patches de código do §4 (um commit por tool; `make check` no fim).
3. `vramd doctor` após os cortes para confirmar que nenhum backend perdeu pesos
   (nenhum cortado está em `backends.yaml`/calibrações).
4. Venvs/target: iniciativa à parte, com janela dedicada.
