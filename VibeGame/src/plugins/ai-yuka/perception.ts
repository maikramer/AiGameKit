/**
 * Perception helpers re-exported from `bvh`. The line-of-sight raycast lives in
 * the BVH plugin (it owns the raycaster + mesh store), so both `rpg-ai` and
 * this plugin import it from there rather than duplicating the math. Re-exported
 * here purely as a convenience entry point for game code that already imports
 * its AI utilities from `ai-yuka`.
 */
export { hasLineOfSight, DEFAULT_VISION_BLOCK_LAYERS } from '../bvh/utils';
