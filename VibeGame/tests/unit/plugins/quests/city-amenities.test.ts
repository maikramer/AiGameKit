import { describe, expect, it } from 'bun:test';

import {
  BOMB_CRAFT_STONE,
  BOMB_CRAFT_WOOD,
  CAMPFIRE_COOLDOWN,
  CAMPFIRE_HEAL,
  LOOKOUT_GATES,
  LOOKOUT_WAYPOINT_PREFIX,
  WELL_COOLDOWN,
  WELL_HEAL,
  canCraftBomb,
} from '../../../../examples/simple-rpg/src/game/city-amenities';

describe('city amenities', () => {
  it('heals less at the well than at the campfire, with a shorter wait', () => {
    expect(WELL_HEAL).toBeLessThan(CAMPFIRE_HEAL);
    expect(WELL_COOLDOWN).toBeLessThan(CAMPFIRE_COOLDOWN);
    expect(CAMPFIRE_HEAL).toBeGreaterThan(0);
    expect(WELL_HEAL).toBeGreaterThan(0);
  });

  it('crafts a bomb from scrap cheaper than selling the mats', () => {
    expect(canCraftBomb(BOMB_CRAFT_STONE, BOMB_CRAFT_WOOD)).toBe(true);
    expect(canCraftBomb(BOMB_CRAFT_STONE - 1, BOMB_CRAFT_WOOD)).toBe(false);
    expect(canCraftBomb(BOMB_CRAFT_STONE, BOMB_CRAFT_WOOD - 1)).toBe(false);
    expect(canCraftBomb(0, 0)).toBe(false);
  });

  it('pins four cardinal gates that match the respawn ring', () => {
    expect(LOOKOUT_GATES.length).toBe(4);
    const byId = Object.fromEntries(LOOKOUT_GATES.map((g) => [g.id, g]));
    expect(byId['lookout:forest']).toEqual(
      expect.objectContaining({ x: 0, z: 50 })
    );
    expect(byId['lookout:desert']).toEqual(
      expect.objectContaining({ x: 50, z: 0 })
    );
    expect(byId['lookout:swamp']).toEqual(
      expect.objectContaining({ x: 0, z: -50 })
    );
    expect(byId['lookout:peaks']).toEqual(
      expect.objectContaining({ x: -50, z: 0 })
    );
    for (const gate of LOOKOUT_GATES) {
      expect(gate.id.startsWith(LOOKOUT_WAYPOINT_PREFIX)).toBe(true);
      expect(gate.label.length).toBeGreaterThan(0);
    }
  });
});
