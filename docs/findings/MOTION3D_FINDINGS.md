# Motion3D — findings (HML22 → SkinTokens)

Happy path e anti-padrões do bake Motius T2M-GPT sobre rigs SkinTokens.
Complementa [`ANIMATOR_RETARGET_FINDINGS.md`](ANIMATOR_RETARGET_FINDINGS.md)
(perfil `hml22`, Quaternius, `loc_conv`).

**Código:** `Motion3D/src/motion3d/bpy_export.py`, `apply_rigged.py` ·  
**CLI:** `motion3d generate` → `motion3d apply-rigged` ·  
**Perfil:** `Animator3D/src/animator3d/data/retarget/hml22.yaml`.

---

## Modelo mental

1. **GPU** gera `joints (T,22,3)` Y-up (HumanML3D / Motius).
2. **Motion3D** exporta um GLB *source* HML22 (armature só, nomes SkinTokens).
3. **Animator3D** copia rotações locais → mesh skinned do alvo.
4. Porque o retarget é absoluto em espaço-mundo, o **rest + aim do source**
   decide se o hero fica direito ou colapsado — não há “offset subtil”.

```
generate NPZ ──► export-glb / apply-rigged (HML22 source)
                         │
                         ▼
              animator3d.retarget --profile hml22
                         │
                         ▼
                   *_walk.glb skinned
```

---

## Contratos do source HML22

| Contrato | Constante / API | Nota |
|----------|-----------------|------|
| Pais HML | `HML22_PARENTS` | índice 0 = pelvis |
| Aim (tail) | `HML22_AIM_CHILD` | **não** derivar de `t2m_kinematic_chain` |
| Folhas | `HML22_LEAF_BONES` | Head / hands / balls → rest |
| Neutro soft | `HML22_NEUTRAL_AIM` | coluna/pescoço ↑, braços A-pose ~15° |
| Pés do alvo | `HML22_TARGET_REST_BONES` = `{foot_l,foot_r}` | só estes lêem rest do rig |
| Stance | `HML22_LEG_SPLAY_DEG` | ±6° anti-cruzamento de tornozelos |
| In-place | `stabilize_facing_zup` + strip XY do pelvis | clip loopável |

---

## Tabela de incidentes

| Sintoma | Causa | Fix |
|---------|-------|-----|
| Tronco/pescoço/braço colapsados de lado | aim derivado de `t2m_kinematic_chain` (3 cadeias em `spine_03` sobrescrevem → peito aponta à clavícula) | `HML22_AIM_CHILD` explícito (`spine_03→neck`) |
| Cabeça ~90° à frente | rest cru de `t2m_raw_offsets` (cabeça à frente do pescoço) | T-pose em `_canonical_rest_joints_yup` |
| Membros torcidos no próprio eixo | `to_track_quat` mete roll | swing-only: `rest_dir.rotation_difference(y) @ rest_quat` |
| Mãos torcidas / sapato de palhaço / nod duplo | folhas sem joint HML; estender direção do pai | `HML22_LEAF_BONES` no rest |
| Cabeça a olhar para o chão, braços colados | anatomia SMPL ≠ rig de jogo; retarget absoluto | `HML22_NEUTRAL_AIM` (mediana → neutro) |
| Braços abertos *depois* de corrigir pés | `target_rest_aims` em todos os ossos; rest SkinTokens dos braços = T-pose | só pés em `HML22_TARGET_REST_BONES` |
| Botas com pitch/toe-out errados | sample T2M assimétrico (ex. R 29° vs L 6°) | pés calibrados no rest do *alvo* |
| Botas a atravessar-se | clip em linha de passerelle; tornozelos cruzam | `HML22_LEG_SPLAY_DEG` |
| Herói sai do sítio / roda | travel + yaw drift do sample | `--in-place` |
| Clip “esticado” (116→139 frames) | scene Blender a 24 fps | `scene.render.fps = motion fps` antes do import |

---

## QA rápido (pós `apply-rigged`)

Por frame, em espaço-mundo do armature:

- `neck_01` / `Head` ≈ `+Z` (0.95–1.0)
- `upperarm_*` ≈ (±0.26, 0, −0.97) — **não** (±0.9, 0, −0.4) T-pose
- `foot_*` pitch/toe-out ≈ rest do alvo (hero tip. −34° / ±21°)
- tornozelos: sep X > 0 em todos os frames
- pelvis XY ≈ 0 com `--in-place`; bob Z ~cm

Screenshots: `aigamekit-lab debug screenshot hero_walk.glb -o shots/`.

---

## Integração monorepo

| Peça | Papel |
|------|--------|
| `./install.sh motion3d` | venv + `[dev]` + PTH Animator3D |
| UMS backend `motion3d` | só **generate** (GPU) |
| `GameAssets.ums_batch.run_motion3d_wave_or_fallback` | wave NPZ/GLB source |
| `motion3d apply-rigged` | CPU, pós-wave; dono do skinned |
| `make test-motion3d` | suite no `Motion3D/.venv` |

**Anti-padrão:** bake de *localizações* de joints no mesh SkinTokens; reimplementar retarget em Motion3D; calibrar braços no rest T-pose do alvo.

---

## Changelog

| Data | Nota |
|------|------|
| 2026-08-02 | Aim explícito, folhas no rest, A-pose neutro, pés do alvo, leg splay, `--in-place` |
| 2026-08-02 | Regressão braços abertos: `HML22_TARGET_REST_BONES` = só pés |
