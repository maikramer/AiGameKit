import { describe, expect, it } from 'bun:test';
import { createEntityFromRecipe } from '../../../src/core/recipes/parser';
import { expandShorthands } from '../../../src/core/recipes/shorthand-expander';
import { State } from '../../../src/core/ecs/state';
import { MonoBehaviour } from '../../../src/plugins/entity-script/components';
import {
  coerceMonoBehaviourModule,
  getEntityScriptsGlob,
  getScriptFile,
  registerEntityScripts,
  resolveEntityScriptGlobKey,
} from '../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../src/plugins/entity-script/plugin';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';
import { TransformsPlugin } from '../../../src/plugins/transforms';

describe('MonoBehaviour', () => {
  it('expandShorthands maps script= to entity-script file for gltf-load', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(GltfXmlPlugin);
    state.registerPlugin(EntityScriptPlugin);

    const recipe = state.getRecipe('GLTFLoader');
    expect(recipe).toBeDefined();

    const expanded = expandShorthands(
      {
        url: '/assets/m.glb',
        script: 'cristal.ts',
      },
      recipe!,
      state
    );

    expect(expanded['mono-behaviour']).toBe('file: cristal.ts');
  });

  it('createEntityFromRecipe with script stores file and adds EntityScript', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(GltfXmlPlugin);
    state.registerPlugin(EntityScriptPlugin);

    const eid = createEntityFromRecipe(state, 'GLTFLoader', {
      url: '/assets/m.glb',
      transform: 'pos: 0 0 0',
      script: 'cristal.ts',
    });

    expect(state.hasComponent(eid, MonoBehaviour)).toBe(true);
    expect(MonoBehaviour.ready[eid]).toBe(0);
    expect(getScriptFile(state, eid)).toBe('cristal.ts');
  });

  it('resolveEntityScriptGlobKey matches basename', () => {
    const glob = {
      './src/scripts/cristal.ts': () => Promise.resolve({}),
      './other/foo.ts': () => Promise.resolve({}),
    };
    expect(resolveEntityScriptGlobKey(glob, 'cristal.ts')).toBe(
      './src/scripts/cristal.ts'
    );
  });

  it('coerceEntityScriptModule reads named exports', () => {
    const start = () => {};
    const update = () => {};
    const mod = coerceMonoBehaviourModule({ start, update });
    expect(mod?.start).toBe(start);
    expect(mod?.update).toBe(update);
  });

  it('registerEntityScripts stores glob on state', () => {
    const state = new State();
    const g = { './a.ts': () => Promise.resolve({}) };
    registerEntityScripts(state, g);
    expect(getEntityScriptsGlob(state)).toBe(g);
  });
});
