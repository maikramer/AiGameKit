import { beforeEach, describe, expect, it } from 'bun:test';
import { PlayerControllerPlugin, State, ThirdPersonCamera } from 'vibegame';

const MAX_ENTITIES = 100000;

const FLOAT_FIELDS = [
  'distance',
  'height',
  'yaw',
  'pitch',
  'positionSmooth',
  'mouseSensitivity',
  'currentX',
  'currentY',
  'currentZ',
  'minTerrainDistance',
  'followX',
  'followY',
  'followZ',
  'smoothYaw',
  'followLag',
  'turnLag',
] as const;

describe('ThirdPersonCamera component', () => {
  it('expõe target como Uint32Array com tamanho MAX_ENTITIES', () => {
    expect(ThirdPersonCamera.target).toBeInstanceOf(Uint32Array);
    expect(ThirdPersonCamera.target.length).toBe(MAX_ENTITIES);
  });

  it('expõe initialized como Uint8Array com tamanho MAX_ENTITIES', () => {
    expect(ThirdPersonCamera.initialized).toBeInstanceOf(Uint8Array);
    expect(ThirdPersonCamera.initialized.length).toBe(MAX_ENTITIES);
  });

  it('expõe todos os campos float como Float32Array com tamanho MAX_ENTITIES', () => {
    for (const field of FLOAT_FIELDS) {
      expect(ThirdPersonCamera[field]).toBeInstanceOf(Float32Array);
      expect(ThirdPersonCamera[field].length).toBe(MAX_ENTITIES);
    }
  });

  it('não expõe campos além de target/initialized/floats', () => {
    const expected = ['target', 'initialized', ...FLOAT_FIELDS].sort();
    expect(Object.keys(ThirdPersonCamera).sort()).toEqual(expected);
  });

  it('inicializa com zeros e faz round-trip de escrita/leitura', () => {
    expect(ThirdPersonCamera.distance[0]).toBe(0);
    expect(ThirdPersonCamera.target[0]).toBe(0);
    expect(ThirdPersonCamera.initialized[0]).toBe(0);

    ThirdPersonCamera.target[2] = 99;
    ThirdPersonCamera.distance[2] = 12.5;
    ThirdPersonCamera.yaw[2] = Math.PI / 2;
    ThirdPersonCamera.initialized[2] = 1;

    expect(ThirdPersonCamera.target[2]).toBe(99);
    expect(ThirdPersonCamera.distance[2]).toBeCloseTo(12.5);
    expect(ThirdPersonCamera.yaw[2]).toBeCloseTo(Math.PI / 2);
    expect(ThirdPersonCamera.initialized[2]).toBe(1);

    ThirdPersonCamera.target[2] = 0;
    ThirdPersonCamera.distance[2] = 0;
    ThirdPersonCamera.yaw[2] = 0;
    ThirdPersonCamera.initialized[2] = 0;
  });
});

describe('PlayerControllerPlugin', () => {
  it('expõe defaults de third-person-camera com os valores esperados', () => {
    const defaults =
      PlayerControllerPlugin.config?.defaults?.['third-person-camera'];
    expect(defaults).toBeDefined();
    expect(defaults!.distance).toBe(12);
    expect(defaults!.height).toBe(4);
    expect(defaults!.pitch).toBeCloseTo(0.3);
    expect(defaults!.positionSmooth).toBeCloseTo(0.08);
    expect(defaults!.mouseSensitivity).toBeCloseTo(0.003);
    expect(defaults!.minTerrainDistance).toBeCloseTo(1.0);
    expect(defaults!.followLag).toBeCloseTo(0.18);
    expect(defaults!.turnLag).toBeCloseTo(0.35);
  });

  it('registra o recipe ThirdPersonCamera com components e merge=true', () => {
    expect(PlayerControllerPlugin.recipes).toBeDefined();
    expect(PlayerControllerPlugin.recipes).toHaveLength(1);
    const recipe = PlayerControllerPlugin.recipes![0];
    expect(recipe.name).toBe('ThirdPersonCamera');
    expect(recipe.components).toEqual([
      'third-person-camera',
      'transform',
      'main-camera',
    ]);
    expect(recipe.merge).toBe(true);
  });

  it('registra dois sistemas em grupos simulation (linking) e draw (camera)', () => {
    expect(PlayerControllerPlugin.systems).toHaveLength(2);
    const [linking, camera] = PlayerControllerPlugin.systems!;
    expect(linking.group).toBe('simulation');
    expect(typeof linking.update).toBe('function');
    expect(camera.group).toBe('draw');
    expect(typeof camera.update).toBe('function');
    expect(Array.isArray(camera.after)).toBe(true);
  });

  it('mapeia o componente ThirdPersonCamera no registro do plugin', () => {
    expect(Object.values(PlayerControllerPlugin.components!)).toContain(
      ThirdPersonCamera
    );
  });
});

describe('PlayerControllerPlugin integração com State', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('registerPlugin torna ThirdPersonCamera adicionável e visível por hasComponent', () => {
    state.registerPlugin(PlayerControllerPlugin);
    const entity = state.createEntity();
    state.addComponent(entity, ThirdPersonCamera);

    expect(state.hasComponent(entity, ThirdPersonCamera)).toBe(true);
  });
});
