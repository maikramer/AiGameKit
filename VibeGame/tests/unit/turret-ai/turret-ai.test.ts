import { beforeEach, describe, expect, it } from 'bun:test';
import {
  Collider,
  CollisionEvents,
  CombatPlugin,
  createTurretAi,
  defineQuery,
  FactionComponent,
  Health,
  PROJECTILE_TEMPLATE_KIND,
  ProjectileConfig,
  ProjectileData,
  Rigidbody,
  spawnProjectileFromTemplate,
  State,
  Transform,
  TurretAiBehaviour,
  getDataRegistry,
  setFaction,
  type TurretAiConfig,
} from 'vibegame';

const ARROW_TEMPLATE = 'arrow';
const projectileQuery = defineQuery([ProjectileData]);

function makeConfig(overrides: Partial<TurretAiConfig> = {}): TurretAiConfig {
  return {
    range: 50,
    cooldown: 0,
    maxProjectiles: 5,
    projectileTemplate: ARROW_TEMPLATE,
    spawnOffset: [0, 0, 0],
    targetFaction: 'enemy',
    ...overrides,
  };
}

function place(eid: number, x: number, y: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = y;
  Transform.posZ[eid] = z;
}

describe('TurretAiBehaviour (T16)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
    state.registerComponent('collider', Collider);
    state.registerComponent('collision-events', CollisionEvents);
    getDataRegistry(state).register('faction-hostility', 'default', {
      pairs: [['player', 'enemy']],
    });
    getDataRegistry(state).register(PROJECTILE_TEMPLATE_KIND, ARROW_TEMPLATE, {
      id: ARROW_TEMPLATE,
      speed: 40,
      damage: 10,
      maxLife: 3,
      sensorRadius: 0.3,
      faction: 'player',
    });
  });

  function makeTurret(x = 0, y = 0, z = 0): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, FactionComponent);
    setFaction(state, eid, 'player');
    place(eid, x, y, z);
    return eid;
  }

  function makeEnemy(x: number, y = 0, z = 0, hp = 100): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, Health);
    state.addComponent(eid, FactionComponent);
    setFaction(state, eid, 'enemy');
    Health.current[eid] = hp;
    Health.max[eid] = hp;
    place(eid, x, y, z);
    return eid;
  }

  function ownProjectiles(turretEid: number): number[] {
    const hits: number[] = [];
    for (const eid of projectileQuery(state.world)) {
      if (ProjectileData.ownerEid[eid] === turretEid) hits.push(eid);
    }
    return hits;
  }

  it('fires a projectile when a hostile target is in range', () => {
    const turret = makeTurret(0, 0, 0);
    makeEnemy(10, 0, 0);

    const ai = new (createTurretAi(makeConfig()))();
    ai.start?.(state, turret);
    ai.update(state, turret);

    const shots = ownProjectiles(turret);
    expect(shots.length).toBe(1);
    expect(state.exists(shots[0])).toBe(true);
    expect(ProjectileConfig.damage[shots[0]]).toBe(10);
  });

  it('respects cooldown: a second consecutive update does not fire again', () => {
    const turret = makeTurret(0, 0, 0);
    makeEnemy(10, 0, 0);

    const ai = new (createTurretAi(makeConfig({ cooldown: 0.5 })))();
    ai.start?.(state, turret);
    ai.update(state, turret);
    ai.update(state, turret);

    expect(ownProjectiles(turret).length).toBe(1);
  });

  it('does not fire when there is no target in range', () => {
    const turret = makeTurret(0, 0, 0);
    makeEnemy(10, 0, 0);

    const ai = new (createTurretAi(makeConfig({ range: 5 })))();
    ai.start?.(state, turret);
    ai.update(state, turret);

    expect(ownProjectiles(turret).length).toBe(0);
  });

  it('does not fire at a non-hostile faction', () => {
    const turret = makeTurret(0, 0, 0);
    const neutral = state.createEntity();
    state.addComponent(neutral, Transform);
    state.addComponent(neutral, Health);
    state.addComponent(neutral, FactionComponent);
    setFaction(state, neutral, 'neutral');
    Health.current[neutral] = 100;
    place(neutral, 1, 0, 0);

    const ai = new (createTurretAi(makeConfig({ range: 50 })))();
    ai.start?.(state, turret);
    ai.update(state, turret);

    expect(ownProjectiles(turret).length).toBe(0);
  });

  it('enforces the maxProjectiles cap', () => {
    const turret = makeTurret(0, 0, 0);
    makeEnemy(10, 0, 0);

    const ai = new (createTurretAi(makeConfig({ maxProjectiles: 1 })))();
    ai.start?.(state, turret);
    ai.update(state, turret);
    ai.update(state, turret);

    expect(ownProjectiles(turret).length).toBe(1);
  });

  it('targets the nearest hostile first', () => {
    const turret = makeTurret(0, 0, 0);
    makeEnemy(0, 0, 20);
    makeEnemy(5, 0, 0);

    const ai = new (createTurretAi(makeConfig({ maxProjectiles: 1 })))();
    ai.start?.(state, turret);
    ai.update(state, turret);

    const shots = ownProjectiles(turret);
    expect(shots.length).toBe(1);
    expect(Rigidbody.velX[shots[0]]).toBeGreaterThan(0);
    expect(Math.abs(Rigidbody.velZ[shots[0]])).toBeLessThan(1);
  });

  it('createTurretAi returns a parameterless TurretAiBehaviour subclass', () => {
    const Cls = createTurretAi(makeConfig());
    const inst = new Cls();
    expect(inst).toBeInstanceOf(TurretAiBehaviour);
    expect(typeof inst.update).toBe('function');
  });

  it('spawnProjectileFromTemplate (T15) remains the firing primitive', () => {
    const turret = makeTurret(0, 0, 0);
    const enemy = makeEnemy(8, 0, 0);
    const eid = spawnProjectileFromTemplate(state, turret, ARROW_TEMPLATE, {
      eid: enemy,
    });
    expect(state.exists(eid)).toBe(true);
    expect(ProjectileData.ownerEid[eid]).toBe(turret);
  });
});
