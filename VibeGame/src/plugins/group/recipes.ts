import type { Recipe } from '../../core';

/**
 * Recipe for the `<Group>` element — a semantic transform-only container.
 *
 * A Group holds no systems, components or parsers of its own: it relies on the
 * engine's built-in entity hierarchy. The XML parser auto-parents any nested
 * recipe child onto the Group entity via the `Parent` component
 * (`core/recipes/parser.ts`), and `TransformHierarchySystem` composes
 * `parent.WorldTransform × child.Transform` so that moving the Group moves every
 * descendant. See `tests/integration/group/group-hierarchy.test.ts` for the
 * contract.
 *
 * Supports nesting (`<Group>` inside `<Group>`) and the usual `pos`, `rot`,
 * `scale`, `name`, `script` attributes — `merge` is intentionally false so each
 * Group is its own addressable entity (e.g. `name="town.marketplace"`).
 */
export const groupRecipe: Recipe = {
  name: 'Group',
  components: ['transform'],
};
