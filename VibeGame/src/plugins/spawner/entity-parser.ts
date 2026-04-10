import type { Parser } from '../../core';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import { PlacePending } from './components';
import { setPlacementSpec } from './place-context';
import type { PlacementSpec } from './place-types';
import {
  parseAt,
  parseSemicolonPlaceString,
  resolveSpawnFromPlaceAttrs,
} from './place-fields';

function prefetchGltfChildren(element: {
  tagName: string;
  children: readonly { tagName: string; attributes: Record<string, unknown> }[];
}): void {
  const walk = (el: {
    tagName: string;
    children: readonly {
      tagName: string;
      attributes: Record<string, unknown>;
    }[];
  }) => {
    for (const c of el.children || []) {
      if (c.tagName === 'gltf-load' || c.tagName === 'gltf-dynamic') {
        const u = c.attributes.url;
        if (typeof u === 'string' && u.trim()) {
          prefetchGltfLocalYBounds(u.trim());
        }
      }
      walk(c as unknown as typeof el);
    }
  };
  walk(element);
}

export const entityParser: Parser = ({ entity, element, state }) => {
  if (element.tagName !== 'entity') return;

  const placeRaw = element.attributes.place;
  if (placeRaw === undefined || placeRaw === null) return;

  const placeStr = String(placeRaw).trim();
  const merged = parseSemicolonPlaceString(placeStr);
  if (!('at' in merged)) {
    throw new Error(
      '[entity] place= must include at: "x z" (e.g. place="at: 0 -8; base-y-offset: 0.02")'
    );
  }
  const [atX, atZ] = parseAt(merged.at);
  const spawn = resolveSpawnFromPlaceAttrs(merged, 'place');

  const spec: PlacementSpec = {
    atX,
    atZ,
    spawn,
    templates: [],
  };
  setPlacementSpec(state, entity, spec);

  const PlacePendingCmp = state.getComponent('placePending');
  if (PlacePendingCmp) {
    state.addComponent(entity, PlacePendingCmp);
    PlacePending.spawned[entity] = 0;
  }

  prefetchGltfChildren(element);
};
