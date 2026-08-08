# Text2D / Icon / Texture / Skymap / Sound — descobertas

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

---

## Text2D (FLUX SDNQ)

- Gera referência 2D para Omni (`--from-image`) e assets 2D.
- Resolução default passou a **1024** (não 2048) — VRAM/tempo.
- Quant SDNQ + `--quality`; vramd precisa do preset no request.
- Matmul quantizado: `aigamekit_shared.sdnq.apply_quantized_matmul` (não helper
  privado em `text2d.generator` — removido).
- Payload wave/CLI: `text2d/ums_payload.py`.
- **Kernel:** `--compile` + `--channels-last` (~−10% hot). Default **ON** em
  `generate-batch` e vramd; one-shot `generate` opt-in.
- Guia: [`KERNEL_OPTS_FINDINGS.md`](KERNEL_OPTS_FINDINGS.md) ·
  bench [`../KERNEL_OPTS_BENCH.md`](../KERNEL_OPTS_BENCH.md).

Armadilha: imagem ref má (crop, fundo confuso, pose errada) → Omni semântica
errada mesmo com bbox/pose OK. Melhorar `idea`/prompt 2D antes de re-roll 3D.

---

## Text2Icon (Sana Sprint 0.6B)

- Ícones; BG transparente via **rembg**.
- Relativamente leve; bom canário vramd (~7 s hot vs ~20 s cold tip.).
- Servers legados `text2icon server` = deprecated; usar vramd.
- **Kernel:** `--channels-last` (~−13% hot) — default **ON** em `batch` + vramd.
  `--compile` em 6 GB **piora** hot — deixar OFF.

---

## Texture2D

- Texturas seamless (SD1.5 + circular padding).
- Backend vramd `texture2d`.
- CLI: `--compile` / `--channels-last` wired; benches 6 GB sem ganho útil.
- **Testes CI:** asserts de `dtype`/fp16 em `device="cuda"` têm de **skip** sem CUDA real —
  PyTorch pode cair em CPU e ficar float32 (falso negativo).

---

## Skymap2D (FLUX.1-dev + LoRA equirect)

### Bug do modelo LoRA (HF Flux-LoRA-Equirectangular-v3)

| Sintoma | Causa | Mitigação no generator |
|---------|-------|------------------------|
| Resolve 1024×768 em vez de 2048×1024 | Modelo ignora pedido | Auto-resize para panorama 2:1 |
| Polos ao **centro** vertical | Latitude trocada | Shift vertical 50% (`_fix_equirect_latitude`) |

### Kernel

- `--compile` (~−19% hot @ 1024×512/14 steps). Default **ON** em `batch` + vramd.
- Cold compile ~6 min — **não** usar em one-shot.
- `--channels-last` ≈0 ganho nestes benches.

### Three.js / VibeGame

- Convenção equirect: centro = horizonte, topo = zénite, fundo = nadir.
- `PMREMGenerator` **ignora** `texture.offset` / `repeat` no shader interno →
  corrigir bitmap (canvas) antes de `fromEquirectangular()`.
- Retrato (H>W) ou eixos trocados → artefactos tipo “pilares”; normalizar
  landscape 2:1 antes do PMREM.
- Helper: `applyEquirectSkyEnvironment` (`vibegame` extras).

### Testes CLI

Com vramd a correr, `CliRunner` + `generate` sem isolamento → job real +
`VRAM_INSUFFICIENT`. Padrão: `--no-vramd` + mock
`prepare_gpu_exclusive` / `warn_if_vram_occupied` (ver
`Skymap2D/tests/test_hardware.py`).

---

## Text2Sound (Stable Audio Open)

- SFX/BGM; kinds via QualityEngine / `audio_kinds`.
- **Trim silêncio inicial** na geração ou import — senão latência perceptível
  no jogo (passos, hits).
- **Duração curta** (~0.5–1.2 s) para one-shots. Saídas ~20–30 s (cauda) fazem
  um único `playSound('swing')` soar a combate infinito — ver
  [`VIBEGAME_AUDIO_COMBAT_FINDINGS.md`](VIBEGAME_AUDIO_COMBAT_FINDINGS.md).
- Preferir integração engine: `AudioListener` + câmera; gesto user para
  AudioContext; **não** `preloadSounds`/Howl no boot (enfileira até gesto —
  ver [`VIBEGAME_AUDIO_COMBAT_FINDINGS.md`](VIBEGAME_AUDIO_COMBAT_FINDINGS.md));
  bank `playSound` / `playSoundAt` com `originEid`.
- CLI: `--compile` / `--channels-last` no DiT/VAE; benches short SFX sem ganho
  hot (compile só aumenta cold).
- `text2sound.utils.safe_filename` / `generate_output_path` → re-export /
  delegação a `aigamekit_shared.path_utils` (monkeypatch de `time` nos testes:
  `aigamekit_shared.path_utils.time.time`, não `text2sound.utils.time`).
- **Mastering (`pedalboard` + pyloudnorm):** em runners CI a wheel pode
  **SIGILL** e matar o processo pytest inteiro. Nunca `import pedalboard` no
  topo dos testes — usar `Text2Sound/tests/_heavy_deps.py`
  (`require_mastering_stack` / probe em subprocesso) e skip se inseguro.
  Guia CI: [`../TESTING.md`](../TESTING.md#github-actions-ci-githubworkflowsciyml).

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Link preload diferido / gesto Howler (VibeGame audio findings) |
| 2026-07-24 | SFX long-tail pitfall + link VibeGame audio/combat findings |
| 2026-07-24 | Pedalboard SIGILL probe; Texture2D skip dtype sem CUDA |
| 2026-07-24 | `apply_quantized_matmul` Shared; Skymap testes `--no-vramd`; Text2Sound path_utils; kernel flags vramd/batch |
| 2026-07-19 | Skymap shift/resize; Text2D 1024; audio trim; PMREM offset |
