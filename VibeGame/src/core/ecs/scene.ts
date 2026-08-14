import { getAllEntities } from 'bitecs';
import type { State } from './state';
import { stopAllCoroutines, cleanupEntityCoroutines } from './coroutines';
import { XMLParser, type ParsedElement } from '../xml';
import { parseXMLToEntities } from '../recipes/parser';
import type { EntityCreationResult } from '../recipes/types';

function performReload(state: State): void {
  if (!state.xmlSource) {
    throw new Error('[VibeGame] Scene.reload: state.xmlSource is not set');
  }

  const allEntityIds = Array.from(getAllEntities(state.world));

  for (const eid of allEntityIds) {
    stopAllCoroutines(state, eid);
  }

  for (const eid of allEntityIds) {
    state.destroyEntity(eid);
  }

  state.clearTemplates();

  const parsed = XMLParser.parse(state.xmlSource);
  parseXMLToEntities(state, parsed.root);
}

export interface SceneSwapResult {
  /** Entities destroyed from the previous world parse (0 on first load). */
  destroyed: number;
  /** Entities created by this parse. */
  created: number;
}

// Entities born from the last world parse, per state. Everything else
// (player, cameras, runtime spawns) is untouched by Scene.swap.
const worldEntitiesByState = new WeakMap<State, number[]>();

function collectEntityIds(
  results: EntityCreationResult[],
  out: number[] = []
): number[] {
  for (const result of results) {
    out.push(result.entity);
    collectEntityIds(result.children, out);
  }
  return out;
}

export const Scene = {
  reload(state: State): void {
    performReload(state);
  },

  async reloadAsync(state: State): Promise<void> {
    performReload(state);
  },

  /** Entity IDs created by the most recent world parse (shallow copy). */
  worldEntities(state: State): number[] {
    return [...(worldEntitiesByState.get(state) ?? [])];
  },

  /**
   * Replace the entities created by the previous world parse with a freshly
   * parsed document. The new document is parsed BEFORE anything is destroyed,
   * so invalid XML leaves the running world untouched.
   *
   * Entities outside the world document (auto-created player and cameras,
   * runtime spawns, HUD) survive the swap — this is what makes live world
   * editing possible.
   */
  swap(state: State, root: ParsedElement): SceneSwapResult {
    const previous = worldEntitiesByState.get(state) ?? [];

    for (const eid of previous) {
      cleanupEntityCoroutines(state, eid);
    }
    let destroyed = 0;
    for (const eid of previous) {
      if (!state.exists(eid)) continue;
      state.destroyEntity(eid);
      destroyed++;
    }

    const creation = parseXMLToEntities(state, root);
    const created = collectEntityIds(creation);
    worldEntitiesByState.set(state, created);

    return { destroyed, created: created.length };
  },
};
