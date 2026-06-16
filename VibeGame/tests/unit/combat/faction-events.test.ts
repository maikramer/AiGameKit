import { beforeEach, describe, expect, it } from 'bun:test';
import {
  bindCombatState,
  CombatDeathCleanupSystem,
  CombatPlugin,
  COMBAT_DAMAGED,
  COMBAT_DEATH,
  COMBAT_HEALED,
  COMBAT_KILLED,
  damageHealth,
  FactionComponent,
  FactionHostilityMatrix,
  getFaction,
  getDataRegistry,
  healHealth,
  Health,
  isHostile,
  onEvent,
  setFaction,
  State,
} from 'vibegame';

interface DamagedPayload {
  target: number;
  amount: number;
  newHp: number;
}
interface DeathPayload {
  target: number;
}

describe('Combat events + faction (T12)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(CombatPlugin);
    bindCombatState(state);
  });

  describe('damageHealth emits combat:damaged', () => {
    it('emits damaged payload with target/amount/newHp', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 100;
      Health.max[eid] = 100;

      const hits: DamagedPayload[] = [];
      onEvent(state, COMBAT_DAMAGED, (p) => {
        hits.push(p as DamagedPayload);
      });

      damageHealth(eid, 30);

      expect(hits.length).toBe(1);
      expect(hits[0]).toEqual({ target: eid, amount: 30, newHp: 70 });
    });

    it('does not emit damaged when entity is already dead', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 0;
      Health.max[eid] = 100;

      const hits: DamagedPayload[] = [];
      onEvent(state, COMBAT_DAMAGED, (p) => {
        hits.push(p as DamagedPayload);
      });

      damageHealth(eid, 30);

      expect(hits.length).toBe(0);
      expect(Health.current[eid]).toBe(0);
    });
  });

  describe('hp reaching 0 emits combat:killed + combat:death', () => {
    it('emits combat:killed on the alive -> dead transition', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 10;
      Health.max[eid] = 10;

      const killed: DeathPayload[] = [];
      onEvent(state, COMBAT_KILLED, (p) => {
        killed.push(p as DeathPayload);
      });

      damageHealth(eid, 10);

      expect(killed.length).toBe(1);
      expect(killed[0]).toEqual({ target: eid });
      expect(Health.current[eid]).toBe(0);
    });

    it('emits combat:death once via CombatDeathCleanupSystem', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 10;
      Health.max[eid] = 10;

      const deaths: DeathPayload[] = [];
      onEvent(state, COMBAT_DEATH, (p) => {
        deaths.push(p as DeathPayload);
      });

      damageHealth(eid, 10);
      CombatDeathCleanupSystem.update!(state);

      expect(deaths.length).toBe(1);
      expect(deaths[0]).toEqual({ target: eid });
    });

    it('death is idempotent: second cleanup run does not re-emit', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 10;
      Health.max[eid] = 10;

      const deaths: DeathPayload[] = [];
      onEvent(state, COMBAT_DEATH, (p) => {
        deaths.push(p as DeathPayload);
      });

      damageHealth(eid, 10);
      CombatDeathCleanupSystem.update!(state);
      CombatDeathCleanupSystem.update!(state);

      expect(deaths.length).toBe(1);
    });

    it('does not re-emit killed/death when damaging a corpse', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 10;
      Health.max[eid] = 10;

      const killed: DeathPayload[] = [];
      const deaths: DeathPayload[] = [];
      onEvent(state, COMBAT_KILLED, (p) => {
        killed.push(p as DeathPayload);
      });
      onEvent(state, COMBAT_DEATH, (p) => {
        deaths.push(p as DeathPayload);
      });

      damageHealth(eid, 10);
      CombatDeathCleanupSystem.update!(state);

      damageHealth(eid, 5);
      CombatDeathCleanupSystem.update!(state);

      expect(killed.length).toBe(1);
      expect(deaths.length).toBe(1);
    });

    it('healing a corpse back to life resets the death flag so it can die again', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 10;
      Health.max[eid] = 10;

      const killed: DeathPayload[] = [];
      const deaths: DeathPayload[] = [];
      onEvent(state, COMBAT_KILLED, (p) => {
        killed.push(p as DeathPayload);
      });
      onEvent(state, COMBAT_DEATH, (p) => {
        deaths.push(p as DeathPayload);
      });

      damageHealth(eid, 10);
      CombatDeathCleanupSystem.update!(state);
      expect(deaths.length).toBe(1);

      healHealth(eid, 10);
      expect(Health.current[eid]).toBe(10);

      damageHealth(eid, 10);
      CombatDeathCleanupSystem.update!(state);

      expect(killed.length).toBe(2);
      expect(deaths.length).toBe(2);
    });
  });

  describe('healHealth emits combat:healed', () => {
    it('emits healed payload clamped to max', () => {
      const eid = state.createEntity();
      state.addComponent(eid, Health);
      Health.current[eid] = 50;
      Health.max[eid] = 100;

      const heals: DamagedPayload[] = [];
      onEvent(state, COMBAT_HEALED, (p) => {
        heals.push(p as DamagedPayload);
      });

      healHealth(eid, 30);

      expect(heals.length).toBe(1);
      expect(heals[0]).toEqual({ target: eid, amount: 30, newHp: 80 });
    });
  });

  describe('faction hostility matrix (data-driven)', () => {
    it('isHostile reads registry pairs and checks bidirectionally', () => {
      const a = state.createEntity();
      const b = state.createEntity();
      const c = state.createEntity();
      state.addComponent(a, FactionComponent);
      state.addComponent(b, FactionComponent);
      state.addComponent(c, FactionComponent);
      setFaction(state, a, 'player');
      setFaction(state, b, 'enemy');
      setFaction(state, c, 'neutral');

      const matrix: FactionHostilityMatrix = {
        pairs: [
          ['player', 'enemy'],
          ['enemy', 'player'],
        ],
      };
      getDataRegistry(state).register('faction-hostility', 'default', matrix);

      expect(isHostile(state, a, b)).toBe(true);
      expect(isHostile(state, b, a)).toBe(true);
      expect(isHostile(state, a, c)).toBe(false);
    });

    it('returns false when no matrix is registered', () => {
      const a = state.createEntity();
      const b = state.createEntity();
      state.addComponent(a, FactionComponent);
      state.addComponent(b, FactionComponent);
      setFaction(state, a, 'player');
      setFaction(state, b, 'enemy');

      expect(isHostile(state, a, b)).toBe(false);
    });

    it('getFaction/setFaction round-trip built-in tags with stable ids', () => {
      const eid = state.createEntity();
      state.addComponent(eid, FactionComponent);

      setFaction(state, eid, 'player');
      expect(FactionComponent.tag[eid]).toBe(0);
      expect(getFaction(state, eid)).toBe('player');

      setFaction(state, eid, 'enemy');
      expect(FactionComponent.tag[eid]).toBe(1);
      expect(getFaction(state, eid)).toBe('enemy');

      setFaction(state, eid, 'neutral');
      expect(getFaction(state, eid)).toBe('neutral');

      setFaction(state, eid, 'merchant');
      expect(getFaction(state, eid)).toBe('merchant');
    });

    it('supports custom faction tags beyond the built-ins', () => {
      const eid = state.createEntity();
      state.addComponent(eid, FactionComponent);

      setFaction(state, eid, 'boss');
      expect(getFaction(state, eid)).toBe('boss');

      const other = state.createEntity();
      state.addComponent(other, FactionComponent);
      setFaction(state, other, 'boss');
      expect(getFaction(state, other)).toBe('boss');
    });
  });
});
