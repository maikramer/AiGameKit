# Spawn variation

<!-- LLM:OVERVIEW -->

Per-instance visual jitter (hue / saturation / brightness / contrast) for static instanced props — trees, foliage, rocks. Samples a preset range per entity and writes colour + shader uniforms into the shared `InstancedMesh2` pool.
<!-- /LLM:OVERVIEW -->

Usado por `<SpawnGroup profile="tree|foliage|rock">` e `<Vegetation>` (preset `foliage` por omissão).

## Presets (`presets.ts`)

| Preset    | Uso típico                    |
| --------- | ----------------------------- |
| `none`    | Sem jitter                    |
| `tree`    | Árvores                       |
| `foliage` | Grama / carpet (`Vegetation`) |
| `rock`    | Pedras                        |

## XML

- `variation="foliage"` — força preset (senão deriva do `profile` do grupo)
- `hue-jitter-deg`, `saturation-min` / `saturation-max`, `brightness-min` / `brightness-max`, `contrast-min` / `contrast-max`
- `variation-spatial` — mistura com posição mundo (manchas coerentes)

## Pipeline

1. `resolveVariationSpec(attrs, groupProfile)` no parse
2. Spec no `SpawnGroupSpec.variation`
3. Sample por instância (`sample.ts`) → `setColorAt` + `setUniformAt('uVarBrightness'|'uVarContrast')` na pool
4. Material patch (`material-patch.ts`) injeta o math de brightness/contrast no fragment shader **antes** do material ir para `InstancedMesh2`

## Material patch (`material-patch.ts`)

`maybePatchInstanceVariationMaterial(mat)` wraps `onBeforeCompile` and injects after `#include <map_fragment>`:

```glsl
#ifdef USE_INSTANCING_INDIRECT
  diffuseColor.rgb = (diffuseColor.rgb - 0.5) * uVarContrast + 0.5;
  diffuseColor.rgb *= uVarBrightness;
#endif
```

`InstancedMesh2.initUniformsPerInstance(INSTANCE_VARIATION_UNIFORM_SCHEMA)` (in `gltf-xml/auto-instance.ts`) declares the per-instance locals at the start of `main` and sets `USE_INSTANCING_INDIRECT`.

### Learnings / pitfalls

- **Patch before `new InstancedMesh2` / `addLOD`.** The library snapshots `onBeforeCompile` and wraps it every frame; patching after handoff is silently lost (same rule as vegetation wind).
- **Gate usage with `#ifdef USE_INSTANCING_INDIRECT`.** Master GLB materials are shared; compiling the same mat outside the instanced path (probe, non-instanced reuse) must not reference `uVar*` or WebGL fails with undeclared identifier. Defines are applied at compile time even though InstancedMesh2 sets them after the base `onBeforeCompile` runs.
- Skip CustomShaderMaterial (`'__csm' in mat`) — that library owns `onBeforeCompile`.
- Bump `customProgramCacheKey` when the injected GLSL changes so Three recompiles.

## Ficheiros

`resolve.ts`, `presets.ts`, `sample.ts`, `material-patch.ts`, `components.ts`, `types.ts`, `lookup.ts`.
