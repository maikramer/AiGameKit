# AiGameKit Monorepo — Roadmap

Lacunas identificadas no pipeline de game development e plano de acção priorizado.

---

## ✅ Crítico — Implementado

### LOD automático

- **Problema:** Todo jogo 3D precisa de níveis de detalhe — hoje é manual ou inexistente
- **Estado:** ✅ Implementado
- **CLI:** `text3d lod modelo.glb -o ./out_dir --basename prop` → `prop_lod0.glb`, `prop_lod1.glb`, `prop_lod2.glb`
- **Batch:** `pipeline: [3d, lod]` no manifest YAML + `--no-lod` para saltar
- **Profile:** `lod: {lod1_ratio: 0.42, lod2_ratio: 0.14, min_faces_lod1: 500, min_faces_lod2: 150, meshfix: false}`
- **Handoff:** Copia `*_lod{0,1,2}.glb` para `assets/models/`
- **Ficheiros:** `Text3D/src/text3d/utils/mesh_lod.py`, `Text3D/src/text3d/cli.py`, `GameAssets/src/gameassets/cli.py`

### Mesh de colisão

- **Problema:** Unity/Godot/Unreal precisam de convex hull simplificado (~100-500 tri) para física
- **Estado:** ✅ Implementado
- **CLI:** `text3d collision modelo.glb -o collision.glb --max-faces 300 --convex-hull`
- **Batch:** `pipeline: [3d, collision]` no manifest YAML + `--no-collision` para saltar
- **Profile:** `collision: {max_faces: 300, convex_hull: true}`
- **Handoff:** Copia `*_collision.glb` para `assets/models/`
- **Ficheiros:** `Text3D/src/text3d/utils/collision.py`, `Text3D/src/text3d/cli.py`, `GameAssets/src/gameassets/cli.py`

### Compressão de áudio

- **Problema:** Text2Sound gera WAV 44kHz — nenhum jogo shipped usa WAV
- **Estado:** ✅ Implementado
- **Handoff:** `gameassets handoff --audio-format ogg --sfx-sample-rate 22050 --bgm-sample-rate 44100`
- **Comportamento:** Converte WAV → OGG Vorbis via ffmpeg; fallback para cópia original se ffmpeg falhar
- **Deteção:** SFX (profile effects → 22050 Hz) vs BGM (profile music → 44100 Hz)
- **Ficheiros:** `GameAssets/src/gameassets/handoff_export.py`, `GameAssets/src/gameassets/cli.py`

### Validação de assets

- **Problema:** Nenhuma verificação automática — GLB sem textura, mesh vazio, poly count absurdo
- **Estado:** ✅ Implementado
- **CLI:** `gameassets validate --profile game.yaml --manifest manifest.yaml --max-poly-count 100000`
- **Verificações:** GLB existência/poly count/textura/tamanho, LODs, collision mesh, áudio
- **Ficheiros:** `GameAssets/src/gameassets/validator.py`, `GameAssets/src/gameassets/cli.py`

---

## 🟡 Importante — Melhora workflow significativamente

### Variações de SFX

- **Problema:** "Coin pickup" soa sempre igual — jogo precisa de 3-5 variações
- **Acção:** `text2sound generate --variations 5` com seeds automáticos; GameAssets manifest suporta `audio_variations: 5`
- **Integração:** Handoff exporta `sfx_coin_01.ogg`, `sfx_coin_02.ogg`, etc.

### Resume robusto do batch

- **Problema:** Batch falha a meio e não retoma bem — repete assets já gerados
- **Estado:** `--resume` existe mas não verifica integridade dos ficheiros existentes
- **Acção:** Verificar cada output antes de saltar (tamanho > 0, formato válido); log de progresso persistente

### Paralelização multi-GPU no batch

- **Problema:** Batch processa linhas sequencialmente — com 2+ GPUs poderia gerar em paralelo
- **Estado:** `--gpu-ids` existe mas divide pesos do modelo, não linhas do batch
- **Acção:** Distribuir linhas por GPUs (round-robin ou por tamanho estimado de VRAM)

---

## 🟢 Nice-to-have — Diferenciação

### Texture atlas / sprite sheet

- **Problema:** Jogos 2D precisam de sprite sheets — hoje é manual
- **Acção:** `text2d spritesheet` — pack de imagens em atlas com metadata JSON

### Asset size budget

- **Problema:** Sem controlo de tamanho total — pode estourar limite de plataforma
- **Acção:** `gameassets budget --max-total-mb 500` — falha se exceder, sugere optimizações

### Melhorias de iteração

- **Problema:** Para refinar um asset tem de regerar do zero — sem "variação sobre o existente"
- **Acção:** `text3d refine` — img2img ou mesh refinement a partir de um GLB existente; `text2sound remix`

### Integração directa com engines

- **Problema:** Godot/Unity/Unreal precisam de import manual
- **Acção:** Exportadores: `gameassets export --target godot` (cria .import, .tscn), `--target unity` (prefabs)

---

## Estado actual do monorepo

| Ferramenta | Versão | Funcional |
|---|---|---|
| Shared | 0.2.0 | ✅ logging, GPU, subprocess, installers |
| ModelServer (UMS) | 0.1.0 | ✅ supervisor VRAM unificado |
| Text2D | 0.1.0 | ✅ text-to-image (FLUX SDNQ) |
| Text2Icon | 0.1.0 | ✅ text-to-icon (Sana Sprint 0.6B) |
| Text3D | 0.1.0 | ✅ text-to-3D (Hunyuan3D-Omni), LOD, collision, simplify |
| Paint3D | 0.1.0 | ✅ 3D texturing (Hunyuan3D-Paint 2.1) |
| Part3D | 0.1.0 | ✅ decomposição semântica (Hunyuan3D-Part) |
| Rigging3D | 0.6.0 | ✅ auto-rigging (SkinTokens) |
| Animator3D | 0.1.0 | ✅ animation (bpy 5.2 LTS) |
| Texture2D | 0.1.0 | ✅ seamless textures (local SD1.5) |
| Skymap2D | 0.1.0 | ✅ equirectangular skymaps (local FLUX.1-dev + LoRA) |
| Text2Sound | 0.1.0 | ✅ text-to-audio (Stable Audio Open) |
| Terrain3D | 0.1.0 | ✅ AI terrain |
| Rocks3D | 0.1.0 | ✅ rochas procedurais (sem PyTorch) |
| AiGameKitLab | 0.1.0 | ✅ debug 3D, benches, profiling |
| Materialize | — | ✅ PBR maps (Rust/wgpu) |
| GameAssets | 0.2.2 | ✅ batch, handoff (OGG), dream, validate, manifest YAML |
| VibeGame | — | ✅ browser 3D engine (Three.js + bitecs) |
