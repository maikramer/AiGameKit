import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../src/core/ecs/state';
import {
  _resetProfilerForTests,
  beginProfilerFrame,
  enableProfiler,
  endProfilerFrame,
  getProfilerSnapshot,
} from '../../../src/core/profiler';
import { MonoBehaviour } from '../../../src/plugins/entity-script/components';
import {
  getEntityScriptsGlob,
  registerEntityScripts,
  setCachedMonoBehaviourModule,
  setScriptFile,
  setScriptRuntime,
} from '../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../src/plugins/entity-script/plugin';
import {
  getEntityScriptFrameStats,
  scriptSpanName,
} from '../../../src/plugins/entity-script/script-profiler';
import {
  buildContext,
  EntityScriptFixedUpdateSystem,
  EntityScriptSystem,
} from '../../../src/plugins/entity-script/system';
import { TransformsPlugin } from '../../../src/plugins/transforms';

describe('entity-script per-file profiler spans', () => {
  let state: State;

  beforeEach(() => {
    _resetProfilerForTests();
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
    enableProfiler('sample');
  });

  function createScriptedEntity(
    file: string,
    mod: Record<string, unknown>,
    count = 1
  ): void {
    const globKey = `./scripts/${file}`;
    const existing = getEntityScriptsGlob(state) ?? {};
    registerEntityScripts(state, {
      ...existing,
      [globKey]: () => Promise.resolve(mod),
    });
    setCachedMonoBehaviourModule(state, globKey, mod as never);
    for (let i = 0; i < count; i++) {
      const eid = state.createEntity();
      state.addComponent(eid, MonoBehaviour, { ready: 1, enabled: 1 });
      setScriptFile(state, eid, file);
      // The hot loop resolves the runtime from the cache populated by setup;
      // mirror that here so update/fixedUpdate are actually invoked.
      setScriptRuntime(state, eid, {
        mod: mod as never,
        ctx: buildContext(state, eid),
        file,
      });
    }
  }

  it('records separate custom spans per script file', () => {
    let creatureCalls = 0;
    let banditCalls = 0;
    createScriptedEntity(
      'creature.ts',
      {
        update: () => {
          creatureCalls += 1;
          const end = performance.now() + 1;
          while (performance.now() < end) {
            /* busy — keep above timer quantum under parallel suite load */
          }
        },
      },
      3
    );
    createScriptedEntity(
      'bandit.ts',
      {
        update: () => {
          banditCalls += 1;
        },
      },
      2
    );

    // Re-arm after other files may have touched the global profiler singleton.
    enableProfiler('sample');
    beginProfilerFrame();
    state.time.frameCount = 1;
    EntityScriptSystem.update!(state);
    endProfilerFrame(1 / 60);

    expect(creatureCalls).toBe(3);
    expect(banditCalls).toBe(2);

    const snap = getProfilerSnapshot();
    const names = snap.customs.map((c) => c.name);
    expect(names).toContain(scriptSpanName('creature.ts'));
    expect(names).toContain(scriptSpanName('bandit.ts'));

    const creature = snap.customs.find(
      (c) => c.name === scriptSpanName('creature.ts')
    )!;
    const bandit = snap.customs.find(
      (c) => c.name === scriptSpanName('bandit.ts')
    )!;
    expect(creature.lastMs).toBeGreaterThan(bandit.lastMs);

    const stats = getEntityScriptFrameStats();
    expect(stats.find((s) => s.span === 'script/creature')?.entities).toBe(3);
    expect(stats.find((s) => s.span === 'script/bandit')?.entities).toBe(2);
  });

  it('tags fixedUpdate under script/<file>.fixed', () => {
    createScriptedEntity('wolf.ts', {
      fixedUpdate: () => {},
    });

    enableProfiler('sample');
    beginProfilerFrame();
    state.time.frameCount = 2;
    EntityScriptFixedUpdateSystem.update!(state);
    endProfilerFrame(1 / 60);

    const names = getProfilerSnapshot().customs.map((c) => c.name);
    expect(names).toContain('script/wolf.fixed');
  });

  it('does not time scripts when profiler is off', () => {
    _resetProfilerForTests();
    createScriptedEntity('idle.ts', { update: () => {} });
    beginProfilerFrame();
    state.time.frameCount = 3;
    EntityScriptSystem.update!(state);
    endProfilerFrame(1 / 60);
    expect(getProfilerSnapshot().customs).toHaveLength(0);
  });
});
