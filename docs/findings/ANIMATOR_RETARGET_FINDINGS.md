# Animator3D retarget (Quaternius) — descobertas

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Inventário pack: [`../quaternius_inventory.md`](../quaternius_inventory.md).  
Código: `Animator3D` `retarget.py` · clips via `animator3d game-pack`.

---

## Dois bugs de eixo (corrigir na raiz — não em pós GLB)

### 1. `pose.bone.location` no frame de REST do osso

Frames de REST diferem entre rigs (ex. pelvis Quaternius ≈ +104° X vs target
identidade). Copiar componentes cruas troca vertical↔horizontal (agachamento
vira deslocamento para trás; pés “flutuam”).

**Fix:** converter com `tgt_rest⁻¹ @ src_rest @ location` (`loc_conv`).

### 2. Swing correction sem confiar no tail glTF

O importador glTF sintetiza tails com heurística ruidosa (pelvis pode sair
horizontal/para baixo → pose roda 90–180°, “bunda empinada”).

**Fix:** `_bone_rest_dir` — geometria do esqueleto: filho de tronco mapeado →
média dos filhos mapeados → direção desde o pai → tail só como último recurso.

---

## QA numérico (sem bpy)

Por keyframe, comparar com o source:

| Métrica | Idle tip. | Run tip. | Jump crouch tip. |
|---------|----------:|---------:|-----------------:|
| `lean` tronco (pelvis→neck_01) | ~7° | ~26° | — |
| `feetY` / pelvis | pés no chão | — | pelvis ≈ 0.47 |

---

## Intermediários legados

`*_rigged_animated` / `*_lodN_animated` gerados **antes** destes fixes podem ter
`root` −90°X ou canais corrompidos → **regenerar**. Não fazer bridge/reuso
binário para “corrigir”.

---

## Pipeline Round 3

`gameassets` corre `animator3d game-pack` **×1** no rigged painted, depois
`text3d lod` (preserva clips). Ver
[`MESH_PIPELINE_FINDINGS.md`](MESH_PIPELINE_FINDINGS.md).

---

## Preset humanoid vs creature (manifest)

`category: creature` no manifesto = “inimigo/NPC”, **não** “rig não-humanoide”.
Default de categoria → `animate.preset: humanoid` (mocap Quaternius).

| Asset típico | Manifest `animate:` | Resultado |
|--------------|---------------------|-----------|
| bandit, goblin, shade, bosses bípedes | `preset: humanoid` + `force_preset: true` + clips `idle,walk,run,jump,attack,hit,death` | Ossos `pelvis`/`upperarm_*`… + clips com esses nomes |
| lobo, verme, escorpião, voadores | `preset: creature\|flying` + `procedural: true` + `force_preset: true` | Clips `Animator3D_*` — alinhar script TS aos nomes |

**Incidente shade (2026-07):** `preset: creature` + `procedural: true` → SkinTokens
gerou asa/`RightWing` (65 bones) e clips `Animator3D_BreatheIdle` etc. Script
`enemies/shade.ts` pedia `idle`/`walk` → sem animação. Fix: humanoid +
`force_preset` + re-rig painted → game-pack → LOD. Validate KTX2/meshopt pode
falhar sem `ktx` — irrelevante p/ clips.

Sem `--force-preset`, `game-pack` auto-desvia para `creature` se o gate
`HumanoidRig.is_humanoid` falhar (rig com asas/multi-pernas). Bípedes com mesh
“fantasma/capa” precisam `force_preset: true` **e** re-rig limpo após mudar o
manifest.

Regenerar só um asset:

```bash
# apagar _intermediate/{id}_rigged*.glb + lods públicos do id
gameassets resume --manifest manifest_<id>_only.yaml --profile game.yaml
aigamekit-lab debug inspect meshes/{id}_lod0.glb   # anims: idle,walk,…
```

---

## Motion3D (HML22) → SkinTokens: o rest do source manda

Doc canónico (tabela completa + QA + integração): [`MOTION3D_FINDINGS.md`](MOTION3D_FINDINGS.md).

`motion3d apply-rigged` constrói um GLB source HML22 (`bpy_export.py`) e deixa o
retarget ao Animator3D (perfil `Animator3D/src/animator3d/data/retarget/hml22.yaml`).
Porque o retarget transfere rotações **locais**, qualquer desalinhamento entre o
rest do source e o rest do alvo aparece como membro colapsado, não como offset
subtil:

| Sintoma no hero | Causa | Fix |
|-----------------|-------|-----|
| Tronco/pescoço/cabeça/braço esquerdo colapsados de lado | aim (tail de cada osso) derivado de `t2m_kinematic_chain`: as 3 cadeias que arrancam em `spine_03` sobrescrevem-se → `spine_03` apontava à `clavicle_l` | mapa explícito `HML22_AIM_CHILD` |
| Cabeça dobrada ~90° para a frente | rest vindo cru dos `t2m_raw_offsets`: `offsets[15] = [0,0,1]` põe a cabeça **à frente** do pescoço → rest do `neck_01` para a frente, e o filho herda a compensação | forçar T-pose no rest: cabeça `+Y`, braços `±X` (`_canonical_rest_joints_yup`) |
| Membros torcidos ao longo do próprio eixo | `Vector.to_track_quat` mete roll arbitrário | swing-only: `rest_dir.rotation_difference(dir) @ rest_quat` |
| Mãos torcidas, dedos do pé enrolados («sapato de palhaço»), nod duplo na cabeça | folhas (`Head`, `hand_*`, `ball_*`) não têm joint HML para apontar; estender a direção do pai inventa pose | `HML22_LEAF_BONES` ficam no rest — o pai carrega o movimento |
| Cabeça a olhar para o chão, braços colados ao torso | anatomia SMPL ≠ rig de jogo (pescoço→cabeça ~28° à frente, braços na vertical) e o retarget copia direções em **mundo** | rodar a mediana do clip para um neutro antes do swing (calibrar contra o rest do *source* não serve — o retarget é absoluto) |
| Botas com pitch errado / pé direito virado para fora | o sample T2M é assimétrico: 29° de toe-out à direita vs 6° à esquerda | só os **pés** vêm do rest do rig alvo (`HML22_TARGET_REST_BONES`); braços/coluna ficam no A-pose soft de `HML22_NEUTRAL_AIM` |
| Braços abertos outra vez após corrigir os pés | `target_rest_aims` em todos os ossos: rest SkinTokens dos braços = T-pose (~horizontal) | nunca calibrar braços no rest do alvo — só `foot_l`/`foot_r` |
| Botas a atravessar-se uma na outra | o clip anda em linha de passerelle — tornozelos cruzam até 8 cm em 17/116 frames | `HML22_LEG_SPLAY_DEG` (±6° por perna) alarga a base sem tocar no timing |
| Herói sai do lugar / roda no sítio | translação do pélvis + yaw drift do sample T2M | `--in-place` (`stabilize_facing_zup` + strip do travel) |

QA rápido (dirs de osso em mundo, por frame) chega para apanhar isto: cabeça e
pescoço devem ter direções quase iguais e ~`+Z`; coxas alternam `±Y` ao longo do
ciclo; `pelvis` só oscila em Z (~4 cm).

---

## Changelog

| Data | Nota |
|------|------|
| 2026-08-02 | Motion3D HML22: aim explícito + rest T-pose (cabeça/braços) |
| 2026-07-24 | Bípedes creature→humanoid Quaternius; incidente shade; force_preset |
| 2026-07-24 | Extraído de AGENTS.md learned facts (loc_conv + `_bone_rest_dir`) |
