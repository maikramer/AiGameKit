# Particles Plugin

Particle system plugin using three.quarks (`BatchedRenderer` + `ParticleSystem`).

## Components

- **`particle-emitter`** — SOA component with preset, emission, color, shape, burst fields.

## Recipes

- `<ParticleSystem preset="fire">` — Continuous looping particle emitter.
- `<ParticleBurst preset="explosion">` — One-shot burst with auto-destroy.

## Presets

fire, rain, snow, smoke, dust, explosion, sparks, magic, fireflies, splash,
woodchips, rockshards, leaves, ground-dust

Alias: `sparkle` → `magic`; `sand-dust` → `ground-dust`.

O enum que valida `preset=` no XML sai de `presetEnumValues()` — **canónicos +
aliases, da mesma fonte que `presetIndex()`**. Enquanto a lista era copiada à
mão para `config.enums`, `preset="sparkle"` passava em `presetIndex()` e era
rejeitado pelo validador de atributos; e um enum rejeitado **aborta o parse do
mundo inteiro**, não só aquele emissor. Ao acrescentar preset ou alias, não há
nada a duplicar — mas há uma entrada a acrescentar em `PRESET_TEXTURE_FILE`
(`textures.ts`), senão o preset fica sem sprite.

| Preset        | Use                                                               |
| ------------- | ----------------------------------------------------------------- |
| `dust`        | Destructible / burst-style sand puff (sphere emitter)             |
| `ground-dust` | **Ambient** low sheet (RectangleEmitter ~8×6 m), horizontal drift |
| `fireflies`   | Looping glow points — dark forest clearings                       |

New presets **append** at the end of `PRESET_NAMES` (stable indices for SOA).

### Ambient ground dust (XML)

`transform="pos: x y z"` num `<ParticleSystem>` é posição de **mundo**, não
altura acima do chão. Em mapas com terreno procedural (simple-rpg: solo aos
23–63 m) um `pos: 110 0.25 35` fica dezenas de metros **enterrado** e nunca se
vê nada. Ancorar sempre no terreno com `<GameObject place="at: x z">` e deixar
o emissor como filho a uma altura local:

```html
<GameObject place="at: 110 35">
  <ParticleSystem preset="ground-dust" transform="pos: 0 0.25 0"></ParticleSystem>
</GameObject>
```

simple-rpg usa este padrão em `public/world/atmosphere/ambient-fx.xml`.
Prefere `ground-dust` para areia rasteira; `dust` fica para FX de quebra
(presets do `destructible`).

Presets de ambiente em ciclo (`snow`, `rain`) precisam de `emission-rate`
explícito: o default de 50/s vira nevão/aguaceiro que tapa a paisagem toda a
poucas dezenas de metros. ~10/s chega para leitura de bioma.

## Textures

Each preset uses a sprite map from `/assets/particles/` (configurable via
`setParticleTextureBaseUrl`). Semantic files: `flame.png`, `smoke.png`,
`spark.png`, etc. Headless / missing files fall back to a soft radial
`DataTexture` so particles never render as hard squares.

Games should ship Kenney (or other) sprites under `public/assets/particles/`.

## Key Rules

- Use `ParticleSystem.emitter` (the internal `ParticleEmitter` Object3D) directly.
  A separate wrapper `ParticleEmitter` causes the batch system to dispose the system
  in update and particles disappear.
- `scene.add(ps.emitter)` — NOT `scene.add(ps)`.
- `batchedRenderer.addSystem(ps)` then `batchedRenderer.update(delta)` each frame.
- Systems are stored in a sidecar `Map<number, ParticleSystem>` keyed by entity ID
  (PS objects cannot live in SOA typed arrays).
- `ConeEmitter` shoots along local **+Z**. Fire/smoke/sparks/splash rotate the emitter
  (`rotation.x = -PI/2`) so the cone points world **+Y**.
- World acceleration uses `ApplyForce`, not `GravityForce` (that one is Newton pull
  toward a point).

## Systems

- `ParticleUpdateSystem` (group: `draw`) — Preloads textures, creates/disposes
  `ParticleSystem` instances, syncs position from `WorldTransform`, ticks the
  `BatchedRenderer`.
