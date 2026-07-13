import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  State,
  Transform,
  WorldTransform,
  XMLParser,
  parseXMLToEntities,
} from 'vibegame';
import { GroupPlugin } from 'vibegame/group';
import { Parent, TransformHierarchySystem } from 'vibegame/transforms';

describe('Group hierarchy', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();
    // GameObject já é registado pelo State; GroupPlugin acrescenta <Group>.
    // TransformsPlugin regista transform/world-transform + o sistema de hierarquia.
    state.registerPlugin({
      systems: [TransformHierarchySystem],
      components: { Transform, WorldTransform },
      config: {
        defaults: {
          transform: { rotW: 1, scaleX: 1, scaleY: 1, scaleZ: 1, dirty: 1 },
          'world-transform': { rotW: 1, scaleX: 1, scaleY: 1, scaleZ: 1 },
        },
      },
    });
    state.registerPlugin(GroupPlugin);
  });

  it('aninha Group → Group → GameObject ligando Parent em todos os níveis', () => {
    const xml = `<root>
      <Group name="town" pos="100 0 100">
        <Group name="town.marketplace" pos="0 0 0">
          <GameObject name="town.merchant" pos="2 0 3"></GameObject>
        </Group>
        <GameObject name="town.bank" pos="-5 0 0"></GameObject>
      </Group>
    </root>`;
    const parsed = XMLParser.parse(xml);
    parseXMLToEntities(state, parsed.root);

    const town = state.getEntityByName('town')!;
    const market = state.getEntityByName('town.marketplace')!;
    const merchant = state.getEntityByName('town.merchant')!;
    const bank = state.getEntityByName('town.bank')!;

    expect(Parent.entity[market]).toBe(town);
    expect(Parent.entity[bank]).toBe(town);
    expect(Parent.entity[merchant]).toBe(market);
  });

  it('mover o Group raiz desloca os descendentes (composição pai×filho)', () => {
    const xml = `<root>
      <Group name="town" pos="100 0 100">
        <Group name="town.market" pos="10 0 10">
          <GameObject name="town.stall" pos="2 0 3"></GameObject>
        </Group>
      </Group>
    </root>`;
    const parsed = XMLParser.parse(xml);
    parseXMLToEntities(state, parsed.root);

    const town = state.getEntityByName('town')!;
    const stall = state.getEntityByName('town.stall')!;

    // Primeira pass: resolve os WorldTransform a partir dos locais do XML.
    TransformHierarchySystem.update!(state);
    expect(WorldTransform.posX[stall]).toBeCloseTo(112, 5); // 100 + 10 + 2
    expect(WorldTransform.posZ[stall]).toBeCloseTo(113, 5); // 100 + 10 + 3

    // Mover o town em runtime: escrever Transform + marcar dirty propaga aos filhos.
    Transform.posX[town] = 200;
    Transform.dirty[town] = 1;
    TransformHierarchySystem.update!(state);
    expect(WorldTransform.posX[stall]).toBeCloseTo(212, 5); // 200 + 10 + 2
    expect(WorldTransform.posZ[stall]).toBeCloseTo(113, 5); // inalterado em Z
  });

  it('rotacionar o Group raiz orbita os filhos à volta do pivot', () => {
    // Filho deslocado +10 em X do town na origem; rodar town 90° em Y leva-o a +10 em Z.
    const xml = `<root>
      <Group name="town" pos="0 0 0">
        <GameObject name="town.flag" pos="10 0 0"></GameObject>
      </Group>
    </root>`;
    const parsed = XMLParser.parse(xml);
    parseXMLToEntities(state, parsed.root);

    const town = state.getEntityByName('town')!;
    const flag = state.getEntityByName('town.flag')!;

    TransformHierarchySystem.update!(state);
    expect(WorldTransform.posX[flag]).toBeCloseTo(10, 5);

    // 90° em Y. O dirty bit do XML inicial já foi limpo na pass anterior.
    Transform.eulerY[town] = 90;
    Transform.dirty[town] = 1;
    TransformHierarchySystem.update!(state);

    // Rotação +90° em Y (convenção Three.js): (x,z)=(10,0) -> (0,-10).
    expect(WorldTransform.posX[flag]).toBeCloseTo(0, 1);
    expect(WorldTransform.posZ[flag]).toBeCloseTo(-10, 1);
  });

  it('um Group sem filhos é uma entidade válida com transform próprio', () => {
    const xml = `<root><Group name="empty" pos="5 0 7"></Group></root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const empty = state.getEntityByName('empty')!;
    expect(state.hasComponent(empty, Transform)).toBe(true);
    expect(Transform.posX[empty]).toBe(5);
    expect(Transform.posZ[empty]).toBe(7);
  });
});
