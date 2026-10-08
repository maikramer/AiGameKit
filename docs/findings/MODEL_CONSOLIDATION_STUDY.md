# Consolidação de modelos — inventário e plano de redução

**Data:** 2026-10-08 · **Estado:** ✅ **EXECUTADO** (tiers 1+2+3a + unificações de vendor; ver §8).

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

- **Venvs por tool: 126 GB → 28 GB** ✅ (executado 2026-10-08, ver §8): o stack torch
  CUDA estava replicado ×17 porque o pip **copia** wheels para cada venv (sem
  hardlinks como o uv) e o pip cache (`/home`) e os venvs (`/media/…`) estão em
  filesystems diferentes. Resolvido com dedupe por hardlinks in-place
  (`make dedupe-venvs`, `scripts/dedupe_venvs.py`) — sem reinstalações nem rede.
  Reexecutar após `./install.sh <tool>` ou upgrades de pacotes grandes.
- **`Viber/target`: 25 GB** — `cargo clean` (ou manter só o profile release). Ainda
  por fazer (WIP activo de outro agente no Viber — não limpar a meio de builds).
- **`outputs/` do Motion3D**: ~123 MB de NPZ de teste — limpeza trivial.

## 7. Sequência recomendada

1. Aprovar tiers → executar `rm` (Tier 1 é incondicional; 2 e 3 por decisão de uso).
2. Patches de código do §4 (um commit por tool; `make check` no fim).
3. `vramd doctor` após os cortes para confirmar que nenhum backend perdeu pesos
   (nenhum cortado está em `backends.yaml`/calibrações).
4. Venvs/target: iniciativa à parte, com janela dedicada.

## 8. Execução (2026-10-08) — registo

**Discos cortados (~42,5 GB):** hub HF 166→125 GB (Sana ×2, t5-base, SA Open 1.0 +
small, Disty0 klein-SDNQ, ai-forever Real-ESRGAN) + `motius-t2mgpt-humanml3d` 1,3 GB +
zips Quaternius 34 MB + log residual text2icon. HY-Motion full **mantido** (alavanca
`--quality highest`). Nota de infra: parte dos blobs HF era **propriedade de root**
(processo antigo correu como root) — precisou `sudo rm`; o hub vive no mount
`/media/maikeu/b1e7…` (mesmo disco do checkout), não em `/home`.

**Commits (main):**
- `f14d8f2d` Text2Sound: aliases/specs/helps legados Open removidos; ids SA3 canónicos
  exportados por `aigamekit_shared.quality` (`SA3_MUSIC_MODEL_ID`/`SA3_EFFECTS_MODEL_ID`),
  `models.py` importa-os — fim do sync manual.
- `8db6f77a` Motion3D/Shared: alias `T2MGPTPipeline`, footprints `motius-t2mgpt` e
  `hunyuan3d-2.1-dit` removidos (o registry do pacote PyPI vramd mantém as chaves
  dele para payloads antigos — sem impacto).
- `26595db6` Text2D: alternativa Disty0/FLUX.2-klein-4B-SDNQ removida de
  cli/tests/READMEs/TROUBLESHOOTING/SKILL (a env `TEXT2D_MODEL_ID` genérica fica).
- `472f592b` Paint3D: `texture_upscale` usa `ensure_realesrgan_ckpt()` (x4plus único).
- `05beaaaf` Text3D/AiGameKitLab: default morto do vendor bg-remover → BiRefNet;
  `pre_quantize.py` duplicado apagado.
- `45e655b6` Shared: fetch itch valida marker de `extracted/` antes do zip — zips
  apagáveis sem re-download (novo teste offline).

**Desvios do plano original (verificação de código):**
1. **Real-ESRGAN — direção invertida**: os dois .pth são treinos **diferentes**
   (702/702 tensors divergem; ai-forever é state_dict cru sem `params_ema`). Unificar
   no HF trocaria os pesos do enhance multiview (default ON) e exigia editar vendored
   do-not-modify. Ficou **um só**: `RealESRGAN_x4plus.pth` (xinntao) para upscale e
   enhance.
2. **Rename do footprint `stable-audio-open` → SKIP**: o registry do vramd PyPI
   (externo) não tem `stable-audio-3`; rename só no monorepo degrada o admit para o
   fallback 8 GiB. Fica pendente de release coordenada do vramd.

**Testes:** Text2Sound 619 ✓ · Shared 1313 ✓ · Motion3D 135 ✓ · Text2D 219 ✓ ·
Paint3D 254 ✓ · AiGameKitLab 274 ✓ · Text3D 538 ✓ + **7 falhas pré-existentes**
(octree ladder/`max_octree_for_vram` — confirmadas por stash antes/depois, WIP alheio).
`vramd doctor` pós-corte: sem perdas (vramd estava parado; auto-arranca no próximo uso).

## 9. Execução complementar — dedupe das venvs (2026-10-08, tarde)

`scripts/dedupe_venvs.py` + `make dedupe-venvs` (`DEDUPE_ARGS=--apply` para aplicar):
agrupa ficheiros ≥1 MiB entre todas as `*/.venv` por (tamanho, blake2b) e substitui
duplicados por hardlinks (atómico por ficheiro: `os.link` + `os.replace`; salta
`st_nlink>1`). Resultado: **126 GB → 28 GB** (98 GB libertados no disco `/media`,
95→193 GB livres), 3005 hardlinks, 0 falhas. As 17 venvs eram todas Python 3.13 com
torch 2.13.0+cu130 idêntico (Intrinsic 2.14.0) — daí a taxa tão alta.

Verificação pós-dedupe: `import torch` nas 16 venvs ✓ · suites rápidas Motion3D 135 /
Text2D 4 / Text2Sound 39 / Paint3D 48 / Shared 77 / AiGameKitLab 274 ✓ · canaries
`bpy` 5.2 / `diffusers` / `vramd` ✓. Semântica: upgrade/uninstall numa venv faz unlink
do link — irmãs intactas; reexecutar o dedupe após installs novos.
