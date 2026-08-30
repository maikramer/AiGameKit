# Quaternius Universal Animation Library — Inventory

Packs (ambos CC0, servidos pelo itch.io, fetch anónimo sem login):

1. **Universal Animation Library [Standard]** (UAL1) — 43 animações.
   Source: https://quaternius.itch.io/universal-animation-library
   Lock: `Shared/src/aigamekit_shared/data/quaternius.lock.json` (pack `quaternius`)
2. **Universal Animation Library 2 [Standard]** (UAL2) — 43 animações complementares.
   Source: https://quaternius.itch.io/universal-animation-library-2
   Lock: `Shared/src/aigamekit_shared/data/quaternius2.lock.json` (pack `quaternius2`)

Os tiers pagos (Pro $9.99 / Source $14.99) contêm as 120+/130+ animações
completas e exigem compra no itch.io — fora do fluxo anónimo do fetcher.

## Download (UAL1)

- itch.io flow (no login required, CC0):
  1. `POST https://quaternius.itch.io/universal-animation-library/file/17958403?source=game_download&as_props=1`
     → JSON `{url: <signed Cloudflare R2 URL, 60s expiry>}`
  2. `GET <signed_url>` → `Universal Animation Library[Standard].zip` (15.9 MB)
- Stable IDs: `upload_id = 17958403`, `game_id = 3408034`
- sha256 (zip): `cc73fc4e495b82958207316596317a3f40b9fa38065bde1027937452da537724`

## Download (UAL2)

- Mesmo fluxo: `POST https://quaternius.itch.io/universal-animation-library-2/file/17958478?...`
- Stable IDs: `upload_id = 17958478`, `game_id = 4221776`
- sha256 (zip): `4008ea208a604773a2b2177d965f0f5d3195498b5bf838c3f5785d68e95f2a68` (18.7 MB)
- Estrutura idêntica (`Unreal-Godot/UAL2_Standard.glb`, `Unity/UAL2_Standard.fbx`,
  variantes `_RM`), **rig idêntico** ao da UAL1 (65 bones, naming UE5 mannequin)
- Bônus: `Female Mannequin/` com `Mannequin_F.glb` (variante feminina do rig)

## Uso no pipeline

```bash
# Fetch multi-pack (idempotente, cache ~/.cache/aigamekit/quaternius/)
animator3d game-pack hero_rigged.glb hero_animated.glb                  # UAL1 (default)
animator3d game-pack hero_rigged.glb hero_animated.glb --anim-pack both # UAL1 + UAL2
animator3d retarget-batch hero_rigged.glb out.glb --profile quaternius2 # só UAL2

# GameAssets: game.yaml
animator3d:
  anim_pack: both   # ou por-asset: animate: { anim_pack: both }
```

Em `--anim-pack both` a UAL1 corre primeiro (locomoção base) e a UAL2 depois;
keys da UAL2 com o mesmo nome limpo **substituem** as da UAL1 (ex.: `chop`
dedicado `TreeChopping_Loop` em vez do `Sword_Attack` genérico).

## Structure (UAL1)

```
Universal Animation Library[Standard]/
├── Unreal-Godot/
│   ├── UAL1_Standard.glb      (7.6 MB) ← preferred (glTF)
│   └── UAL1_Standard_RM.glb   (root-motion variant)
├── Unity/
│   ├── UAL1_Standard.fbx      (23.7 MB)
│   └── UAL1_Standard_RM.fbx   (root-motion variant)
├── README.txt
└── License.txt
```

All animations are embedded as tracks in a **single GLB/FBX** (not one file per clip).

## Source rig (Quaternius universal humanoid) — 65 bones

Naming convention: **UE5 Mannequin style** (`pelvis`, `spine_01`, `upperarm_l`, `calf_r`...).
This is the Unreal Engine humanoid skeleton naming, NOT Mixamo.

```
root
└── pelvis
    ├── spine_01 → spine_02 → spine_03
    │   ├── neck_01 → Head
    │   ├── clavicle_l → upperarm_l → lowerarm_l → hand_l
    │   │   ├── index_01_l → 02_l → 03_l → 04_leaf_l
    │   │   ├── middle_01_l ... 04_leaf_l
    │   │   ├── pinky_01_l ... 04_leaf_l
    │   │   ├── ring_01_l ... 04_leaf_l
    │   │   └── thumb_01_l ... 04_leaf_l
    │   ├── clavicle_r → upperarm_r → lowerarm_r → hand_r
    │   │   └── (mirror of left hand)
    │   ├── thigh_l → calf_l → foot_l → ball_l → ball_leaf_l
    │   └── thigh_r → calf_r → foot_r → ball_r → ball_leaf_r
```

