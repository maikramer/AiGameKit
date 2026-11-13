# Findings — índice

Hub canónico: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

| Doc | Foco |
|-----|------|
| [UMS_VRAM_FINDINGS.md](UMS_VRAM_FINDINGS.md) | Admit, peak, waves, WAL, respawn, dead VRAM, testes |
| [UMS_CALIBRATION_RTX4050_6G.md](UMS_CALIBRATION_RTX4050_6G.md) | Footprint **medido** dos 10 backends numa GPU 6 GB (`vramd calibrate`); 4 subdimensionados, skymap2d não carrega |
| [MESH_PIPELINE_FINDINGS.md](MESH_PIPELINE_FINDINGS.md) | Master DAG Round 3, LOD0, promote/resume; **normais/tangentes** (prune keep-attrs, ktxdecompress); Decimate stepwise; V/Tri; árvores cut-only; compressão ([`../GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md)) |
| [HI3D_REFINE_FINDINGS.md](HI3D_REFINE_FINDINGS.md) | Hi3D v1.0 (SPARC3D) como stage `refine` default ON: 512³ em 6 GB com **residência por fase** (32 s quente / 60 s frio, 3,74 GiB; offload sequencial = 137 s), normalização do upstream, decimação+debris, `topology_clean` a abrir a malha, venv 3.10 + worker UMS local |
| [SDNQ_029_FINDINGS.md](SDNQ_029_FINDINGS.md) | SDNQ 0.2.9: **double quantization de escalas** (`scale_dtype`), codebook guarda `2^bits` níveis fp32 **por grupo** (inflacionava int3/int2 para 11/6 b/w), presets novos (`uint4-hq`, int3/int2 reais), dynamic medida e rejeitada; `aigamekit-lab bench sdnq` |
| [OCTREE_FACES_FINDINGS.md](OCTREE_FACES_FINDINGS.md) | Empírico simple-rpg: faces ≈ 8×10⁴·char_m²; κ·octree² (κ≈5.5); por categoria |
| [PAINT_PART_FINDINGS.md](PAINT_PART_FINDINGS.md) | Paint SDNQ/bake; Part3D; payload vramd |
| [IMAGE_SKY_SOUND_FINDINGS.md](IMAGE_SKY_SOUND_FINDINGS.md) | Text2D, Skymap, audio trim, kernels |
| [TEXTURE2D_SEAMLESS_QUALITY_FINDINGS.md](TEXTURE2D_SEAMLESS_QUALITY_FINDINGS.md) | Seamless 2.0: métrica continuidade (a antiga media igualdade), VAE tiling quebra costura, late-circular + roll, hires refine, heal |
| [TEXT2SOUND_SA3_LOOP_FINDINGS.md](TEXT2SOUND_SA3_LOOP_FINDINGS.md) | BGM seamless SA3: equal-power, comprimento exacto, mastering dobrado |
| [KERNEL_OPTS_FINDINGS.md](KERNEL_OPTS_FINDINGS.md) | compile / channels-last / flashvdm defaults |
| [ANIMATOR_RETARGET_FINDINGS.md](ANIMATOR_RETARGET_FINDINGS.md) | Quaternius retarget: loc_conv, `_bone_rest_dir`, QA; bipeds → `humanoid` (não `creature`); tabela HML22 |
| [ANIMATOR_IK_LIMITS_FINDINGS.md](ANIMATOR_IK_LIMITS_FINDINGS.md) | IK 2-ossos + limites de juntas: pole por eixo de flexão, identidade `W=Wp·Rp⁻¹·R·B`, subframe, maioria-voto; `ik-limits` CLI |
| [ANIMATOR_QUADRUPED_CLIPS_FINDINGS.md](ANIMATOR_QUADRUPED_CLIPS_FINDINGS.md) | Quadrúpedes: galope transverso (fases por pata, spine flex, voo), front/hind anatómico, death colapso terminal, hit flinch, attack com patas+lunge; Death≠chop / Hit≠mine |
| [MOTION3D_FINDINGS.md](MOTION3D_FINDINGS.md) | Text-to-motion **SprongMotion-14M** (CPU, runtime C): esqueleto 22 igual ao HML22 em índices, `joints` > `rotations` (erro 1,2 cm), rest do pelvis na origem → loc_scale 800×, perfil `sprong22`, `--attempts` e score mecânico |
| [PRECOMPUTE_COLLIDERS_FINDINGS.md](PRECOMPUTE_COLLIDERS_FINDINGS.md) | Colisores cápsula/cilindro pré-calculados (tronco); `aigamekit-lab precompute` → `gameassets_handoff.json` (consumo no runtime); carve procedural |
| [TEXT2D_FLUX3_KLEIN_MIGRATION.md](TEXT2D_FLUX3_KLEIN_MIGRATION.md) | Text2D → FLUX.3 Image Klein: ModularPipeline (diffusers 0.42), pop do prompt upsampler, refs/boxes/silhueta, steps 4-16 sem guidance, footprint `flux3-klein`, `load_keys`/`shape_keys` no vramd |
| [TEXT2SVG_MODEL_RESEARCH.md](TEXT2SVG_MODEL_RESEARCH.md) | text2svg: StarVector-1B vs OmniSVG vs VTracer; `--svg`/`vectorize`; vendor inference-only no transformers 5.x; backend `text2svg` + footprint `starvector-1b` |
| [MODEL_CONSOLIDATION_STUDY.md](MODEL_CONSOLIDATION_STUDY.md) | Inventário de modelos/variantes (~166 GB HF) + cortes por tier (~44 GB: órfãos Sana/t5-base/T2MGPT, SA Open legado, Disty0 klein, HY-Motion full) e unificações de vendor (Real-ESRGAN, bg-remover, aliases) |
| [TOOLKIT_CORE_PROFILE_STUDY.md](TOOLKIT_CORE_PROFILE_STUDY.md) | Uso real das 20 tools nos exemplos: Part3D/Motion3D/Intrinsic não usados (~42 GB); perfis install.sh `core|examples|all`; doc-bug "parts auto-detect" |

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
| [`../TESTING.md`](../TESTING.md) · [`../TESTING_PT.md`](../TESTING_PT.md) | Piso de cobertura (≥100/tool), suites `*coverage*`, anti-padrões, **armadilhas CI** (SIGILL, softfill sem Text3D) |
