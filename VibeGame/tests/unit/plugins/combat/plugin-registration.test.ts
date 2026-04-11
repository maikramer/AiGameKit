import { describe, expect, it } from 'bun:test';
import { CombatPlugin } from '../../../../src/plugins/combat/plugin';

describe('CombatPlugin registration', () => {
  it('should register health component', () => {
    expect(CombatPlugin.components).toBeDefined();
    expect(CombatPlugin.components!['health']).toBeDefined();
  });

  it('should register projectileData component', () => {
    expect(CombatPlugin.components).toBeDefined();
    expect(CombatPlugin.components!['projectileData']).toBeDefined();
  });

  it('should have config defaults for health', () => {
    const defaults = CombatPlugin.config?.defaults;
    expect(defaults).toBeDefined();
    expect(defaults!['health']).toEqual({ current: 100, max: 100 });
  });

  it('should have config defaults for projectileData', () => {
    const defaults = CombatPlugin.config?.defaults;
    expect(defaults).toBeDefined();
    expect(defaults!['projectileData']).toEqual({ damage: 10, ownerEid: 0, lifetime: 3.0, age: 0 });
  });

  it('should have systems as an array', () => {
    expect(Array.isArray(CombatPlugin.systems)).toBe(true);
  });

  it('should NOT be in DefaultPlugins', async () => {
    const fs = await import('fs');
    const path = await import('path');
    const defaultsPath = path.join(import.meta.dir, '../../../../src/defaults.ts');
    const content = fs.readFileSync(defaultsPath, 'utf-8');
    expect(content.includes('CombatPlugin')).toBe(false);
  });
});
