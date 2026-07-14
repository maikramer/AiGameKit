# Audio Plugin

<!-- LLM:OVERVIEW -->

Howler.js-based audio: named sound bank, routing buses, GLTF clip markers, stereo-pan spatial emitters, and layered music mixing wired to RPG battle events.
<!-- /LLM:OVERVIEW -->

## Purpose

- Declarative `<AudioSource>` / `<AudioMixer>` / `<MusicLayer>` recipes
- Global sound bank (`defineSoundBank`, `playSound`, `playSoundAt`)
- Master + per-bus volume (music, sfx, ui)
- Animation clip sound markers (normalized time triggers)
- Spatial audio via Howler stereo panning (no HRTF)

## Layout

```
audio/
├── context.md       # This file
├── bank.ts          # Sound bank, buses, clip markers
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

## Clip markers

Attach synchronized one-shots to GLTF animation clips via `addClipSound(clipName, marker)`:

- `marker.at` — normalized time 0..1 on the clip timeline
- `marker.sound` — bank key to fire when the playhead crosses `at`

`AudioSystem` tracks per-entity normalized clip time and calls `fireClipMarkers` on crossings (including loop wrap). Markers are global per clip name, not per entity.

## Spatial audio

`AudioSource.spatial` and bank `spatial: true` use **Howler stereo panning only** — there is **no HRTF / Web Audio PannerNode HRTF model**. Distance attenuation uses Howler `pannerAttr` (ref distance, max distance, rolloff). Listener position follows the main camera each frame.

## Battle music (RpgCore)

`RpgCorePlugin` calls `wireMusicMixerEvents(state)` (`rpg-core/music-mixer-bridge.ts`), which subscribes to:

- `music:enter-battle` → sets `getAudioMix(state).activeLayer = MUSIC_LAYER_BATTLE`
- `music:exit-battle` → sets active layer back to `MUSIC_LAYER_EXPLORE`

`MusicMixerSystem` reads `activeLayer` and fades `<MusicLayer>` entities. Emit battle transitions from gameplay via `emitEvent(state, MUSIC_ENTER_BATTLE)` / `MUSIC_EXIT_BATTLE` (exported from `mixer.ts`).

## Entry points

- **Plugin**: `AudioPlugin` from `plugin.ts`
- **Bank**: `defineSoundBank`, `playSound`, `playSoundAt`, `playSoundOn` from `bank.ts`
- **Mixer**: `playMusicLayer`, `crossfadeMusicLayers`, `getAudioMix` from `mixer.ts`
- **RPG bridge**: `wireMusicMixerEvents` from `rpg-core` (not re-exported from audio index)

## Dependencies

- **Internal**: `rendering` (MainCamera), `transforms`, `gltf-anim` (clip time for markers)
- **External**: howler
