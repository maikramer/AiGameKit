import { describe, expect, it } from 'bun:test';
import {
  type AgentConfig,
  collectNavmeshGeometry,
  createAgent,
  isNavMeshReady,
  type NavMeshGeometry,
  NavMeshPlugin,
  navMeshAgentRecipe,
  navMeshRecipe,
  navMeshWalkableRecipe,
  State,
} from 'vibegame';

describe('NavMesh recipes', () => {
  it('declares the surface recipe standalone and the walkable/agent recipes as merge components', () => {
    expect(navMeshRecipe.name).toBe('NavMesh');
    expect(navMeshRecipe.components).toEqual(['nav-mesh-surface']);
    expect(navMeshRecipe.merge).toBeFalsy();

    expect(navMeshWalkableRecipe.name).toBe('NavMeshWalkable');
    expect(navMeshWalkableRecipe.merge).toBe(true);
    expect(navMeshWalkableRecipe.components).toEqual(['nav-mesh-walkable']);

    expect(navMeshAgentRecipe.name).toBe('NavMeshAgent');
    expect(navMeshAgentRecipe.merge).toBe(true);
    expect(navMeshAgentRecipe.components).toEqual(['nav-mesh-agent']);
  });
});

describe('NavMeshPlugin registration', () => {
  it('bundles the two systems, three recipes and three components', () => {
    expect(NavMeshPlugin.systems).toHaveLength(2);
    expect(NavMeshPlugin.recipes?.map((r) => r.name)).toEqual([
      'NavMesh',
      'NavMeshWalkable',
      'NavMeshAgent',
    ]);
    expect(Object.keys(NavMeshPlugin.components ?? {}).sort()).toEqual([
      'nav-mesh-agent',
      'nav-mesh-surface',
      'nav-mesh-walkable',
    ]);
  });

  it('provides component defaults that match the component pre-fill', () => {
    const agentDefaults = NavMeshPlugin.config?.defaults?.['nav-mesh-agent'];
    expect(agentDefaults).toBeDefined();
    expect(agentDefaults?.agentIndex).toBe(-1);
    expect(agentDefaults?.speed).toBe(3);
    expect(agentDefaults?.radius).toBeCloseTo(0.4, 5);
    expect(agentDefaults?.height).toBeCloseTo(1.0, 5);
    expect(agentDefaults?.enabled).toBe(1);

    const surfaceDefaults =
      NavMeshPlugin.config?.defaults?.['nav-mesh-surface'];
    expect(surfaceDefaults?.enabled).toBe(1);
    expect(surfaceDefaults?.generated).toBe(0);
  });
});

describe('NavMesh geometry collection & readiness', () => {
  it('returns typed, empty geometry when no terrain or obstacles are present', () => {
    const state = new State();
    const geom = collectNavmeshGeometry(state) as NavMeshGeometry;
    expect(geom.positions).toBeInstanceOf(Float32Array);
    expect(geom.indices).toBeInstanceOf(Uint32Array);
    expect(geom.positions.length).toBe(0);
    expect(geom.indices.length).toBe(0);

    const geomCustom = collectNavmeshGeometry(state, 64, 60);
    expect(geomCustom.indices.length).toBe(0);
  });

  it('reports no ready navmesh until generation has run', () => {
    expect(isNavMeshReady()).toBe(false);
  });

  it('createAgent refuses to spawn before the crowd is ready', () => {
    const state = new State();
    const config: AgentConfig = { speed: 4.5, radius: 0.5, height: 1.8 };
    const idx = createAgent(state, 0, config);
    expect(idx).toBe(-1);
  });
});
