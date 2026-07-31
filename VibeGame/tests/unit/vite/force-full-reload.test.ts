import { describe, expect, it } from 'bun:test';
import { shouldForceFullReload } from '../../../src/vite/force-full-reload';

describe('shouldForceFullReload', () => {
  it('forces reload for engine TypeScript under VibeGame/src', () => {
    expect(
      shouldForceFullReload(
        '/home/me/AiGameKit/VibeGame/src/extras/gltf-bridge.ts'
      )
    ).toBe(true);
  });

  it('forces reload for example src', () => {
    expect(
      shouldForceFullReload(
        '/home/me/AiGameKit/VibeGame/examples/simple-rpg/src/main.ts'
      )
    ).toBe(true);
  });

  it('forces reload for files under Vite root src/', () => {
    expect(
      shouldForceFullReload(
        '/home/me/AiGameKit/VibeGame/examples/simple-rpg/src/game/foo.ts',
        '/home/me/AiGameKit/VibeGame/examples/simple-rpg'
      )
    ).toBe(true);
  });

  it('does not force reload for GLB/PNG assets', () => {
    expect(
      shouldForceFullReload(
        '/home/me/AiGameKit/VibeGame/examples/simple-rpg/public/assets/a.glb'
      )
    ).toBe(false);
  });

  it('does not force reload for markdown', () => {
    expect(
      shouldForceFullReload(
        '/home/me/AiGameKit/VibeGame/src/plugins/foo/context.md'
      )
    ).toBe(false);
  });
});
