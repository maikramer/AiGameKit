import type { Page } from '@playwright/test';

export async function screenshotCanvas(
  page: Page,
  canvasSelector = '#game-canvas'
): Promise<Uint8Array> {
  const canvas = page.locator(canvasSelector);
  const buf = await canvas.screenshot();
  return new Uint8Array(buf);
}

export async function probeCanvasPixel(
  page: Page,
  x: number,
  y: number,
  canvasSelector = '#game-canvas'
): Promise<{ r: number; g: number; b: number; a: number }> {
  return page.evaluate(
    ([selector, px, py]) => {
      const canvas = document.querySelector(selector) as HTMLCanvasElement;
      if (!canvas) return { r: 0, g: 0, b: 0, a: 0 };
      const gl = canvas.getContext('webgl2');
      if (!gl) {
        const ctx = canvas.getContext('2d');
        if (!ctx) return { r: 0, g: 0, b: 0, a: 0 };
        const data = ctx.getImageData(px, py, 1, 1).data;
        return { r: data[0], g: data[1], b: data[2], a: data[3] };
      }
      const pixel = new Uint8Array(4);
      gl.readPixels(
        px,
        canvas.height - py - 1,
        1,
        1,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        pixel
      );
      return {
        r: pixel[0],
        g: pixel[1],
        b: pixel[2],
        a: pixel[3],
      };
    },
    [canvasSelector, x, y] as [string, number, number]
  );
}

export async function canvasDimensions(
  page: Page,
  canvasSelector = '#game-canvas'
): Promise<{ width: number; height: number }> {
  const canvas = page.locator(canvasSelector);
  const box = await canvas.boundingBox();
  return box
    ? { width: Math.round(box.width), height: Math.round(box.height) }
    : { width: 0, height: 0 };
}
