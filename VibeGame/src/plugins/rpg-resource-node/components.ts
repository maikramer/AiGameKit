import {
  defineComponent,
  F64,
  U16,
  U8,
} from '../../core/ecs/component-storage';

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
export const ResourceNode = defineComponent({
  /** Resource kind enum value (see `config.enums['resource-node'].kind`). */
  kind: U8,
  /** Amount yielded by a single harvest. */
  yield: U16,
  /** Respawn cooldown in seconds; `0` = one-shot (no respawn). */
  respawn: U16,
  /** `0` = available, `1` = depleted (waiting for respawn timer). */
  depleted: U8,
  /** `state.time.elapsed` timestamp at which the node becomes available again. */
  respawnAt: F64,
});
