import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { registerDebugVar } from '../../../src/plugins/debug/registry';
import {
  recordDiagnostic,
  clearDiagnostics,
} from '../../../src/plugins/debug/diagnostics';
import {
  buildAgentReport,
  buildAssetsSummary,
} from '../../../src/plugins/debug/report';

const Pos = {
  x: new Float32Array(64),
  y: new Float32Array(64),
};

function freshState(): State {
  const state = new State();
  state.headless = true;
  state.registerComponent('pos', Pos);
  return state;
}

beforeEach(() => clearDiagnostics());

describe('agent report', () => {
  it('dumps world counters, named entities, registry and loading gates', () => {
    const state = freshState();
    const eid = state.createEntity();
    state.setEntityName('hero', eid);
    state.addComponent(eid, Pos, { x: 1.5, y: -2 });
    registerDebugVar(state, 'player.hp', () => 100);

    const report = buildAgentReport(state);

    expect(report.world.entities).toBe(1);
    expect(report.world.namedEntities).toBe(1);
    expect(report.loading.physicsHeld).toBe(false);
    expect(report.meta.headless).toBe(true);
    expect(typeof report.meta.timestamp).toBe('string');

    expect(report.entities).toHaveLength(1);
    expect(report.entities![0]).toMatchObject({
      name: 'hero',
      components: { pos: { x: 1.5, y: -2 } },
    });

    expect(report.registry.vars['player.hp']).toBe('100');
    expect(report.registry.actions).toEqual([]);

    expect(report.logs).toEqual([]);
    expect(report.logsTruncated).toBe(false);
    expect(report.errors).toEqual([]);
    expect(report.assets.observed).toBe(0);
    expect(report.entitiesTruncated).toBeUndefined();
  });

  it('caps the entity dump and flags truncation', () => {
    const state = freshState();
    for (const name of ['a', 'b', 'c']) {
      const eid = state.createEntity();
      state.setEntityName(name, eid);
    }

    const report = buildAgentReport(state, { maxEntities: 2 });

    expect(report.world.namedEntities).toBe(3);
    expect(report.entities).toHaveLength(2);
    expect(report.entitiesTruncated).toBe(true);
  });

  it('supports skipEntities and unnamed entities', () => {
    const state = freshState();
    state.setEntityName('named', state.createEntity());
    state.createEntity();

    expect(
      buildAgentReport(state, { skipEntities: true }).entities
    ).toBeUndefined();
    expect(buildAgentReport(state).entities).toHaveLength(1);
    expect(
      buildAgentReport(state, { includeUnnamedEntities: true }).entities
    ).toHaveLength(2);
  });

  it('bounds the log slice and flags truncation', () => {
    const state = freshState();
    for (let i = 0; i < 5; i++) {
      recordDiagnostic('console', `msg-${i}`, { level: 'info' });
    }

    const report = buildAgentReport(state, { logs: { limit: 3 } });

    expect(report.logs.map((e) => e.message)).toEqual([
      'msg-2',
      'msg-3',
      'msg-4',
    ]);
    expect(report.logsTruncated).toBe(true);
  });

  it('aggregates captured errors into the report', () => {
    const state = freshState();
    recordDiagnostic('uncaught', 'boom');
    recordDiagnostic('uncaught', 'boom');
    recordDiagnostic('webgl', 'Shader compile failed');

    const report = buildAgentReport(state, { skipEntities: true });

    expect(report.errors[0]).toMatchObject({
      kind: 'uncaught',
      message: 'boom',
      count: 2,
    });
    expect(report.errors[1].kind).toBe('webgl');
  });

  it('lists failed resource loads in the assets summary', () => {
    recordDiagnostic('resource', 'Failed to load <img> /missing.glb', {
      url: '/missing.glb',
    });

    const assets = buildAssetsSummary();
    expect(assets.failed).toHaveLength(1);
    expect(assets.failed[0].url).toBe('/missing.glb');
  });
});
