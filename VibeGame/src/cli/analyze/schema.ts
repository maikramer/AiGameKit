import type { ParsedElement } from '../../core';
import type { State } from '../../core/ecs/state';
import {
  formatUnknownAttribute,
  formatUnknownElement,
} from '../../core/recipes/diagnostics';
import { getAvailableAttributes } from '../../core/recipes/parser';
import {
  recipeSchemas,
  safeValidateRecipeAttributes,
  type RecipeName,
} from '../../core/validation';
import { createHeadlessState } from '../headless';
import { DebugPlugin } from '../../plugins/debug/plugin';
import { DefaultPlugins } from '../../plugins/defaults';
import { RpgPlugins } from '../../plugins/rpg-bundle';
import { SpawnGatePlugin } from '../../plugins/spawn-gate/plugin';
import { IsometricCameraPlugin } from '../../plugins/isometric-camera/plugin';
import { FarmPlotPlugin } from '../../plugins/farm-plot/plugin';
import { DayCyclePlugin } from '../../plugins/daycycle/plugin';
import type { AnalyzeIssue, AnalyzePluginSet } from './types';

/** Containers / HTML noise — not recipes. */
const SKIP_TAGS = new Set([
  'world',
  'scene',
  'include',
  'html',
  'head',
  'body',
  'meta',
  'link',
  'title',
  'style',
  'script',
  'div',
  'span',
  'template',
]);

/** Composition primitive children (not registered recipes). */
const COMPOSITION_PRIMS = new Set([
  'box',
  'sphere',
  'cylinder',
  'plane',
  'pad',
]);

/** Opt-in engine plugins with recipes used by examples (not in DefaultPlugins). */
const EXTRA_ENGINE_PLUGINS = [
  SpawnGatePlugin,
  DebugPlugin,
  IsometricCameraPlugin,
  FarmPlotPlugin,
  DayCyclePlugin,
];

export function buildAnalyzeState(pluginSet: AnalyzePluginSet = 'all'): State {
  const plugins =
    pluginSet === 'default'
      ? [...DefaultPlugins]
      : [...DefaultPlugins, ...RpgPlugins, ...EXTRA_ENGINE_PLUGINS];
  return createHeadlessState({ plugins });
}

/**
 * Unknown recipe tags (error) + unknown attrs / soft Zod (warn).
 * Run on the pre-city-expand tree so CityGrid tags are still present.
 */
export function checkSchema(root: ParsedElement, state: State): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const recipeNames = Array.from(state.getRecipeNames());
  const recipeLower = new Set(recipeNames.map((n) => n.toLowerCase()));

  const walk = (el: ParsedElement, parentTag: string | null) => {
    const tag = el.tagName;
    const tagLower = tag.toLowerCase();

    if (SKIP_TAGS.has(tagLower)) {
      for (const c of el.children) walk(c, tag);
      return;
    }

    const underComposition = parentTag?.toLowerCase() === 'composition';
    if (underComposition && COMPOSITION_PRIMS.has(tagLower)) {
      for (const c of el.children) walk(c, tag);
      return;
    }

    if (!recipeLower.has(tagLower)) {
      issues.push({
        severity: 'error',
        code: 'recipe',
        message: `[analyze] ERROR ${formatUnknownElement(tag, recipeNames)}`,
      });
      for (const c of el.children) walk(c, tag);
      return;
    }

    const recipe = state.getRecipe(tag);
    if (!recipe) {
      for (const c of el.children) walk(c, tag);
      return;
    }

    const available = getAvailableAttributes(recipe, state);
    const availableLower = new Map(
      available.map((a) => [a.toLowerCase(), a] as const)
    );
    const shorthands: string[] = [];
    if (recipe.components) {
      for (const cn of recipe.components) {
        shorthands.push(...Object.keys(state.config.getShorthands(cn)));
      }
    }

    for (const attrName of Object.keys(el.attributes)) {
      if (availableLower.has(attrName.toLowerCase())) continue;
      if (state.getComponent(attrName)) continue;
      issues.push({
        severity: 'warn',
        code: 'attr',
        message: `[analyze] WARN ${formatUnknownAttribute(attrName, recipe.name, available, shorthands)}`,
      });
    }

    const zodKey = recipe.name as RecipeName;
    if (zodKey in recipeSchemas) {
      const filtered: Record<string, import('../../core').XMLValue> = {};
      for (const [k, v] of Object.entries(el.attributes)) {
        if (k === 'name' || k === 'id' || k === 'tag' || k === 'layer')
          continue;
        if (!availableLower.has(k.toLowerCase())) continue;
        filtered[k] = v;
      }
      if (Object.keys(filtered).length > 0) {
        const result = safeValidateRecipeAttributes(zodKey, filtered);
        if (!result.success && result.error) {
          // Skip pure "unknown attribute" noise from incomplete Zod shapes
          if (!/Unknown attribute/i.test(result.error)) {
            issues.push({
              severity: 'warn',
              code: 'attr',
              message: `[analyze] WARN schema <${recipe.name}>: ${result.error}`,
            });
          }
        }
      }
    }

    // parserOwnsChildren: kids are markup for the parser (HUD tabs, spawn templates), not recipes
    if (recipe.parserOwnsChildren) {
      return;
    }

    for (const c of el.children) walk(c, tag);
  };

  walk(root, null);
  return issues;
}
