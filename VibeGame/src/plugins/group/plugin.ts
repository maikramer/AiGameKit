import type { Plugin } from '../../core';
import { groupRecipe } from './recipes';

/**
 * Registers the `<Group>` recipe only. Hierarchy behaviour is provided entirely
 * by the engine core (`Parent` component + `TransformHierarchySystem` + the XML
 * parser's auto-parenting of nested recipe children), so this plugin declares no
 * systems, components, or parsers.
 */
export const GroupPlugin: Plugin = {
  recipes: [groupRecipe],
};
