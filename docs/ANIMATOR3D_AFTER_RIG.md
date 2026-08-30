# Animator3D after Rigging3D

Happy path: rigged GLB → `animator3d game-pack` → animated GLB → (GameAssets) `text3d lod`.

Package docs: [`Animator3D/README.md`](../Animator3D/README.md).  
Retarget bugs / biped vs creature: [`findings/ANIMATOR_RETARGET_FINDINGS.md`](findings/ANIMATOR_RETARGET_FINDINGS.md).  
Text-to-motion → SkinTokens (`motion3d apply-rigged`, perfil `hml22`): [`findings/MOTION3D_FINDINGS.md`](findings/MOTION3D_FINDINGS.md).  
Clip inventory: [`quaternius_inventory.md`](quaternius_inventory.md).

---

## CLI (canonical)

```bash
# Catálogo de animações disponíveis nos packs UAL1/UAL2 (sem bpy/GPU/download):
animator3d list-animations            # tabela agrupada; --pack quaternius|quaternius2|both
animator3d list-animations --json     # máquina-legível (agentes/pipelines)

animator3d game-pack rigged.glb animated.glb --preset humanoid --force-preset \
  --clips idle,walk,run,jump,attack,hit,death
```

GameAssets batch/resume runs this **once** on `_intermediate/{id}_rigged.glb`
(Round 3 DAG), then LODs the animated mesh. Do not game-pack per LOD.

| Path | When | Clip names in GLB |
|------|------|-------------------|
| **Quaternius** (default `humanoid`) | Bipeds, humanoids, “fantasma” bipeds | `idle`, `walk`, `run`, `jump`, `attack`, `hit`, `death`, … |
| **Villager** (`--anim-pack villager`) | Trabalhos/crafting (arado, pesca, mining, martelo — Kevin Iglesias, FBX por clip, EULA free) | `plow`, `fish`, `gather`, `hammer`, `mineground`, `minewall`, … |
| **Procedural** (`creature` / `flying` / `--procedural`) | Non-humanoid / multi-limb | `Animator3D_BreatheIdle`, `Animator3D_Walk`, … |

VibeGame enemy scripts (`creature.ts`, `enemies/*.ts`) expect the **clean**
Quaternius names for bipeds. Procedural names need matching TS.

---

## Manifest (`game.yaml` / CSV)

`category: creature` means “enemy/NPC slot”, **not** “use animate preset creature”.

```yaml
# bipedal enemy / hero — correct
animate:
  preset: humanoid
  force_preset: true
  clips: idle,walk,run,jump,attack,hit,death

# wolf / worm / flyer — correct
animate:
  preset: creature   # or flying
  procedural: true
  force_preset: true
```

Without `force_preset: true`, `game-pack` may auto-switch humanoid → creature when
`HumanoidRig.is_humanoid` fails (wings, odd chains). After changing preset, purge
`_rigged*` + public lods and `gameassets resume`.

---

## Root / pivot

- Static `root` at feet; **do not** retarget Quaternius root rotation.
- Location gait on `pelvis` only.
- Details: findings doc (loc_conv, `_bone_rest_dir`).

---

## Related

| Doc | Role |
|-----|------|
| [`MONOREPO_GAME_PIPELINE.md`](MONOREPO_GAME_PIPELINE.md) | Folders, handoff, PlayerGLTF |
| [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md) | Round 3 promote/resume |
| [`ZERO_TO_GAME_AI.md`](ZERO_TO_GAME_AI.md) | Agent onboarding |
