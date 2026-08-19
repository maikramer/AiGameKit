import type { Parser, ParsedElement, XMLValue } from '../../core';
import { parseNumberAttr, parseVec3Attr } from '../../core';
import { SpawnerPending } from '../spawner/components';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import { setNaturePlan } from './context';
import {
  hasNearCondition,
  type GroveMemberRule,
  type GroveRule,
  type NatureRulesPlan,
  type RangeBand,
  type SpeciesRule,
  type WhereCondition,
} from './rules';

/**
 * `<NatureSpawner seed region-min region-max count|density-per-km2
 * min-spacing noise-scale>` with `<Species>` (+`<Where>`) and `<Grove>`
 * children. The tag itself never spawns — the planner materializes one
 * SpawnGroupSpec per species after the ground is ready.
 */

function toNumber(value: XMLValue | undefined, fallback: number): number {
  return parseNumberAttr(value, fallback);
}

function str(value: XMLValue | undefined): string | undefined {
  if (value === undefined || value === null) return undefined;
  const s = String(value).trim();
  return s === '' ? undefined : s;
}

function childrenOf(el: ParsedElement): ParsedElement[] {
  return el.children.filter((c) => c.tagName && c.tagName !== 'parsererror');
}

function whereChildOf(el: ParsedElement): ParsedElement | undefined {
  return childrenOf(el).find((c) => c.tagName.toLowerCase() === 'where');
}

/**
 * Band from a `*-min` / `*-max` attribute pair. XML values are pre-converted
 * by XMLValueParser (`"16"` → number 16), so range STRINGS like `"11..17"`
 * never survive the pipeline intact — bands in XML use the engine's min/max
 * pair convention (`altitude-min`, `altitude-max`, …). Open ends: omit the
 * side you don't want to bound.
 */
function bandFromAttrs(
  a: Record<string, XMLValue>,
  minKey: string,
  maxKey: string,
  label: string
): RangeBand | undefined {
  const minRaw = str(a[minKey]);
  const maxRaw = str(a[maxKey]);
  if (minRaw === undefined && maxRaw === undefined) return undefined;
  const min = minRaw !== undefined ? parseFloat(minRaw) : -Infinity;
  const max = maxRaw !== undefined ? parseFloat(maxRaw) : Infinity;
  if (Number.isNaN(min)) {
    throw new Error(
      `[nature] ${label} ${minKey}="${minRaw}" deve ser numérico.`
    );
  }
  if (Number.isNaN(max)) {
    throw new Error(
      `[nature] ${label} ${maxKey}="${maxRaw}" deve ser numérico.`
    );
  }
  if (min > max) {
    throw new Error(
      `[nature] ${label}: ${minKey} (${min}) > ${maxKey} (${max}).`
    );
  }
  return { min, max };
}

function parseWhere(
  el: ParsedElement | undefined,
  label: string
): WhereCondition {
  if (!el) return {};
  const a = el.attributes;
  const cond: WhereCondition = {};

  const biome = str(a.biome);
  if (biome) {
    cond.biome = biome
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
  }

  cond.altitude = bandFromAttrs(a, 'altitude-min', 'altitude-max', label);
  cond.slope = bandFromAttrs(a, 'slope-min', 'slope-max', label);

  const water = str(a.water)?.toLowerCase();
  if (water === 'in' || water === 'bank') {
    cond.waterMode = water;
  } else if (water) {
    throw new Error(
      `[nature] ${label} water="${water}" inválido. Use "in" (sobre a água) ou "bank" (margem esculpida).`
    );
  }
  cond.waterDist = bandFromAttrs(a, 'water-dist-min', 'water-dist-max', label);
  if (cond.waterMode && cond.waterDist) {
    throw new Error(
      `[nature] ${label}: water= e water-dist-min/max= são mutuamente exclusivos.`
    );
  }

  cond.roadDist = bandFromAttrs(a, 'road-dist-min', 'road-dist-max', label);

  const near = str(a.near);
  if (near) {
    cond.nearSpecies = near
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
  }
  cond.nearDist = bandFromAttrs(a, 'near-dist-min', 'near-dist-max', label);
  if (cond.nearSpecies && !cond.nearDist) {
    throw new Error(
      `[nature] ${label}: near= exige near-dist-min/max=. Ex.: near="oak" near-dist-min="0" near-dist-max="9".`
    );
  }
  if (cond.nearDist && !cond.nearSpecies) {
    throw new Error(
      `[nature] ${label}: near-dist-min/max= exige near= (ids de espécies).`
    );
  }

  cond.noise = bandFromAttrs(a, 'noise-min', 'noise-max', label);
  return cond;
}

