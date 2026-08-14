# Chrono Plugin

Opt-in time-travel recording for debugging and gameplay rewind.

## Purpose

- Records world snapshots into a fixed-size ring buffer at a configurable rate
- `chronoSeek`/`chronoRewind` restore any recorded frame in place: named
  entities keep their eid (three.js cameras, physics handles and listeners stay
  valid), divergent entities are destroyed, missing ones are recreated
- Physics bodies are teleported back to their restored pose/velocity after a seek
- Integrates with the debug registry (`chrono.rewind`, `chrono.mark`,
  `chrono.status`, var `chrono.frames`)

## Structure

- `plugin.ts` — `ChronoPlugin` (system registration + default enable) and `applyChronoOptions` (options, physics resync, debug actions)
- `utils.ts` — `resyncPhysicsAfterSeek` pushes restored ECS pose into Rapier bodies
- Recording core lives in `src/core/ecs/chrono.ts` (engine-level, no plugin required)

## Usage

```ts
GAME.withChrono({ seconds: 60, hz: 10 }).run();

// anywhere later:
chronoRewind(state, 5);        // back 5s
chronoSeek(state, frameIndex); // exact frame
chronoMark(state, 'before-boss');
```

The `vibegame:chrono-seek` window event fires after every successful seek.
