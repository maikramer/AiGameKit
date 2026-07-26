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
gamedev-lab debug inspect meshes/{id}_lod0.glb   # anims: idle,walk,…
```

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Bípedes creature→humanoid Quaternius; incidente shade; force_preset |
| 2026-07-24 | Extraído de AGENTS.md learned facts (loc_conv + `_bone_rest_dir`) |