const SPECIES_OWN_ATTRIBUTES = new Set([
  'id',
  'weight',
  'cap',
  'url',
  'lod1-url',
  'lod2-url',
  'profile',
]);

function parseSpecies(el: ParsedElement, seen: Set<string>): SpeciesRule {
  const a = el.attributes;
  const id = str(a.id)?.toLowerCase();
  const url = str(a.url);
  if (!id || !url) {
    throw new Error(
      '[nature] <Species> exige id="…" e url="…" (GLB).\n' +
        '  Exemplo: <Species id="oak" weight="3" url="/assets/meshes/forest/tree_oak_lod0.glb"><Where altitude="1..15" /></Species>'
    );
  }
  if (seen.has(id)) {
    throw new Error(`[nature] Species id="${id}" duplicado.`);
  }

  const spawnAttrs: Record<string, XMLValue> = {};
  for (const [k, v] of Object.entries(a)) {
    if (!SPECIES_OWN_ATTRIBUTES.has(k)) spawnAttrs[k] = v;
  }

  return {
    id,
    weight: Math.max(0, toNumber(a.weight, 1)),
    cap: Math.max(0, Math.floor(toNumber(a.cap, 0))),
    url,
    lod1Url: str(a['lod1-url']),
    lod2Url: str(a['lod2-url']),
    where: parseWhere(whereChildOf(el), `Species "${id}"`),
    profile: str(a.profile) ?? 'none',
    spawnAttrs,
  };
}

function parseMember(
  el: ParsedElement,
  speciesIds: Set<string>,
  groveId: string
): GroveMemberRule {
  const a = el.attributes;
  const species = str(a.species)?.toLowerCase();
  if (!species) {
    throw new Error(
      `[nature] <Member> do grove "${groveId}" exige species="…" (id de um <Species>).`
    );
  }
  if (!speciesIds.has(species)) {
    throw new Error(
      `[nature] Grove "${groveId}": <Member species="${species}"> não corresponde a nenhum <Species id>.`
    );
  }
  const count = bandFromAttrs(
    a,
    'count-min',
    'count-max',
    `Grove "${groveId}" Member`
  );
  const ring = bandFromAttrs(
    a,
    'at-min',
    'at-max',
    `Grove "${groveId}" Member`
  );
  return {
    species,
    countMin: Math.max(0, Math.floor(count?.min ?? 1)),
    countMax: Math.max(0, Math.floor(count?.max ?? count?.min ?? 1)),
    ringMin: Math.max(0, Math.min(1, ring?.min ?? 0)),
    ringMax: Math.max(0, Math.min(1, ring?.max ?? 1)),
  };
}

function parseGrove(el: ParsedElement, speciesIds: Set<string>): GroveRule {
  const a = el.attributes;
  const id =
    str(a.id) ?? `grove-${speciesIds.size}-${el.attributes.count ?? 1}`;
  const count = Math.max(1, Math.floor(toNumber(a.count, 1)));
  const radius = Math.max(0.5, toNumber(a.radius, 8));
  const where = parseWhere(whereChildOf(el), `Grove "${id}"`);
  if (hasNearCondition(where)) {
    throw new Error(
      `[nature] Grove "${id}": near=/near-dist= não se aplicam a hubs — use bandas em <Species>.`
    );
  }
  const members = childrenOf(el)
    .filter((c) => c.tagName.toLowerCase() === 'member')
    .map((c) => parseMember(c, speciesIds, id));
  if (members.length === 0) {
    throw new Error(
      `[nature] Grove "${id}" exige ao menos um <Member species="…" count-min/max="…" at-min/max="…" />.`
    );
  }
  return { id, count, radius, where, members };
}

