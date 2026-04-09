import type { Parser, XMLValue } from '../../core';
import { formatUnknownElement } from '../../core/recipes/diagnostics';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import {
  applyChildTemplateProfile,
  normalizeChildTemplateProfileId,
  resolveGroupSpawnFields,
} from './profiles';
import { setPlacementSpec } from './place-context';
import type { PlacementSpec } from './place-types';
import type { SpawnTemplateRole, SpawnTemplateSpec } from './types';

const VALID_ROLES = new Set<string>([
  'visual',
  'dynamic',
  'static',
  'kinematic',
  '',
]);

function parseRole(value: XMLValue | undefined): SpawnTemplateRole {
  if (value === undefined || value === null) return '';
  const s = String(value).trim().toLowerCase();
  if (s === '') return '';
  if (!VALID_ROLES.has(s)) {
    console.warn(
      `[place] role="${value}" desconhecido; use visual | dynamic | static | kinematic. Ignorado no spec.`
    );
    return '';
  }
  return s as SpawnTemplateRole;
}

function parseAt(value: XMLValue | undefined): [number, number] {
  if (value === undefined || value === null) {
    throw new Error(
      '[place] Atributo obrigatório "at" em falta.\n' +
        '  Exemplo: <place at="0 -8">...</place> (dois números: x z em espaço mundo)'
    );
  }
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const o = value as Record<string, number>;
    if ('x' in o && 'z' in o) {
      return [Number(o.x), Number(o.z)];
    }
    if ('x' in o && 'y' in o) {
      return [Number(o.x), Number(o.y)];
    }
  }
  if (Array.isArray(value) && value.length >= 2) {
    return [Number(value[0]), Number(value[1])];
  }
  if (typeof value === 'string') {
    const p = value
      .trim()
      .split(/\s+/)
      .map((x) => parseFloat(x));
    if (p.length >= 2 && p.every((n) => !Number.isNaN(n))) {
      return [p[0]!, p[1]!];
    }
  }
  if (typeof value === 'number') {
    return [value, 0];
  }
  throw new Error(
    '[place] Atributo "at" deve ser dois números: "x z" (ex.: at="12 -4").'
  );
}

export const placeParser: Parser = ({ entity, element, state }) => {
  if (element.tagName !== 'place') return;

  const [atX, atZ] = parseAt(element.attributes.at);

  const children = element.children.filter(
    (c) => c.tagName && c.tagName !== 'parsererror'
  );
  if (children.length === 0) {
    throw new Error(
      '[place] É necessário pelo menos um filho (recipe).\n' +
        '  Exemplo: <place at="0 -8"><gltf-load url="..."></gltf-load></place>'
    );
  }

  const templates: SpawnTemplateSpec[] = [];
  for (const child of children) {
    if (!state.hasRecipe(child.tagName)) {
      const msg = formatUnknownElement(
        child.tagName,
        Array.from(state.getRecipeNames())
      );
      throw new Error(
        `[place] Filho inválido: ${msg}\n` +
          '  Use apenas tags com recipe registado (ex.: gltf-load, particle-emitter).'
      );
    }
    const attrs = { ...child.attributes };
    const childTplProfile = normalizeChildTemplateProfileId(attrs.profile);
    if ('profile' in attrs) {
      delete attrs.profile;
    }
    const roleRaw = attrs.role;
    if ('role' in attrs) {
      delete attrs.role;
    }
    applyChildTemplateProfile(child.tagName, attrs, childTplProfile);
    const tpl: SpawnTemplateSpec = {
      tagName: child.tagName,
      attributes: attrs,
      role: parseRole(roleRaw),
    };
    if (childTplProfile) {
      tpl.childProfile = childTplProfile;
    }
    templates.push(tpl);
  }

  const mergedAttrs: Record<string, XMLValue> = { ...element.attributes };
  if ('y-offset' in mergedAttrs && !('base-y-offset' in mergedAttrs)) {
    mergedAttrs['base-y-offset'] = mergedAttrs['y-offset'];
  }

  const spawn = resolveGroupSpawnFields(mergedAttrs, 'place');

  const spec: PlacementSpec = {
    atX,
    atZ,
    spawn,
    templates,
  };

  setPlacementSpec(state, entity, spec);

  for (const tpl of templates) {
    const u = tpl.attributes.url;
    if (typeof u === 'string' && u.trim()) {
      prefetchGltfLocalYBounds(u.trim());
    }
  }
};
