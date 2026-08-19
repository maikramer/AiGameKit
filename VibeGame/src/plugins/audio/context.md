# Audio Plugin

<!-- LLM:OVERVIEW -->

Howler.js-based audio: named sound bank, routing buses, GLTF clip markers,
stereo-pan spatial emitters with distance cull, layered music mixing wired to
RPG battle events, and a debug bridge + profiler Audio tab for attribution.
<!-- /LLM:OVERVIEW -->

## Purpose

- Declarative `<AudioSource>` / `<AudioMixer>` / `<MusicLayer>` recipes
- Global sound bank (`defineSoundBank`, `playSound`, `playSoundAt`, `playSoundOn`)
- Master + per-bus volume (music, sfx, ui)
- Animation clip sound markers (normalized time triggers)
- Spatial audio via Howler stereo panning (no HRTF) + **maxDistance cull**
- QA: `window.__VIBEGAME__.audio`, ring log with `origin` / preload vs gameplay

## Layout

```
audio/
├── context.md       # This file
├── bank.ts          # Sound bank, buses, clip markers, spatial cull
├── bridge.ts        # __VIBEGAME__.audio handle (Playwright / AI)
├── debug-log.ts     # Ring buffer + snapshot for profiler Audio tab
├── components.ts    # AudioSource, AudioListener, MusicLayerComponent
├── mixer.ts         # AudioMixer recipe, music layer crossfade
├── plugin.ts        # AudioPlugin registration
├── recipes.ts       # audioClipRecipe
├── sfx-registry.ts  # Named SFX resolver (XML sound="key")
└── systems.ts       # AudioSystem, SoundBankSystem, listener sync
```

## Buses

Volume routing is **master × bus × clip**:

| Bus      | Default     | Typical use                                    |
| -------- | ----------- | ---------------------------------------------- |
| `master` | 1.0         | Global gain (`setMasterVolume`)                |
| `music`  | 0.7         | Background / layered tracks (`setMusicVolume`) |
| `sfx`    | 0.8         | One-shots, footsteps, UI (`setSfxVolume`)      |
| `ui`     | (per sound) | Optional per-key bus in `SoundDef`             |

`AudioMixer` XML sets initial bus levels; runtime helpers live in `mixer.ts` (`getAudioMix`, `setBusVolume` in `bank.ts`).

## Sound bank

- `defineSoundBank({ key: { url, bus?, volume?, spatial?, maxDistance?, … } })`
- `preloadSounds()` — silent cache warm (`kind: preload` in debug log; not gameplay).
  **Browser:** queues keys until `allowSoundPreload()` so Howler does not create
  an `AudioContext` before a user gesture (autoplay policy warning).
- `allowSoundPreload()` — unlock + flush queued preloads (called from
  `resumeAudioContextOnFirstUserGesture` on first `pointerdown`)
- **Separate Howl caches** for 2D vs spatial — never reuse a 2D preload Howl for `playSoundAt`
- `PlayOptions.originEid` / `origin` — profiler attribution (`topOrigins`)

### Autoplay / user gesture

- Prefer `<Scene resume-audio-on-user-gesture="true">` (runtime wires the listener).
- Gesture path: `allowSoundPreload()` → optional silent-WAV Howl if `Howler.ctx`
  still null → `resumeAudioContextIfSuspended()`.
- Headless / no-DOM: preload allowed immediately (tests, CI).

### Spatial cull

Listener world pose is published each frame. `playSoundAt` / follow plays beyond
`maxDistance` (default **40**) are skipped (`kind: skip`, `detail: cull>Nm`) so
far entities do not leak ghost SFX.

## Clip markers

Attach synchronized one-shots to GLTF animation clips via `addClipSound(clipName, marker)`:

- `marker.at` — normalized time 0..1 on the clip timeline
- `marker.sound` — bank key to fire when the playhead crosses `at`

`SoundBankSystem` tracks per-entity normalized clip time and calls `fireClipMarkers`
on crossings (including loop wrap). Markers are global per clip name, not per entity.

**Caveat:** PlayerGLTF attack uses `playOverride`; game melee SFX often schedule
from clip duration × impact fraction instead of markers alone. See
[`docs/AUDIO.md`](../../../docs/AUDIO.md#attack-impact-timing-melee--harvest) and
`player/gltf-systems.ts` (`ATTACK_IMPACT_FRACTION`).

## Spatial audio

`AudioSource.spatial` and bank `spatial: true` use **Howler stereo panning only** —
there is **no HRTF / Web Audio PannerNode HRTF model**. Distance attenuation uses
Howler `pannerAttr` (ref distance, max distance, rolloff). Listener position
follows the main camera / listener `WorldTransform` each frame.

## Battle music (RpgCore)

`RpgCorePlugin` calls `wireMusicMixerEvents(state)` (`rpg-core/music-mixer-bridge.ts`), which subscribes to:

- `music:enter-battle` → sets `getAudioMix(state).activeLayer = MUSIC_LAYER_BATTLE`
- `music:exit-battle` → sets active layer back to `MUSIC_LAYER_EXPLORE`

`MusicMixerSystem` reads `activeLayer` and fades `<MusicLayer>` entities. Emit battle transitions from gameplay via `emitEvent(state, MUSIC_ENTER_BATTLE)` / `MUSIC_EXIT_BATTLE` (exported from `mixer.ts`).

## Debug / profiler

- Bridge: `bridge.ts` → `__VIBEGAME__.audio` (`snapshot`, `clearLog`, `playSound`, `stopAll`, listener pos)
- Ring log: `debug-log.ts` — `preload` vs gameplay; stacks when Audio tab armed
- URL: `?profiler=audio` or `?profilerTab=audio` (see `profiler/url.ts`)
- E2E: `playwright/audio-spatial.spec.ts`

## Entry points

- **Plugin**: `AudioPlugin` from `plugin.ts`
- **Bank**: `defineSoundBank`, `playSound`, `playSoundAt`, `playSoundOn`, `preloadSounds`, `allowSoundPreload` from `bank.ts`
- **Gesture**: `resumeAudioContextOnFirstUserGesture` / `resumeAudioContextIfSuspended` from `systems.ts`
- **Mixer**: `playMusicLayer`, `crossfadeMusicLayers`, `getAudioMix` from `mixer.ts`
- **Bridge**: `installAudioBridge` / `ensureAudioBridge` from `bridge.ts`
- **RPG bridge**: `wireMusicMixerEvents` from `rpg-core` (not re-exported from audio index)

## Dependencies

- **Internal**: `rendering` (MainCamera), `transforms`, `gltf-anim` (clip time for markers), `profiler` (Audio tab UI)
- **External**: howler

## See also

- Full guide: [`docs/AUDIO.md`](../../../docs/AUDIO.md)
- SFX generation pitfalls: [`docs/findings/IMAGE_SKY_SOUND_FINDINGS.md`](../../../../docs/findings/IMAGE_SKY_SOUND_FINDINGS.md)
- Melee swing timing (simple-rpg): `examples/simple-rpg/src/game/melee.ts`
