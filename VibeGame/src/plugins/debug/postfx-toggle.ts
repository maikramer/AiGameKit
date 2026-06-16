import { defineQuery, type Recipe, type State, type System } from '../../core';
import { isKeyDown } from '../input/utils';
import { getRenderingContext } from '../rendering/utils';
import { Postprocessing } from '../postprocessing/components';

export type PostFxEffectField =
  | 'bloom'
  | 'chromaticAberration'
  | 'vignette'
  | 'aa'
  | 'toneMapping'
  | 'ssao';

export type PostFxKeyBindings = Map<string, PostFxEffectField>;

export type IsKeyDownFn = (code: string) => boolean;

const EFFECT_ALIASES: Record<string, PostFxEffectField> = {
  bloom: 'bloom',
  ca: 'chromaticAberration',
  chromaticaberration: 'chromaticAberration',
  vignette: 'vignette',
  aa: 'aa',
  tonemapping: 'toneMapping',
  ssao: 'ssao',
};

const FIELD_MODULUS: Record<PostFxEffectField, number> = {
  bloom: 2,
  chromaticAberration: 2,
  vignette: 2,
  aa: 3,
  toneMapping: 5,
  ssao: 2,
};

export const DEFAULT_POSTFX_BINDINGS: PostFxKeyBindings = new Map<
  string,
  PostFxEffectField
>([
  ['Digit1', 'bloom'],
  ['Digit2', 'chromaticAberration'],
  ['Digit3', 'vignette'],
  ['Digit4', 'aa'],
  ['Digit5', 'ssao'],
  ['Digit6', 'toneMapping'],
]);

export function parsePostFxBindings(raw: string): PostFxKeyBindings {
  const result: PostFxKeyBindings = new Map();
  const trimmed = raw.trim();
  if (trimmed === '') return result;
  for (const pair of trimmed.split(',')) {
    const colon = pair.indexOf(':');
    if (colon === -1) continue;
    const code = pair.slice(0, colon).trim();
    const effectName = pair
      .slice(colon + 1)
      .trim()
      .toLowerCase();
    const field = EFFECT_ALIASES[effectName];
    if (!field || code === '') continue;
    result.set(code, field);
  }
  return result;
}

type PostprocessingFields = Record<PostFxEffectField, Uint8Array>;

export interface PostFxToggleOptions {
  bindings: PostFxKeyBindings;
  isKeyDown: IsKeyDownFn;
  debounce: Set<string>;
  postprocessing: PostprocessingFields;
  eid: number;
}

export interface PostFxToggleResult {
  toggled: PostFxEffectField[];
}

export function applyPostFxToggle(
  opts: PostFxToggleOptions
): PostFxToggleResult {
  const toggled: PostFxEffectField[] = [];
  for (const [code, field] of opts.bindings) {
    const down = opts.isKeyDown(code);
    if (down && !opts.debounce.has(code)) {
      opts.debounce.add(code);
      const arr = opts.postprocessing[field];
      const mod = FIELD_MODULUS[field];
      arr[opts.eid] = (arr[opts.eid] + 1) % mod;
      toggled.push(field);
    }
    if (!down) opts.debounce.delete(code);
  }
  return { toggled };
}

const postprocessingQuery = defineQuery([Postprocessing]);
const postfxState = new WeakMap<
  State,
  { bindings: PostFxKeyBindings; debounce: Set<string> }
>();

export function getPostFxToggleState(state: State): {
  bindings: PostFxKeyBindings;
  debounce: Set<string>;
} {
  let s = postfxState.get(state);
  if (!s) {
    s = { bindings: new Map(DEFAULT_POSTFX_BINDINGS), debounce: new Set() };
    postfxState.set(state, s);
  }
  return s;
}

export function setPostFxBindings(
  state: State,
  bindings: PostFxKeyBindings
): void {
  const s = getPostFxToggleState(state);
  s.bindings = bindings;
}

export const PostFxToggleSystem: System = {
  group: 'simulation',
  update(state: State) {
    const entities = postprocessingQuery(state.world);
    if (entities.length === 0) return;
    const eid = entities[0];

    const ps = getPostFxToggleState(state);
    const result = applyPostFxToggle({
      bindings: ps.bindings,
      isKeyDown,
      debounce: ps.debounce,
      postprocessing: Postprocessing as unknown as PostprocessingFields,
      eid,
    });

    if (result.toggled.length > 0) {
      const ctx = getRenderingContext(state);
      ctx.postProcessing?.dispose();
      ctx.postProcessing = undefined;
    }
  },
};

export const postFxToggleRecipe: Recipe = {
  name: 'PostFxDebugToggle',
  components: [],
  parserAttributes: ['bindings'],
};
