import { logger } from '../../core/utils/logger';
import type { Parser, XMLValue } from '../../core';
import { Parent } from '../../core';
import { BodyType, Rigidbody } from '../physics/components';
import { PlacePending, setPlacementSpec } from '../spawner';
import {
  parseAt,
  parseSemicolonPlaceString,
  resolveSpawnFromPlaceAttrs,
} from '../spawner/place-fields';
import { Transform } from '../transforms/components';
import {
  isPrimitiveTag,
  parsePrimitiveSpec,
  setCompositionData,
  type ColliderMode,
  type PrimitiveSpec,
} from './primitives';
import type { State } from '../../core';

const BODY_TYPES: Record<string, number> = {
  fixed: BodyType.Fixed,
  static: BodyType.Fixed,
  dynamic: BodyType.Dynamic,
  kinematic: BodyType.KinematicVelocityBased,
  'kinematic-velocity': BodyType.KinematicVelocityBased,
  'kinematic-position': BodyType.KinematicPositionBased,
};

function resolveBodyType(value: XMLValue | undefined): number {
  if (value === undefined || value === null) return BodyType.Fixed;
  const key = String(value).trim().toLowerCase();
  return BODY_TYPES[key] ?? BodyType.Fixed;
}

function resolveColliderMode(value: XMLValue | undefined): ColliderMode {
  if (value === undefined || value === null) return 'auto';
  return String(value).trim().toLowerCase() === 'none' ? 'none' : 'auto';
}

// `body` is a parser attribute (not a component field), so the parser owns
// Rigidbody setup; position/rotation mirror Transform for PhysicsInit.
function setupRigidbody(state: State, entity: number, bodyType: number): void {
  state.addComponent(entity, Rigidbody);
  Rigidbody.type[entity] = bodyType;
  Rigidbody.posX[entity] = Transform.posX[entity];
  Rigidbody.posY[entity] = Transform.posY[entity];
  Rigidbody.posZ[entity] = Transform.posZ[entity];
  Rigidbody.rotX[entity] = Transform.rotX[entity];
  Rigidbody.rotY[entity] = Transform.rotY[entity];
  Rigidbody.rotZ[entity] = Transform.rotZ[entity];
  Rigidbody.rotW[entity] = Transform.rotW[entity] || 1;
  Rigidbody.eulerX[entity] = Transform.eulerX[entity];
  Rigidbody.eulerY[entity] = Transform.eulerY[entity];
  Rigidbody.eulerZ[entity] = Transform.eulerZ[entity];
  Rigidbody.poseDirty[entity] = 1;
}

function setupPlace(
  state: State,
  entity: number,
  placeRaw: XMLValue | undefined
): void {
  if (placeRaw === undefined || placeRaw === null) return;
  const placeStr = String(placeRaw).trim();
  if (placeStr === '') return;

  const merged = parseSemicolonPlaceString(placeStr);
  if (!('at' in merged)) {
    throw new Error(
      '[Composition] place= must include at: "x z" (e.g. place="at: 16 8")'
    );
  }
  const [atX, atZ] = parseAt(merged.at);
  const spawn = resolveSpawnFromPlaceAttrs(merged, 'place');

  setPlacementSpec(state, entity, {
    atX,
    atZ,
    spawn,
    templates: [],
  });
  state.addComponent(entity, PlacePending);
  PlacePending.spawned[entity] = 0;
}

export const compositionParser: Parser = ({
  entity,
  element,
  state,
  context,
}) => {
  const specs: PrimitiveSpec[] = [];

  for (const child of element.children) {
    if (!child.tagName || child.tagName.toLowerCase() === 'parsererror')
      continue;

    if (isPrimitiveTag(child.tagName)) {
      specs.push(parsePrimitiveSpec(child.tagName, child.attributes));
      continue;
    }

    if (state.hasRecipe(child.tagName)) {
      // Recipe children (PointLight, AudioSource, ...) become sibling entities
      // parented to the composition so their local Transform is relative to it.
      const childEntity = state.createFromRecipe(
        child.tagName,
        child.attributes
      );
      state.addComponent(childEntity, Parent, { entity });
      // `createFromRecipe` only applies component defaults + declared attribute
      // mappings; a recipe's own parser is invoked by the core element walker,
      // which never sees these children because Composition declares
      // `parserOwnsChildren`. Skipping it silently dropped every attribute a
      // parser owns — `<DialogueNPC dialogue-id>` inside a `<Composition>` fell
      // back to quest index 0, so every quest NPC offered the same quest.
      const childParser = state.getParser(child.tagName);
      if (childParser) {
        childParser({ entity: childEntity, element: child, state, context });
      }
      continue;
    }

    logger.warn(
      `[Composition] Ignoring unknown child <${child.tagName}>. ` +
        'Use Box/Sphere/Cylinder/Plane/Pad or a registered recipe (e.g. PointLight).'
    );
  }

  const colliderMode = resolveColliderMode(element.attributes.collider);
  setCompositionData(state, entity, { specs, colliderMode });

  setupRigidbody(state, entity, resolveBodyType(element.attributes.body));
  setupPlace(state, entity, element.attributes.place);
};