## Animations available

### UAL1 (43)

Locomotion: `Idle_Loop`, `Walk_Loop`, `Walk_Formal_Loop`, `Jog_Fwd_Loop`, `Sprint_Loop`,
`Crouch_Idle_Loop`, `Crouch_Fwd_Loop`
Jump: `Jump_Start`, `Jump_Loop`, `Jump_Land`
Roll: `Roll`
Combat: `Sword_Idle`, `Sword_Attack`, `Punch_Jab`, `Punch_Cross`
Pistol: `Pistol_Idle_Loop`, `Pistol_Aim_*`, `Pistol_Reload`, `Pistol_Shoot`
Spell: `Spell_Simple_Enter/Exit/Idle/Shoot`
Hit/Death: `Hit_Chest`, `Hit_Head`, `Death01`
Swim: `Swim_Idle_Loop`, `Swim_Fwd_Loop`
Interact: `Interact`, `PickUp_Table`, `Fixing_Kneeling`
Sitting: `Sitting_Enter/Exit/Idle/Talking`
Social: `Idle_Talking_Loop`, `Dance_Loop`, `Idle_Torch_Loop`, `Driving_Loop`
TPose: `A_TPose` (reference bind pose — useful for retarget alignment)

### UAL2 (43, tier Standard gratuito)

Farming: `Farm_Harvest`, `Farm_PlantSeed`, `Farm_Watering`
Colheita: `TreeChopping_Loop` (chop DEDICADO — melhor que o swing genérico UAL1)
Espada: `Sword_Regular_A/B/C` (+`_Rec` de A/B), `Sword_Regular_Combo`,
`Sword_Heavy_Combo`, `Sword_Dash`, `Sword_Block`
Escudo: `Idle_Shield_Loop`, `Idle_Shield_Break`, `Shield_Dash`, `Shield_OneShot`
Melee: `Melee_Hook`, `Melee_Hook_Rec`, `OverhandThrow`, `Hit_Knockback`
Movimento: `ClimbUp_1m`, `Slide_Start/Loop/Exit`, `NinjaJump_Start/Idle_Loop/Land`,
`Walk_Carry_Loop`
Zombie: `Zombie_Idle_Loop`, `Zombie_Walk_Fwd_Loop`, `Zombie_Scratch`
Emotes/interação: `Consume`, `Yes`, `Idle_No_Loop`, `Chest_Open`, `LayToIdle`,
`Idle_Lantern_Loop`, `Idle_FoldArms_Loop`, `Idle_Rail_Loop`, `Idle_Rail_Call`,
`Idle_TalkingPhone_Loop`
TPose: `A_TPose`

Nota: a UAL2 free **não tem locomoção base** (idle/walk/run) — usar
`--anim-pack both` para o catálogo combinado (~80 clips únicos).

## Bone mapping: Quaternius → simple-rpg (Mixamo-like, no prefix)

The simple-rpg rigs (hero, goblin, wolf, ogre...) use Mixamo naming WITHOUT the
`mixamorig:` prefix: `Hips`, `Spine`, `Chest`, `UpperChest`, `LeftArm`, `LeftForeArm`, etc.

