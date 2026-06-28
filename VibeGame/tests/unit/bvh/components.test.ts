import { describe, expect, it } from 'bun:test';
import { BvhTarget } from 'vibegame';

const MAX_ENTITIES = 100000;

describe('BvhTarget component', () => {
  it('expõe exatamente os campos include, layer e dirty', () => {
    expect(Object.keys(BvhTarget).sort()).toEqual([
      'dirty',
      'include',
      'layer',
    ]);
  });

  it('usa os tipos de typed array corretos por campo', () => {
    expect(BvhTarget.include).toBeInstanceOf(Uint8Array);
    expect(BvhTarget.layer).toBeInstanceOf(Uint16Array);
    expect(BvhTarget.dirty).toBeInstanceOf(Uint8Array);
  });

  it('dimensiona cada array para MAX_ENTITIES (100000)', () => {
    expect(BvhTarget.include.length).toBe(MAX_ENTITIES);
    expect(BvhTarget.layer.length).toBe(MAX_ENTITIES);
    expect(BvhTarget.dirty.length).toBe(MAX_ENTITIES);
  });

  it('inicializa todos os slots com zero', () => {
    expect(BvhTarget.include[0]).toBe(0);
    expect(BvhTarget.layer[0]).toBe(0);
    expect(BvhTarget.dirty[0]).toBe(0);
    expect(BvhTarget.include[MAX_ENTITIES - 1]).toBe(0);
  });

  it('faz round-trip de leitura/escrita por entidade', () => {
    BvhTarget.include[42] = 1;
    BvhTarget.layer[42] = 0x0002;
    BvhTarget.dirty[42] = 1;

    expect(BvhTarget.include[42]).toBe(1);
    expect(BvhTarget.layer[42]).toBe(0x0002);
    expect(BvhTarget.dirty[42]).toBe(1);
    expect(BvhTarget.include[43]).toBe(0);

    BvhTarget.include[42] = 0;
    BvhTarget.layer[42] = 0;
    BvhTarget.dirty[42] = 0;
  });

  it('aceita o valor máximo do layer (0xffff) sem clipping', () => {
    BvhTarget.layer[7] = 0xffff;
    expect(BvhTarget.layer[7]).toBe(65535);
    BvhTarget.layer[7] = 0;
  });
});
