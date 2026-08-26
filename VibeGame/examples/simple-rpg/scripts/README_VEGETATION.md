# Vegetation GLBs (simple-rpg)

Grass / plant / flower meshes for the smart `<Vegetation>` carpet.

## Output

`examples/shared-assets/public/assets/meshes/vegetation/*.glb` (Y-up for
Three.js / wind / ground-align) — the canonical shared pool, served to every
example by `vibegame({ sharedAssets })`.

Author space in Blender is Z-up; the script exports with `export_yup=True`.

## Generate

Requires `bpy` (Animator3D venv / Blender Python):

```bash
cd VibeGame/examples/simple-rpg
npm run generate-vegetation
# or:
# Animator3D/.venv/bin/python scripts/generate_vegetation_glb.py
```

`fetch-assets` fills the pool in fill-if-missing mode, so a release tarball never overwrites the local bpy meshes with stubs.

## Scene usage

| Fragment                                   | Role                                  |
| ------------------------------------------ | ------------------------------------- |
| `public/world/spawn/ring.xml`              | Valley ring ±58 around Discordia      |
| `public/world/vegetation/crystal-vale.xml` | Deep biome carpets (outside the ring) |

Engine docs: `VibeGame/src/plugins/vegetation/context.md`.

## Scale notes

Native mesh height is often ~0.2–0.4 m. Recipes use `scale-min` / `scale-max` (and size tiers) so world instances land roughly ~0.25–0.75 m+ for grass tufts — not 1 m+ giants. Prefer tuning XML scale over regenerating meshes unless topology changes.
