import { DirectionalLight, type Scene } from 'three';

// Cache the directional-light reference per scene so we don't traverse the
// whole graph every frame. The cache invalidates only when the directional-light
// count in the scene changes (a light was added/removed) — directional light
// properties (position/target) are read live from the cached reference.
const dirLightCache = new WeakMap<
  Scene,
  { light: DirectionalLight | null; count: number }
>();

/**
 * First directional light in the scene (the engine's sun). God rays and the
 * height-fog inscattering both key off this light so every sun-driven effect
 * converges on the same position in the sky.
 */
export function findFirstDirectionalLight(
  scene: Scene
): DirectionalLight | null {
  let cached = dirLightCache.get(scene);
  // Cheap live count: DirectionalLights are direct children of the scene root
  // (the LightSyncSystem adds them there). Counting root children with the
  // isDirectionalLight flag is O(children-of-root), not a full traverse.
  let rootDirCount = 0;
  for (const child of scene.children) {
    if ((child as DirectionalLight).isDirectionalLight) rootDirCount++;
  }
  if (
    cached &&
    cached.count === rootDirCount &&
    cached.light &&
    scene === cached.light.parent
  ) {
    return cached.light;
  }
  // Cache miss (first call, count changed, or light reparented/removed): do one
  // full traverse to (re)resolve, then stamp the count.
  let resolved: DirectionalLight | null = null;
  scene.traverse((obj) => {
    if (resolved === null && (obj as DirectionalLight).isDirectionalLight) {
      resolved = obj as DirectionalLight;
    }
  });
  dirLightCache.set(scene, { light: resolved, count: rootDirCount });
  return resolved;
}
