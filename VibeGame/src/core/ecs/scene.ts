import { getAllEntities } from "bitecs";
import type { State } from "./state";
import { stopAllCoroutines } from "./coroutines";
import { XMLParser } from "../xml";
import { parseXMLToEntities } from "../recipes/parser";

function performReload(state: State): void {
  if (!state.xmlSource) {
    throw new Error("[VibeGame] Scene.reload: state.xmlSource is not set");
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

export const Scene = {
  reload(state: State): void {
    performReload(state);
  },

  async reloadAsync(state: State): Promise<void> {
    performReload(state);
  },
};
