import { describe, expect, test } from 'bun:test';
import {
  defineComponent,
  filled,
  isComponentMaterialized,
  F32,
  F64,
  I16,
  I32,
  U8,
  U16,
  U32,
} from '../../../src/core/ecs/component-storage';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';

describe('defineComponent', () => {
  test('maps every field kind to its typed array, sized to the cap', () => {
    const c = defineComponent({
      a: F32,
      b: F64,
      c: U8,
      d: U16,
      e: U32,
      f: I16,
      g: I32,
    });

    expect(c.a).toBeInstanceOf(Float32Array);
    expect(c.b).toBeInstanceOf(Float64Array);
    expect(c.c).toBeInstanceOf(Uint8Array);
    expect(c.d).toBeInstanceOf(Uint16Array);
    expect(c.e).toBeInstanceOf(Uint32Array);
    expect(c.f).toBeInstanceOf(Int16Array);
    expect(c.g).toBeInstanceOf(Int32Array);
    expect(c.a.length).toBe(MAX_ENTITIES);
  });

  test('allocates nothing until a field is touched', () => {
    const c = defineComponent({ x: F32, y: F32 });

    expect(isComponentMaterialized(c)).toBe(false);
    void c.x;
    expect(isComponentMaterialized(c)).toBe(true);
  });

  test('one field access materialises the whole component', () => {
    const c = defineComponent({ x: F32, y: U8 });

    void c.x;

    expect(Object.getOwnPropertyDescriptor(c, 'y')?.get).toBeUndefined();
    expect(c.y).toBeInstanceOf(Uint8Array);
  });

  test('repeated reads return the same array instance', () => {
    const c = defineComponent({ x: F32 });

    expect(c.x).toBe(c.x);
  });

  test('writes survive materialisation', () => {
    const c = defineComponent({ x: F32 });

    c.x[7] = 1.5;

    expect(c.x[7]).toBe(1.5);
  });

  test('filled() seeds every slot with a non-zero default', () => {
    const c = defineComponent({
      enabled: filled(U8, 1),
      index: filled(I32, -1),
      plain: U8,
    });

    expect(c.enabled[0]).toBe(1);
    expect(c.enabled[MAX_ENTITIES - 1]).toBe(1);
    expect(c.index[42]).toBe(-1);
    expect(c.plain[0]).toBe(0);
  });

  test('fields stay enumerable so field-copy helpers see the shape', () => {
    const c = defineComponent({ x: F32, y: U8 });

    expect(Object.keys(c).sort()).toEqual(['x', 'y']);
  });

  test('enumerating a component counts as a touch', () => {
    const c = defineComponent({ x: F32 });

    expect(isComponentMaterialized(c)).toBe(false);
    Object.values(c);
    expect(isComponentMaterialized(c)).toBe(true);
  });

  test('a component with no fields reports as materialised', () => {
    expect(isComponentMaterialized(defineComponent({}))).toBe(true);
  });
});
