import type { Page } from '@playwright/test';

export interface EntityData {
  eid: number;
  name: string | null;
  components: Record<string, Record<string, number>>;
}

export interface GameState {
  elapsed: number;
  entities: EntityData[];
}

type Bridge = {
  snapshot: () => string;
  entities: () => EntityData[];
  entity: (name: string) => EntityData | null;
  component: (eid: number, name: string) => Record<string, number> | null;
  query: (...names: string[]) => number[];
  componentNames: () => string[];
  namedEntities: () => Array<{ name: string; eid: number }>;
  step: (dt?: number) => void;
};

function getBridge(): Bridge {
  return (window as unknown as Record<string, unknown>).__VIBEGAME__ as Bridge;
}

export class GameInspector {
  constructor(private page: Page) {}

  async isReady(): Promise<boolean> {
    return this.page.evaluate(() => {
      return (
        typeof window !== 'undefined' &&
        !!(window as unknown as Record<string, unknown>).__VIBEGAME__
      );
    });
  }

  async waitForBridge(timeout = 15000): Promise<void> {
    await this.page.waitForFunction(
      () => !!(window as unknown as Record<string, unknown>).__VIBEGAME__,
      undefined,
      { timeout }
    );
  }

  async snapshot(): Promise<string> {
    return this.page.evaluate(() => getBridge().snapshot());
  }

  async entities(): Promise<EntityData[]> {
    return this.page.evaluate(() => getBridge().entities());
  }

  async entity(name: string): Promise<EntityData | null> {
    return this.page.evaluate((n) => getBridge().entity(n), name);
  }

  async component(
    eid: number,
    name: string
  ): Promise<Record<string, number> | null> {
    return this.page.evaluate(([e, n]) => getBridge().component(e, n), [
      eid,
      name,
    ] as [number, string]);
  }

  async query(...componentNames: string[]): Promise<number[]> {
    return this.page.evaluate(
      (names) => getBridge().query(...names),
      componentNames
    );
  }

  async componentNames(): Promise<string[]> {
    return this.page.evaluate(() => getBridge().componentNames());
  }

  async namedEntities(): Promise<Array<{ name: string; eid: number }>> {
    return this.page.evaluate(() => getBridge().namedEntities());
  }

  async step(dt?: number): Promise<void> {
    await this.page.evaluate((delta) => getBridge().step(delta), dt);
  }

  async captureConsoleErrors(): Promise<string[]> {
    return this.page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      return (w.__VIBEGAME_CONSOLE_ERRORS as string[]) ?? [];
    });
  }

  async captureWebGLErrors(): Promise<string[]> {
    return this.page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      return (w.__VIBEGAME_WEBGL_ERRORS as string[]) ?? [];
    });
  }
}

export function installConsoleCapture(page: Page): void {
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      page
        .evaluate((text) => {
          const w = window as unknown as Record<string, unknown>;
          if (!w.__VIBEGAME_CONSOLE_ERRORS) w.__VIBEGAME_CONSOLE_ERRORS = [];
          (w.__VIBEGAME_CONSOLE_ERRORS as string[]).push(text);
        }, msg.text())
        .catch(() => {});
    }
  });
}

export async function injectWebGLErrorCapture(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__VIBEGAME_WEBGL_ERRORS = [];

    const proto = HTMLCanvasElement.prototype as any;
    const origGetContext = proto.getContext.bind(proto);
    proto.getContext = function (contextId: string, ...args: unknown[]) {
      const ctx = origGetContext(
        contextId,
        ...args
      ) as WebGL2RenderingContext | null;
      if (contextId === 'webgl2' && ctx && 'getError' in ctx) {
        const gl = ctx;
        const origCompileShader = gl.compileShader.bind(gl);
        gl.compileShader = function (shader: unknown) {
          origCompileShader(shader);
          if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            const info = gl.getShaderInfoLog(shader);
            if (info) {
              (w.__VIBEGAME_WEBGL_ERRORS as string[]).push(
                `Shader compile: ${info}`
              );
            }
          }
        };

        const origLinkProgram = gl.linkProgram.bind(gl);
        gl.linkProgram = function (program: unknown) {
          origLinkProgram(program);
          if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            const info = gl.getProgramInfoLog(program);
            if (info) {
              (w.__VIBEGAME_WEBGL_ERRORS as string[]).push(
                `Program link: ${info}`
              );
            }
          }
        };
      }
      return ctx;
    };
  });
}
