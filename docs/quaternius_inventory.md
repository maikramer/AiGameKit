# Quaternius Universal Animation Library — Inventory (Fase 0)

Pack: **Universal Animation Library [Standard]** by Quaternius
License: **CC0 1.0** (Public Domain)
Source: https://quaternius.itch.io/universal-animation-library

## Download

- itch.io flow (no login required, CC0):
  1. `POST https://quaternius.itch.io/universal-animation-library/file/17958403?source=game_download&as_props=1`
     → JSON `{url: <signed Cloudflare R2 URL, 60s expiry>}`
  2. `GET <signed_url>` → `Universal Animation Library[Standard].zip` (15.9 MB)
- Stable IDs: `upload_id = 17958403`, `game_id = 3408034`
- sha256 (zip): `cc73fc4e495b82958207316596317a3f40b9fa38065bde1027937452da537724`

## Structure

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

## Animations available (43)

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

## Bone mapping: Quaternius → simple-rpg (Mixamo-like, no prefix)

The simple-rpg rigs (hero, goblin, wolf, ogre...) use Mixamo naming WITHOUT the
`mixamorig:` prefix: `Hips`, `Spine`, `Chest`, `UpperChest`, `LeftArm`, `LeftForeArm`, etc.

| Quaternius (source) | simple-rpg (target) | Notes |
|---|---|---|
| `root` | (skip) | root offset node, no deform |
| `pelvis` | `Hips` | |
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

Core clips needed by the game (`idle/walk/run/jump/attack/death/roar`):

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

Extra clips worth including for richness (emotes, situational):
`Crouch_Idle_Loop`, `Roll`, `Interact`, `Spell_Simple_Shoot`, `Idle_Talking_Loop`,
`Idle_Torch_Loop`, `Push_Loop`, `Fixing_Kneeling`.

## Notes

- `A_TPose` is the bind/reference pose — use for retarget T-pose alignment validation.
- `_RM` (root motion) variants NOT used — root motion conflicts with simple-rpg's
  AI-driven movement (creatures translate via Transform, not root motion).
- The GLB includes a mesh (the yellow mannequin) — we import it only to get the
  armature + animation tracks; the mesh is discarded (we bake onto the target rig).
- Retarget strategy: **Copy Rotation** constraint (LOCAL owner/target space) per
  mapped bone + visual-keying bake. This preserves the target's rest pose and is
  robust to orientation differences (UE5 Mannequin vs Mixamo bone rolls differ).
