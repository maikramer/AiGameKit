import { MAX_ENTITIES } from './constants';

/**
 * Lazily-allocated SoA storage for ECS components.
 *
 * Every component field is a `TypedArray[MAX_ENTITIES]`, so declaring them
 * eagerly means the engine pays for all of them the moment the barrel is
 * imported — around 290 MB of zero-filled arrays across the ~104 components,
 * whether the game is a full RPG or hello-world, and whether a plugin is
 * registered or not. The cap is what makes it expensive, and the cap is also
 * what content-heavy scenes want raised.
 *
 * {@link defineComponent} keeps the same field shape and the same
 * `Component.field[eid]` access, but allocates the arrays on the component's
 * first field access. Reading or writing any field materialises the whole
 * component at once and replaces the accessors with plain data properties, so
 * only one hidden-class transition happens per component and hot loops see an
 * ordinary object afterwards. A component nobody touches never allocates.
 */
export const F32 = 'f32';
export const F64 = 'f64';
export const U8 = 'u8';
export const U16 = 'u16';
export const U32 = 'u32';
export const I16 = 'i16';
export const I32 = 'i32';

export type FieldKind =
  | typeof F32
  | typeof F64
  | typeof U8
  | typeof U16
  | typeof U32
  | typeof I16
  | typeof I32;

/** A field kind, optionally with a non-zero default for every entity. */
export type FieldSpec = FieldKind | { kind: FieldKind; fill: number };

/**
 * Field default other than 0 — the lazy equivalent of
 * `new Uint8Array(MAX_ENTITIES).fill(1)`.
 */
export function filled<K extends FieldKind>(
  kind: K,
  fill: number
): { kind: K; fill: number } {
  return { kind, fill };
}

interface FieldArrays {
  f32: Float32Array;
  f64: Float64Array;
  u8: Uint8Array;
  u16: Uint16Array;
  u32: Uint32Array;
  i16: Int16Array;
  i32: Int32Array;
}

type KindOf<S> = S extends FieldKind
  ? S
  : S extends { kind: infer K }
    ? K
    : never;

/** The component object produced from a field-kind map. */
export type ComponentOf<T extends Record<string, FieldSpec>> = {
  readonly [K in keyof T]: FieldArrays[KindOf<T[K]> & FieldKind];
};

function allocate(kind: FieldKind): FieldArrays[FieldKind] {
  switch (kind) {
    case F32:
      return new Float32Array(MAX_ENTITIES);
    case F64:
      return new Float64Array(MAX_ENTITIES);
    case U8:
      return new Uint8Array(MAX_ENTITIES);
    case U16:
      return new Uint16Array(MAX_ENTITIES);
    case U32:
      return new Uint32Array(MAX_ENTITIES);
    case I16:
      return new Int16Array(MAX_ENTITIES);
    case I32:
      return new Int32Array(MAX_ENTITIES);
  }
}

/**
 * Declare a component from a map of field kinds:
 *
 * ```ts
 * export const Transform = defineComponent({ posX: F32, posY: F32, dirty: U8 });
 * Transform.posX[eid] = 1; // allocates on this first touch, then plain arrays
 * ```
 */
export function defineComponent<T extends Record<string, FieldSpec>>(
  fields: T
): ComponentOf<T> {
  const component = {} as Record<string, FieldArrays[FieldKind]>;
  const keys = Object.keys(fields);
  let materialized = false;

  const materialize = (): void => {
    if (materialized) return;
    materialized = true;
    for (const key of keys) {
      const spec = fields[key];
      const array =
        typeof spec === 'string' ? allocate(spec) : allocate(spec.kind);
      if (typeof spec !== 'string' && spec.fill !== 0) array.fill(spec.fill);
      Object.defineProperty(component, key, {
        value: array,
        enumerable: true,
        writable: false,
        configurable: false,
      });
    }
  };

  for (const key of keys) {
    Object.defineProperty(component, key, {
      // Enumerable so `Object.keys` / field-copy helpers still see the shape;
      // note that enumerating a component is itself a touch and allocates it.
      enumerable: true,
      configurable: true,
      get(): FieldArrays[FieldKind] {
        materialize();
        return component[key];
      },
    });
  }

  return component as ComponentOf<T>;
}

/** True when the component has allocated its arrays (diagnostics/tests). */
export function isComponentMaterialized(component: object): boolean {
  const key = Object.keys(component)[0];
  if (key === undefined) return true;
  const descriptor = Object.getOwnPropertyDescriptor(component, key);
  return descriptor?.get === undefined;
}
