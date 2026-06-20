# player-controller plugin

**This plugin is a third-person *camera* rig, not the player movement controller.**

The name is historical and misleading. What lives here:

- `ThirdPersonCamera` component + `thirdPersonCameraRecipe`
- `ThirdPersonCameraSystem` — follow/orbit-behind camera with terrain-aware
  distance, smoothing, mouse look.
- `PlayerCameraLinkingSystem` — binds the first unlinked `ThirdPersonCamera` to
  the first player and ensures the camera has an `InputState` (so mouse-look
  works).

## Relationship to the `player` plugin

The actual character controller — movement, grounding, GLTF model/animation —
lives in the sibling **`player`** plugin (`PlayerPlugin`), via the
`PlayerController` component. Use that for the player itself.

Both plugins ship in `DefaultPlugins`. Reach for:

- `PlayerPlugin` — you want a controllable character.
- `PlayerControllerPlugin` — you want the third-person follow camera.

## Known overlap (do not "fix" blindly)

Both plugins export a system named `PlayerCameraLinkingSystem`, and both write
`ThirdPersonCamera.target`:

- `player/systems.ts` links either a `ThirdPersonCamera` *or* an `OrbitCamera`,
  gated on `PlayerController.cameraEntity`.
- `player-controller/systems.ts` links only `ThirdPersonCamera` and **also adds
  `InputState` to the camera** (the `player` version does not).

When both run, whichever links first wins; the InputState side effect only
happens if the `player-controller` system reaches the camera while
`ThirdPersonCamera.target === 0`. Consolidating these into one linking system is
the right long-term cleanup, but it changes camera-input wiring and must be
verified in-game (mouse look, orbit fallback) before merging — it is not a safe
mechanical refactor.
