# Text2D / Icon / Texture / Skymap / Sound — descobertas

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

---

## Text2D (FLUX SDNQ)

- Gera referência 2D para Omni (`--from-image`) e assets 2D.
- Resolução default passou a **1024** (não 2048) — VRAM/tempo.
- Quant SDNQ + `--quality`; UMS precisa do preset no request.
- Bench: [`../KERNEL_OPTS_BENCH.md`](../KERNEL_OPTS_BENCH.md).

Armadilha: imagem ref má (crop, fundo confuso, pose errada) → Omni semântica
errada mesmo com bbox/pose OK. Melhorar `idea`/prompt 2D antes de re-roll 3D.

---

## Text2Icon (Sana Sprint 0.6B)

- Ícones; BG transparente via **rembg**.
- Relativamente leve; bom canário UMS (~7 s hot vs ~20 s cold tip.).
- Servers legados `text2icon server` = deprecated; usar UMS.

---

## Texture2D

- Texturas seamless (HF / SD + circular padding tip.).
- Backend UMS `texture2d`.

---

## Skymap2D (FLUX.1-dev + LoRA equirect)

### Bug do modelo LoRA (HF Flux-LoRA-Equirectangular-v3)

| Sintoma | Causa | Mitigação no generator |
|---------|-------|------------------------|
| Resolve 1024×768 em vez de 2048×1024 | Modelo ignora pedido | Auto-resize para panorama 2:1 |
| Polos ao **centro** vertical | Latitude trocada | Shift vertical 50% (`_fix_equirect_latitude`) |

### Three.js / VibeGame

- Convenção equirect: centro = horizonte, topo = zénite, fundo = nadir.
- `PMREMGenerator` **ignora** `texture.offset` / `repeat` no shader interno →
  corrigir bitmap (canvas) antes de `fromEquirectangular()`.
- Retrato (H>W) ou eixos trocados → artefactos tipo “pilares”; normalizar
  landscape 2:1 antes do PMREM.
- Helper: `applyEquirectSkyEnvironment` (`vibegame` extras).

---

## Text2Sound (Stable Audio Open)

- SFX/BGM; kinds via QualityEngine / `audio_kinds`.
- **Trim silêncio inicial** na geração ou import — senão latência perceptível
  no jogo (passos, hits).
- Preferir integração engine: `AudioListener` + câmera; gesto user para
  AudioContext.

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-19 | Skymap shift/resize; Text2D 1024; audio trim; PMREM offset |
