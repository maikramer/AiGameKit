import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { MonoBehaviour } from '../../../../src/plugins/entity-script/components';
import {
  getScriptLoadRetry,
  registerEntityScripts,
  SCRIPT_LOAD_MAX_ATTEMPTS,
  scriptLoadRetryGate,
  setScriptFile,
} from '../../../../src/plugins/entity-script/context';
import { EntityScriptSystem } from '../../../../src/plugins/entity-script/system';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
};

describe('entity-script module load retry', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  function createScriptedEntity(
    file: string,
    loader: () => Promise<unknown>
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled: 1 });
    setScriptFile(state, eid, file);
    registerEntityScripts(state, { [`./scripts/${file}`]: loader });
    return eid;
  }

  it('transient failure keeps the entity pending and retries to success', async () => {
    let calls = 0;
    let started = 0;
    const eid = createScriptedEntity('flaky.ts', () => {
      calls++;
      if (calls === 1) {
        return Promise.reject(new Error('outdated optimize dep'));
      }
      return Promise.resolve({
        start: () => {
          started++;
        },
      });
    });

    // First attempt fails — the entity must NOT be latched as done.
    EntityScriptSystem.update!(state);
    await flush();
    expect(calls).toBe(1);
    expect(MonoBehaviour.ready[eid]).toBe(0);

    // Cooldown: no new attempt while the backoff window is open.
    const retry = getScriptLoadRetry(state, './scripts/flaky.ts');
    expect(retry?.attempts).toBe(1);
    EntityScriptSystem.update!(state);
    await flush();
    expect(calls).toBe(1);
    expect(scriptLoadRetryGate(state, './scripts/flaky.ts', 0)).toBe(
      'cooldown'
    );

    // Backoff expired: the retry succeeds, start runs, retry state clears.
    state.time.elapsed = (retry?.nextAttemptAt ?? 0) + 0.001;
    EntityScriptSystem.update!(state);
    await flush();
    expect(calls).toBe(2);
    expect(started).toBe(1);
    expect(MonoBehaviour.ready[eid]).toBe(1);
    expect(getScriptLoadRetry(state, './scripts/flaky.ts')).toBeUndefined();
  });

  it('a permanently broken module exhausts attempts and latches ready', async () => {
    let calls = 0;
    const eid = createScriptedEntity('broken.ts', () => {
      calls++;
      return Promise.reject(new Error('syntax error'));
    });

    EntityScriptSystem.update!(state);
    await flush();

    let guard = 0;
    while (MonoBehaviour.ready[eid] === 0 && guard++ < 50) {
      const retry = getScriptLoadRetry(state, './scripts/broken.ts');
      state.time.elapsed = (retry?.nextAttemptAt ?? 0) + 0.001;
      EntityScriptSystem.update!(state);
      await flush();
    }

    expect(calls).toBe(SCRIPT_LOAD_MAX_ATTEMPTS);
    expect(
      scriptLoadRetryGate(state, './scripts/broken.ts', state.time.elapsed)
    ).toBe('exhausted');
    expect(MonoBehaviour.ready[eid]).toBe(1);
  });

  it('start() failure latches immediately without re-running start', async () => {
    let starts = 0;
    const eid = createScriptedEntity('badstart.ts', () =>
      Promise.resolve({
        start: () => {
          starts++;
          throw new Error('boom');
        },
      })
    );

    EntityScriptSystem.update!(state);
    await flush();
    expect(starts).toBe(1);
    expect(MonoBehaviour.ready[eid]).toBe(1);

    // The module loaded fine — no retry cycle, start must not run again.
    EntityScriptSystem.update!(state);
    await flush();
    expect(starts).toBe(1);
  });

  it('destroy after a successful retry fires onDestroy exactly once', async () => {
    let destroys = 0;
    let calls = 0;
    const eid = createScriptedEntity('flaky2.ts', () => {
      calls++;
      if (calls === 1) {
        return Promise.reject(new Error('server restart'));
      }
      return Promise.resolve({
        start: () => {},
        onDestroy: () => {
          destroys++;
        },
      });
    });

    EntityScriptSystem.update!(state);
    await flush();
    const retry = getScriptLoadRetry(state, './scripts/flaky2.ts');
    state.time.elapsed = (retry?.nextAttemptAt ?? 0) + 0.001;
    EntityScriptSystem.update!(state);
    await flush();
    expect(MonoBehaviour.ready[eid]).toBe(1);

    state.destroyEntity(eid);
    expect(destroys).toBe(1);
  });
});