export const natureSpawnerParser: Parser = ({ entity, element, state }) => {
  const a = element.attributes;

  if (!hasRegion(a)) {
    throw new Error(
      '[nature] <NatureSpawner> exige region-min="x 0 z" e region-max="x 0 z".\n' +
        '  Exemplo: <NatureSpawner seed="42" region-min="-660 0 -660" region-max="660 0 660" density-per-km2="9000">'
    );
  }
  const regionMin = parseVec3Attr(a['region-min'], [0, 0, 0]);
  const regionMax = parseVec3Attr(a['region-max'], [0, 0, 0]);

  const hasDensity = str(a['density-per-km2']) !== undefined;
  const hasCount = str(a.count) !== undefined;
  let spawnCountMode: 'fixed' | 'density';
  let count = 0;
  let densityPerKm2 = 0;
  if (hasDensity) {
    spawnCountMode = 'density';
    densityPerKm2 = toNumber(a['density-per-km2'], 0);
    if (!Number.isFinite(densityPerKm2) || densityPerKm2 < 0) {
      throw new Error(
        '[nature] density-per-km2 deve ser um número ≥ 0 (candidatos por km²).'
      );
    }
  } else if (hasCount) {
    spawnCountMode = 'fixed';
    count = Math.floor(toNumber(a.count, 0));
    if (count < 1) {
      throw new Error('[nature] count deve ser ≥ 1 (ou usa density-per-km2).');
    }
  } else {
    throw new Error(
      '[nature] <NatureSpawner> exige count="N" ou density-per-km2="…" para o total de candidatos.'
    );
  }

  const species: SpeciesRule[] = [];
  const seenSpecies = new Set<string>();
  const groveElements: ParsedElement[] = [];
  for (const child of childrenOf(element)) {
    const tag = child.tagName.toLowerCase();
    if (tag === 'species') {
      species.push(parseSpecies(child, seenSpecies));
      seenSpecies.add(species[species.length - 1]!.id);
    } else if (tag === 'grove') {
      groveElements.push(child);
    } else {
      throw new Error(
        `[nature] Filho <${child.tagName}> inesperado. Use <Species> ou <Grove>.`
      );
    }
  }
  if (species.length === 0) {
    throw new Error(
      '[nature] <NatureSpawner> exige ao menos um <Species id url> filho.'
    );
  }
  // Groves parse after every species exists — member refs are order-independent.
  const groves = groveElements.map((g) => parseGrove(g, seenSpecies));
  for (const sp of species) {
    if (sp.weight < 0) {
      throw new Error(`[nature] Species "${sp.id}": weight deve ser ≥ 0.`);
    }
  }

  const plan: NatureRulesPlan = {
    seed: Math.floor(toNumber(a.seed, 1)),
    regionMin,
    regionMax,
    spawnCountMode,
    count,
    densityPerKm2,
    minSpacing: Math.max(0.01, toNumber(a['min-spacing'], 2.5)),
    noiseScale: Math.max(1, toNumber(a['noise-scale'], 90)),
    species,
    groves,
  };

  // The tag never spawns on its own; NaturePlannerSystem materializes the
  // per-species children once the ground is ready.
  SpawnerPending.spawned[entity] = 1;
  setNaturePlan(state, entity, { plan, planned: false });

  for (const sp of species) prefetchGltfLocalYBounds(sp.url);
};

function hasRegion(a: Record<string, XMLValue>): boolean {
  return (
    str(a['region-min']) !== undefined && str(a['region-max']) !== undefined
  );
}
