import { describe, expect, it, mock } from 'bun:test';
import { syncComposerSize } from '../../../src/plugins/postprocessing/composer';

describe('syncComposerSize', () => {
  it('returns false when renderer and canvas have no size', () => {
    const setSize = mock(() => {});
    const renderer = {
      getSize(out: {
        x: number;
        y: number;
        set?: (x: number, y: number) => void;
      }) {
        out.x = 0;
        out.y = 0;
        return out;
      },
      getDrawingBufferSize(out: { x: number; y: number }) {
        out.x = 0;
        out.y = 0;
        return out;
      },
      setSize: mock(() => {}),
      domElement: { clientWidth: 0, clientHeight: 0 },
    };
    const prevInner = globalThis.window;
    (globalThis as any).window = { innerWidth: 0, innerHeight: 0 };
    try {
      expect(syncComposerSize({ setSize }, renderer as never)).toBe(false);
      expect(setSize).not.toHaveBeenCalled();
    } finally {
      (globalThis as any).window = prevInner;
    }
  });

  it('sizes composer from renderer.getSize when positive', () => {
    const setSize = mock(() => {});
    const renderer = {
      getSize(out: { x: number; y: number }) {
        out.x = 1280;
        out.y = 720;
        return out;
      },
      getDrawingBufferSize(out: { x: number; y: number }) {
        // Firefox can report 0 here before first present — must still succeed.
        out.x = 0;
        out.y = 0;
        return out;
      },
      setSize: mock(() => {}),
      domElement: { clientWidth: 1280, clientHeight: 720 },
    };
    expect(syncComposerSize({ setSize }, renderer as never)).toBe(true);
    expect(setSize).toHaveBeenCalledWith(1280, 720);
  });

  it('falls back to canvas client size then syncs composer', () => {
    const setSize = mock(() => {});
    const rendererSetSize = mock(
      (_w: number, _h: number, _updateStyle?: boolean) => {}
    );
    let logicalW = 0;
    let logicalH = 0;
    const renderer = {
      getSize(out: { x: number; y: number }) {
        out.x = logicalW;
        out.y = logicalH;
        return out;
      },
      getDrawingBufferSize(out: { x: number; y: number }) {
        out.x = logicalW;
        out.y = logicalH;
        return out;
      },
      setSize: (w: number, h: number, updateStyle?: boolean) => {
        logicalW = w;
        logicalH = h;
        rendererSetSize(w, h, updateStyle);
      },
      domElement: { clientWidth: 800, clientHeight: 600 },
    };
    expect(syncComposerSize({ setSize }, renderer as never)).toBe(true);
    expect(rendererSetSize).toHaveBeenCalledWith(800, 600, false);
    expect(setSize).toHaveBeenCalledWith(800, 600);
  });
});
