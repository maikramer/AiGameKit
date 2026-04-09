import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { AiSteeringPlugin } from '../../../src/plugins/ai-steering/plugin';
import { DefaultPlugins } from '../../../src/plugins/defaults';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { I18nPlugin } from '../../../src/plugins/i18n/plugin';
import { JointsPlugin } from '../../../src/plugins/joints/plugin';
import { NavmeshPlugin } from '../../../src/plugins/navmesh/plugin';
import { NetworkPlugin } from '../../../src/plugins/network/plugin';
import { ParticlesPlugin } from '../../../src/plugins/particles/plugin';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';
import { SaveLoadPlugin } from '../../../src/plugins/save-load/plugin';

describe('Engine feature plugins registration', () => {
  beforeEach(() => {
    // no shared state
  });

  it('includes gameplay plugins in DefaultPlugins', () => {
    expect(DefaultPlugins).toContain(RaycastPlugin);
    expect(DefaultPlugins).toContain(NavmeshPlugin);
    expect(DefaultPlugins).toContain(AiSteeringPlugin);
    expect(DefaultPlugins).toContain(ParticlesPlugin);
    expect(DefaultPlugins).toContain(HudPlugin);
    expect(DefaultPlugins).toContain(JointsPlugin);
  });

  it('optional plugins are not in DefaultPlugins', () => {
    expect(DefaultPlugins).not.toContain(SaveLoadPlugin);
    expect(DefaultPlugins).not.toContain(NetworkPlugin);
    expect(DefaultPlugins).not.toContain(I18nPlugin);
  });

  it('registers raycast components', () => {
    const state = new State();
    state.registerPlugin(RaycastPlugin);
    expect(state.getComponent('raycast-source')).toBeDefined();
    expect(state.getComponent('raycast-result')).toBeDefined();
  });

  it('registers navmesh components', () => {
    const state = new State();
    state.registerPlugin(NavmeshPlugin);
    expect(state.getComponent('nav-mesh')).toBeDefined();
    expect(state.getComponent('nav-agent')).toBeDefined();
  });
});
