import type { State } from '../../core';

/**
 * Entity the directional-light shadow frustum should be centred on.
 *
 * The default centring chain (see `resolveShadowCenter`) assumes the camera is
 * roughly at head height behind the character: with no `ThirdPersonCamera` it
 * centres the box on the camera itself, biased forward by a fraction of the
 * frustum radius. That is a good guess for a first-person-ish rig and a bad one
 * for anything standing far back — an orthographic isometric camera sits tens
 * of metres away, so the box lands well short of the character and the hero
 * casts no shadow at all.
 *
 * Setting a focus entity replaces the guess with the truth: whatever the game
 * considers the subject (usually the player) is what the shadow box tracks.
 *
 * Unset by default, so games that never call this keep the historical
 * behaviour byte for byte.
 */
const shadowFocus = new WeakMap<State, number>();

/** Centre the shadow frustum on `entity` (must have a `WorldTransform`). */
export function setShadowFocusEntity(state: State, entity: number): void {
  shadowFocus.set(state, entity);
}

/** Current shadow focus entity, or 0 when none is set. */
export function getShadowFocusEntity(state: State): number {
  return shadowFocus.get(state) ?? 0;
}

/** Drop the focus entity and fall back to the default centring chain. */
export function clearShadowFocusEntity(state: State): void {
  shadowFocus.delete(state);
}
