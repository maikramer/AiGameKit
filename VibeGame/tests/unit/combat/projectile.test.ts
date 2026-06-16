import { beforeEach, describe, expect, it } from 'bun:test';
import {
  Collider,
  CollisionEvents,
  CombatPlugin,
  getDataRegistry,
  ProjectileConfig,
  ProjectileData,
  PROJECTILE_TEMPLATE_KIND,
  Rigidbody,
  spawnProjectile,
  spawnProjectileFromTemplate,
  State,
  Transform,
} from 'vibegame';

describe('Projectile spawn + lifetime (T15)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
    state.registerComponent('collider', Collider);
    state.registerComponent('collision-events', CollisionEvents);
  });

  describe('spawnProjectile cria entity com config', () => {
    it('attaches ProjectileData + ProjectileConfig + Rigidbody + Collider', () => {
      const origin = state.createEntity();
      state.addComponent(origin, Transform);
      Transform.posX[origin] = 0;
      Transform.posY[origin] = 1;
      Transform.posZ[origin] = 0;

      const target = state.createEntity();
      state.addComponent(target, Transform);
      Transform.posX[target] = 10;
      Transform.posY[target] = 1;
      Transform.posZ[target] = 0;

      const eid = spawnProjectile(state, origin, target, {
        speed: 40,
        damage: 10,
        maxLife: 3,
        faction: 0,
      });

      expect(state.hasComponent(eid, ProjectileData)).toBe(true);
      expect(state.hasComponent(eid, ProjectileConfig)).toBe(true);
      expect(state.hasComponent(eid, Rigidbody)).toBe(true);
      expect(state.hasComponent(eid, Collider)).toBe(true);
      expect(state.hasComponent(eid, CollisionEvents)).toBe(true);

      expect(ProjectileConfig.speed[eid]).toBe(40);
      expect(ProjectileConfig.maxLife[eid]).toBe(3);
      expect(ProjectileConfig.damage[eid]).toBe(10);
      expect(ProjectileConfig.faction[eid]).toBe(0);

      expect(ProjectileData.ownerEid[eid]).toBe(origin);
      expect(ProjectileData.lifetime[eid]).toBe(3);
      expect(ProjectileData.age[eid]).toBe(0);
    });

    it('sets velocity along normalized origin -> target direction times speed', () => {
      const origin = state.createEntity();
      state.addComponent(origin, Transform);
      Transform.posX[origin] = 0;
      Transform.posY[origin] = 0;
      Transform.posZ[origin] = 0;

      const target = state.createEntity();
      state.addComponent(target, Transform);
      Transform.posX[target] = 10;
      Transform.posY[target] = 0;
      Transform.posZ[target] = 0;

      const eid = spawnProjectile(state, origin, target, {
        speed: 40,
        damage: 10,
        maxLife: 3,
      });

      expect(Rigidbody.velX[eid]).toBeCloseTo(40, 5);
      expect(Rigidbody.velY[eid]).toBeCloseTo(0, 5);
      expect(Rigidbody.velZ[eid]).toBeCloseTo(0, 5);
      expect(Rigidbody.gravityScale[eid]).toBe(0);
      expect(Collider.isSensor[eid]).toBe(1);
    });

    it('accepts an explicit point target and a bare eid target', () => {
      const origin = state.createEntity();
      state.addComponent(origin, Transform);
      Transform.posX[origin] = 0;
      Transform.posY[origin] = 0;
      Transform.posZ[origin] = 0;

      const byPoint = spawnProjectile(
        state,
        origin,
        { point: [0, 5, 0] },
        {
          speed: 10,
          damage: 1,
          maxLife: 1,
        }
      );
      expect(Rigidbody.velY[byPoint]).toBeCloseTo(10, 5);

      const target = state.createEntity();
      state.addComponent(target, Transform);
      Transform.posZ[target] = 7;
      const byEid = spawnProjectile(state, origin, target, {
        speed: 10,
        damage: 1,
        maxLife: 1,
      });
      expect(Rigidbody.velZ[byEid]).toBeCloseTo(10, 5);
    });
  });

  describe('projectile cleanup apos maxLife sem hit', () => {
    it('removes the projectile once age >= ProjectileConfig.maxLife', () => {
      const origin = state.createEntity();
      state.addComponent(origin, Transform);

      const target = state.createEntity();
      state.addComponent(target, Transform);
      Transform.posX[target] = 1;

      const eid = spawnProjectile(state, origin, target, {
        speed: 5,
        damage: 4,
        maxLife: 0.5,
      });
      expect(state.exists(eid)).toBe(true);

      state.step(0.3);
      expect(state.exists(eid)).toBe(true);

      state.step(0.3);
      expect(state.exists(eid)).toBe(false);
    });

    it('falls back to ProjectileData.lifetime for legacy projectiles', () => {
      const eid = state.createEntity();
      state.addComponent(eid, ProjectileData, {
        lifetime: 0.4,
        age: 0,
        damage: 1,
        ownerEid: 0,
      });

      state.step(0.5);

      expect(state.exists(eid)).toBe(false);
    });
  });

  describe('spawnProjectileFromTemplate', () => {
    it('reads the projectile template from the registry and spawns configured', () => {
      getDataRegistry(state).register(PROJECTILE_TEMPLATE_KIND, 'arrow', {
        id: 'arrow',
        speed: 40,
        damage: 10,
        maxLife: 3,
        sensorRadius: 0.3,
        faction: 'enemy',
      });

      const origin = state.createEntity();
      state.addComponent(origin, Transform);
      const target = state.createEntity();
      state.addComponent(target, Transform);
      Transform.posX[target] = 5;

      const eid = spawnProjectileFromTemplate(state, origin, 'arrow', target);

      expect(ProjectileConfig.speed[eid]).toBe(40);
      expect(ProjectileConfig.maxLife[eid]).toBe(3);
      expect(ProjectileConfig.damage[eid]).toBe(10);
      expect(ProjectileConfig.faction[eid]).toBe(1);
      expect(Collider.radius[eid]).toBeCloseTo(0.3, 5);
    });

    it('throws on unknown template name', () => {
      const origin = state.createEntity();
      state.addComponent(origin, Transform);
      expect(() =>
        spawnProjectileFromTemplate(state, origin, 'missing')
      ).toThrow(/missing/);
    });
  });
});
