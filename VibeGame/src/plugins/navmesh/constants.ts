/**
 * Default NavMeshAgent capsule (metres). Leaf module — no plugin imports — so
 * sibling plugins (e.g. physics recipes sizing creature colliders) can reuse
 * the values without pulling the navmesh plugin graph into a cycle.
 */
export const DEFAULT_NAVMESH_AGENT_RADIUS = 0.4;
export const DEFAULT_NAVMESH_AGENT_HEIGHT = 1.0;