| Quaternius (source) | simple-rpg (target) | Notes |
|---|---|---|
| `root` | `root` (estático) | criado nos pés; **nunca** retargetar rotação (±90° Y↔Z) |
| `pelvis` | `pelvis` / `Hips` | location+rotation (bob); sem location o clip pende da cintura |
| `spine_01` | `Spine` | |
| `spine_02` | `Chest` | |
| `spine_03` | `UpperChest` | ogre has extra `Neck1`; map to `Neck` or skip |
| `neck_01` | `Neck` | |
| `Head` | `Head` | |
| `clavicle_l` | `LeftShoulder` | |
| `upperarm_l` | `LeftArm` | |
| `lowerarm_l` | `LeftForeArm` | |
| `hand_l` | `LeftHand` | |
| `clavicle_r` | `RightShoulder` | |
| `upperarm_r` | `RightArm` | |
| `lowerarm_r` | `RightForeArm` | |
| `hand_r` | `RightHand` | |
| `thigh_l` | `LeftUpLeg` | |
| `calf_l` | `LeftLeg` | |
| `foot_l` | `LeftFoot` | |
| `ball_l` | `LeftToeBase` | ogre missing `LeftToeBase` → skip |
| `thigh_r` | `RightUpLeg` | |
| `calf_r` | `RightLeg` | |
| `foot_r` | `RightFoot` | |
| `ball_r` | `RightToeBase` | |
| `index/middle/pinky/ring/thumb_*_l/r` | `LeftHandFinger*` | generic, not 1:1; usually skip (clips don't animate fingers heavily) |

## Clip selection for simple-rpg (mapping clean names → Quaternius tracks)

### Perfil standard (12 humanoides + inimigos): `quaternius.yaml`

O catálogo UAL1 completo está agora mapeado (todos os 42 tracks usáveis; só
`A_TPose` fica de fora). Clips core do jogo:

| Clean name (target) | Quaternius track | Used for |
|---|---|---|
| `idle` | `Idle_Loop` | idle / breathe |
| `walk` | `Walk_Loop` | walk |
| `run` | `Jog_Fwd_Loop` | chase / run (jog looks better than sprint for combat) |
| `sprint` | `Sprint_Loop` | optional fast chase |
| `jump` | `Jump_Start` | lunge / attack leap |
| `attack` | `Sword_Attack` | melee attack |
| `punch` | `Punch_Cross` | alt melee |
| `hit` | `Hit_Chest` | damage reaction |
| `death` | `Death01` | death (replaces `Fall`) |
| `roar` | `Dance_Loop` | boss victory (closest "emote"; Death01 alt) |
| `roll` | `Roll` | dodge / roll |
| `interact` | `Interact` | interact gesture |

Catálogo estendido (novos): `walkformal`, `crouchidle`, `crouchwalk`,
`swimidle`, `swim`, `jumpland`, `fall` (← `Jump_Loop`, pose airborne para o
estado de queda), `punchjab`, `hithead`, `pistolidle`, `aim`/`aimdown`/`aimup`,
`reload`, `shoot`, `spellstart`/`spellidle`/`spellcast`/`spellend`, `pickup`,
`fixing` (← `Fixing_Kneeling`), `push`, `talk`, `torch`, `dance`, `drive`,
`sitdown`/`sit`/`sittalk`/`situp`.

### Perfil hero (superset com armas/ferramentas): `quaternius-hero.yaml`

O hero usa fuzzy matching por substring (`findClipFuzzy` em `gltf-systems.ts`) para
seleccionar clips de arma/ferramenta. As keywords **não têm aliases** — o nome do
clip no GLB tem de conter literalmente a keyword. Clips de arma/ferramenta:

| Clean name | Quaternius track | Keyword do jogo | Quando |
|---|---|---|---|
| `sword` | `Sword_Attack` | `sword` | combate c/ espada (V) |
| `axe` | `Sword_Attack` | `axe` | combate c/ machado (V) |
| `spear` | `Sword_Attack` | `spear` | combate c/ lança (V) |
| `chop` | `Sword_Attack` (UAL1) / `TreeChopping_Loop` (UAL2) | `chop` | cortar madeira (J perto de árvore) |
| `mine` | `Fixing_Kneeling` | `mine` | minerar pedra (J perto de pedra) |
| `gather` | `Interact` | `gather` | interagir (F) |

A UAL1 não tem `axe`/`spear` dedicados — `Sword_Attack` serve como swing
genérico de arma branca; `Fixing_Kneeling` (agachado a bater) serve para
mineração. Com `--anim-pack both`, a UAL2 substitui `chop` pelo dedicado
`TreeChopping_Loop` e acrescenta `harvest`/`plant`/`water` (farming).

### Perfil UAL2: `quaternius2.yaml` (44 keys)

Farming (`harvest`, `plant`, `water`), `chop` dedicado, espada
(`sworda`/`b`/`c` + `_rec`, `swordcombo`, `swordheavy`, `sworddash`,
`swordblock`), escudo (`shield`, `shieldbreak`, `shielddash`, `shieldbash`),
melee (`hook`, `hookrec`, `throw`, `knockback`), movimento (`climb`,
`slidestart`/`slide`/`slideexit`, `ninjajump`/`ninjaair`/`ninjaland`,
`carrywalk`), zombie (`zombieidle`, `zombiewalk`, `zombiescratch`), emotes
(`eat`, `yes`, `no`, `chestopen`, `getup`, `lantern`, `foldarms`, `lean`,
`call`, `phone`).

## Notes

- `A_TPose` is the bind/reference pose — use for retarget T-pose alignment validation.
- `_RM` (root motion) variants NOT used — root motion conflicts with simple-rpg's
  AI-driven movement (creatures translate via Transform, not root motion).
- The GLB includes a mesh (the yellow mannequin) — we import it only to get the
  armature + animation tracks; the mesh is discarded (we bake onto the target rig).

## Pitfall: origem nos pés em repouso → cintura ao play

Sintoma clássico no viewer: inanimado OK (eixos nos pés); ao selecionar um clip,
eixos saltam para a cintura e os pés afundam no grid.

| Causa | Mitigação no pipeline |
|-------|------------------------|
| SkinTokens: `pelvis` como única raiz | `ensure_feet_root_bone` cria `root` estático em `(0,0,0)` |
| Quaternius anima `root` com quat ±90° (Y↔Z) | **`root` fora do `bone_map`** — nunca retargetar rotação/location do root |
| Só rotações no pelvis (sem location) | `_LOCATION_SRC_BONES = {pelvis}` — bob do gait |

Detalhe operacional + checklist QA:
[`findings/ANIMATOR_RETARGET_FINDINGS.md`](findings/ANIMATOR_RETARGET_FINDINGS.md).

GLBs gerados **com** `root` no map (23 bones no log) → regenerar com
`animator3d game-pack`. Não compensar com pós-processamento binário do GLB.

## Retarget strategy — axis correction com swing removal

**Ficheiro:** `Animator3D/src/animator3d/retarget.py`

O retarget transfere a **pose global (armature-space)** do source para o target,
osso a osso, com uma correcção fixa por osso que compensa diferenças de rest pose.

### O problema: T-pose vs A-pose

O rig do Quaternius usa **T-pose** (braços horizontais); os rigs do simple-rpg
(Rigging3D/SkinTokens) usam **A-pose** (braços a ~45° para baixo). Copiar o
`matrix_basis` (rotação local) directamente transfere o mesmo delta — mas aplicado
sobre rests diferentes produz o dobro do movimento. Resultado: no idle, os braços
do hero cruzavam o tronco em V.

### A solução: axis correction + swing removal

Para cada osso mapeado, pré-computa-se uma correcção fixa (uma vez, do rest pose):

```
swing = src_dir.rotation_difference(tgt_dir)   # arco mínimo entre direções de rest
correction = src_rest⁻¹ @ swing⁻¹ @ tgt_rest
```

- `src_dir`/`tgt_dir`: direção do osso (tail − head) no rest, em armature space
- `swing`: rotação que alinha a direção de rest do source com a do target
- Remover o swing (`swing⁻¹`) garante que a diferença T-pose/A-pose **não** é
  re-aplicada como delta — só o movimento real da animação é transferido

### Propagação analítica por frame

A pose global do target é propagada por frame num dict, **pais antes de filhos**
(ordenação topológica DFS). Isto evita ler `pose.bone.matrix` a meio do frame
(que seria stale — o depsgraph só re-avalia em `view_layer.update()`).

```
desired_pose = source_pose @ correction
target_basis = (parent_pose @ parent_rest⁻¹ @ target_rest)⁻¹ @ desired_pose
```

### Continuidade de quaternion

`make_compatible(prev)` é chamado frame a frame para evitar interpolação pelo
caminho longo (180° flip) — sem isto, o viewer mostra um "trambolhão" entre frames.

### Porquê não constraints?

Prototipámos `Copy Rotation` LOCAL (braços torcidos), `Copy Rotation` WORLD via
constraint + bake visual (funciona mas é lento — re-avalia depsgraph por frame),
delta em armature-space com parent desejado ("bola de carne"), e conjugação
(body deitado). A axis correction com swing removal é a única que produz resultado
correcto **e** é rápida (cálculo fixo por osso, não por frame).
