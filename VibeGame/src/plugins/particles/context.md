# Particles Plugin

Particle system plugin using three.quarks (`BatchedRenderer` + `ParticleSystem`).

## Components

- **`particle-emitter`** — SOA component with preset, emission, color, shape, burst fields.

## Recipes

- `<ParticleSystem preset="fire">` — Continuous looping particle emitter.
- `<ParticleBurst preset="explosion">` — One-shot burst with auto-destroy.

## Presets

fire, rain, snow, smoke, dust, explosion, sparks, magic, fireflies

## Key Rules

- Use `ParticleSystem.emitter` (the internal `ParticleEmitter` Object3D) directly.
  A separate wrapper `ParticleEmitter` causes the batch system to dispose the system
  in update and particles disappear.
- `scene.add(ps.emitter)` — NOT `scene.add(ps)`.
- `batchedRenderer.addSystem(ps)` then `batchedRenderer.update(delta)` each frame.
- Systems are stored in a sidecar `Map<number, ParticleSystem>` keyed by entity ID
  (PS objects cannot live in SOA typed arrays).

## Systems

- `ParticleUpdateSystem` (group: `draw`) — Creates/disposes `ParticleSystem` instances,
  syncs position from `WorldTransform`, ticks the `BatchedRenderer`.
