# Isometric Camera Plugin

<!-- LLM:OVERVIEW -->

Fixed-angle orthographic camera for isometric games. Pitch is pinned at
`atan(1/√2)` (35.264°), yaw is locked to four 90°-apart quadrants rotated with
Q/E, and the wheel zooms the orthographic frustum. Follows a target with lag.
Opt-in — **not** in `DefaultPlugins`.
<!-- /LLM:OVERVIEW -->

## Purpose

A game whose art and gameplay assume one viewing angle (a tile grid, hand-placed
props, a farm laid out in metres) needs the angle to actually stay put.
`OrbitCamera` can be coaxed close by pinning `min-pitch === max-pitch`, but its
yaw cannot be clamped at all and its zoom is a `dolly()` on a perspective rig,
which is a different thing from an orthographic frustum height.

## Usage

```ts
withPlugin(IsometricCameraPlugin);
```

```html
<!-- merge:true — must stay top-level, never inside a <Group> -->
<IsometricCamera
  ortho-size="16"
  distance="70"
  min-ortho-size="9"
  max-ortho-size="40"
  follow-lag="0.14"
  turn-lag="0.22"
  offset-y="0.9"
></IsometricCamera>
```

`PlayerCameraLinkingSystem` binds the first unbound isometric camera to the
player automatically, so no `target=` is needed in the common case.

## Controls

| Input | Effect |
| --- | --- |
| `Q` / `E` | Rotate the view 90° (one step per press, smoothed) |
| Wheel | Zoom — changes `MainCamera.orthoSize` |

`Q` is taken. A game that also wants `Q` for a pause modal has to move the modal
(`simple-farm` uses `key="escape"`).

## Design notes

**Why orthographic works without a runtime toggle.** `createThreeCamera` picks
`OrthographicCamera` vs `PerspectiveCamera` once, at construction, and
`syncCameraSettings` cannot switch the type afterwards. The recipe therefore
declares `main-camera.projection: 1` in `overrides`, which is applied when the
entity is created — before the rendering plugin ever builds the THREE camera.

**Why yaw accumulates instead of wrapping.** `targetYaw` is unwrapped: Q adds
`+π/2` forever, E subtracts it. Deriving the target from `yawIndex * π/2` would
wrap it into `[-π, π]`, and the 3 → 0 step would then read as −270° — a full
spin the wrong way. The smoothing itself uses `shortestAngleDelta`, so the
visible sweep is always the 90° short path.

**Why the zoom is written in `simulation`.** `MainCamera.orthoSize` is read by
`CameraSyncSystem` in the `draw` group. Writing it from a system ordered *after*
`CameraSyncSystem` would apply each wheel notch a frame late.

**Why the pose is written in `draw`, after `CameraSyncSystem`.** Same reason as
`ThirdPersonCameraSystem`: this system has to be the sole authority over the
THREE camera transform, or the two fight and the view jitters every frame.

**Shadows.** The directional-light shadow box is only ~32 m across and is
normally centred on the third-person target, or — failing that — on the camera
itself, biased forward. An orthographic rig stands ~70 m back, so that fallback
would leave the subject outside the box entirely and it would cast no shadow.
The pose system therefore calls `setShadowFocusEntity(state, target)`
(`rendering/shadow-focus.ts`), which takes priority over the whole chain.

**Movement stays screen-relative** with no extra work: `resolveCameraYaw` in the
player plugin returns this camera's smoothed yaw, and with no `ThirdPersonCamera`
in the scene `PlayerMovementSystem` takes the strafe branch — so A/D strafe,
W/S run along the view axis, and 8-way movement follows the camera through a
Q/E rotation. The hero keeps facing its last heading when idle
(`resolveMouseMode` is pinned to mode 1), which is the Harvest-Moon behaviour a
facing-tile interaction needs.

## Files

| File | Contents |
| --- | --- |
| `components.ts` | `IsometricCamera` |
| `logic.ts` | Pure helpers: `snapYawIndex`, `rotateYawOnEdge`, `applyZoomInput`, `smoothZoom`, `isometricEyeOffset`, `ISO_PITCH` |
| `systems.ts` | `IsometricCameraInputSystem` (simulation), `IsometricCameraSystem` (draw) |
| `recipes.ts` | `<IsometricCamera>` |
| `plugin.ts` | `IsometricCameraPlugin` + defaults |

The DOM-free half lives in `logic.ts` because `isKeyDown` only reports keys once
a canvas has focus — the rotation and zoom rules have to stay testable headless.

## Related

- [`../orbit-camera/context.md`](../orbit-camera/context.md) — free-look orbit rig
- [`../player-controller/context.md`](../player-controller/context.md) — third-person rig
- [`../rendering/context.md`](../rendering/context.md) — `MainCamera`, shadow frustum

<!-- LLM:EXAMPLES -->

```ts
import {
  IsometricCamera,
  IsometricCameraPlugin,
  ISO_PITCH,
  withPlugin,
} from 'vibegame';

withPlugin(IsometricCameraPlugin);

// Lock the view completely (no Q/E), looking straight down a quadrant.
IsometricCamera.allowRotate[cam] = 0;

// Frame a fixed number of metres vertically.
IsometricCamera.targetOrthoSize[cam] = 12;

console.log((ISO_PITCH * 180) / Math.PI); // 35.26438968275465
```

<!-- /LLM:EXAMPLES -->
