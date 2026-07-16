# Vegetation plugin

Dense static ground cover (grass, plants, flowers) via `SpawnGroupSpec` + InstancedMesh2, with optional wind.

## Recipe

```html
<Vegetation
  meshes="/assets/…/grass.glb /assets/…/flower_yellowA.glb"
  density-per-km2="50000"
  seed="41"
  region-min="-90 0 -90"
  region-max="90 0 90"
  smart="1"
  flower-near-radius="2.2"
  flower-density-ratio="0.15"
  plant-density-ratio="0.25"
  wind="1"
></Vegetation>
```

## Smart patch (default `smart="1"`)

When meshes resolve to ≥2 roles (`grass` / `plant` / `flower` by filename):

1. `VegetationPlannerSystem` samples shared cluster hubs (seed + region).
2. Creates child entities per layer with `clusterCenters` set.
3. Flowers/plants use the same hubs and a smaller `clusterRadius` (`flower-near-radius`).

Roles: `grass*`, `flower*`, `plant*` (override: `mesh-roles="/a.glb:grass,/b.glb:flower"`).

Size tiers from GLB height (or filename `large`/`short`) set per-layer `scaleMin`/`scaleMax` unless the patch sets `scale-min`/`scale-max`.

## Legacy

`smart="0"` or a single role → one `SpawnGroupSpec` on the Vegetation entity (previous behaviour).
