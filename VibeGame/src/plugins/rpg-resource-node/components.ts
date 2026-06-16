import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Harvestable resource node (wood/stone/ore/...). Compose with `Destructible`,
 * `Transform` or any other component as needed — this component only carries
 * harvest state.
 *
 * `kind` is a small enum resolved from the `resource-node` config enum
 * (`wood=0, stone=1, ore=2` by default). Extend it by registering additional
 * enum entries (see `resolveResourceNodeKind`/`getResourceNodeKind`).
 *
 * `depleted` is `0` while the node is available and `1` while it waits for
 * `respawnAt`. One-shot nodes (`respawn=0`) never deplete — the caller is
 * responsible for removing them after a harvest.
 */
export const ResourceNode = {
  /** Resource kind enum value (see `config.enums['resource-node'].kind`). */
  kind: new Uint8Array(MAX_ENTITIES),
  /** Amount yielded by a single harvest. */
  yield: new Uint16Array(MAX_ENTITIES),
  /** Respawn cooldown in seconds; `0` = one-shot (no respawn). */
  respawn: new Uint16Array(MAX_ENTITIES),
  /** `0` = available, `1` = depleted (waiting for respawn timer). */
  depleted: new Uint8Array(MAX_ENTITIES),
  /** `state.time.elapsed` timestamp at which the node becomes available again. */
  respawnAt: new Float64Array(MAX_ENTITIES),
} as const;
