# Findings — índice

Hub canónico: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

| Doc | Foco |
|-----|------|
| [UMS_VRAM_FINDINGS.md](UMS_VRAM_FINDINGS.md) | Admit, peak, waves, WAL, respawn, dead VRAM, testes |
| [MESH_PIPELINE_FINDINGS.md](MESH_PIPELINE_FINDINGS.md) | Master DAG Round 3, LOD0, promote/resume; **normais/tangentes** (prune keep-attrs, ktxdecompress); Decimate stepwise; V/Tri; árvores cut-only; compressão ([`../GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md)) |
| [OCTREE_FACES_FINDINGS.md](OCTREE_FACES_FINDINGS.md) | Empírico simple-rpg: faces ≈ 8×10⁴·char_m²; κ·octree² (κ≈5.5); por categoria |
| [PAINT_PART_FINDINGS.md](PAINT_PART_FINDINGS.md) | Paint SDNQ/bake; Part3D; payload UMS |
| [IMAGE_SKY_SOUND_FINDINGS.md](IMAGE_SKY_SOUND_FINDINGS.md) | Text2D, Skymap, audio trim, kernels |
| [VIBEGAME_AUDIO_COMBAT_FINDINGS.md](VIBEGAME_AUDIO_COMBAT_FINDINGS.md) | Cull espacial, profiler Audio, SFX longos, melee impact 0.35 |
| [VIBEGAME_SPAWN_GROUND_FINDINGS.md](VIBEGAME_SPAWN_GROUND_FINDINGS.md) | Chão: estáticos AABB; creatures CCT; anti-settle / anti-fudge Y |
| [KERNEL_OPTS_FINDINGS.md](KERNEL_OPTS_FINDINGS.md) | compile / channels-last / flashvdm defaults |
| [ANIMATOR_RETARGET_FINDINGS.md](ANIMATOR_RETARGET_FINDINGS.md) | Quaternius retarget: loc_conv, `_bone_rest_dir`, QA; bipeds → `humanoid` (não `creature`); tabela HML22 |
| [MOTION3D_FINDINGS.md](MOTION3D_FINDINGS.md) | Text-to-motion → SkinTokens: `apply-rigged`, aim/rest/folhas, neutro A-pose vs pés do alvo, in-place, venv |
| [PRECOMPUTE_COLLIDERS_FINDINGS.md](PRECOMPUTE_COLLIDERS_FINDINGS.md) | Colisores cápsula/cilindro pré-calculados (tronco); `aigamekit-lab precompute` → `gameassets_handoff.json` → `PrecomputePlugin`; carve procedural |

**Relacionados (fora desta pasta):**

| Doc | Foco |
|-----|------|
| [`../ANIMATOR3D_AFTER_RIG.md`](../ANIMATOR3D_AFTER_RIG.md) | Happy path game-pack; Quaternius vs procedural; manifest bipeds |
| [`../GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md) | Happy path waves + MasterDeferQueue |
| [`../OMNI_SHAPE_FINDINGS.md`](../OMNI_SHAPE_FINDINGS.md) | Omni bbox/pose/softfill |
| [`../MANIFEST_AUTHORING.md`](../MANIFEST_AUTHORING.md) | Manual `manifest.yaml` (size_m, Omni, quando override octree) |
| [`../UMS_SUBPROCESS_PLAN.md`](../UMS_SUBPROCESS_PLAN.md) | Subprocess workers (estado actual) |
| [`../mission/`](../mission/README.md) | Missão / premissas do monorepo |
| [`../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md) | Lições forma / Part3D |
| [`../GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md) | Happy path `text3d finish` / KTX2+meshopt (deps `ktx` + npx) |
| [`../TESTING.md`](../TESTING.md) · [`../TESTING_PT.md`](../TESTING_PT.md) | Piso de cobertura (≥100/tool), suites `*coverage*`, anti-padrões, **armadilhas CI** (SIGILL, softfill sem Text3D, flakes Bun) |
