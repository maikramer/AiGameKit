/**
 * Performance benchmark utility for the VibeGame engine.
 *
 * Captures frame time and render stats as JSON. Works in browser (ESM)
 * and can be called from Playwright `page.evaluate()` or DevTools console.
 *
 * Does NOT import from the engine — the caller passes the renderer's
 * `.info` object so this file stays zero-dependency.
 */

export interface RendererInfo {
  render: {
    calls: number;
    triangles: number;
  };
  programs?: { length: number };
}

export interface BenchmarkResult {
  avgFrameTime: number;
  minFrameTime: number;
  maxFrameTime: number;
  stdDev: number;
  drawCalls: number;
  triangles: number;
  programs: number;
  totalFrames: number;
  duration: number;
}

/**
 * Capture benchmark metrics over N animation frames.
 *
 * @param getRendererInfo - Closure that returns the current `renderer.info`.
 *   This indirection ensures we read the stats *after* all N frames have rendered.
 * @param frames - Number of frames to sample (default 300 ≈ 5 s at 60 fps).
 */
export function captureBenchmark(
  getRendererInfo: () => RendererInfo | null,
  frames: number = 300
): Promise<BenchmarkResult> {
  return new Promise<BenchmarkResult>((resolve) => {
    const timestamps: number[] = [];
    let count = 0;

    const t0 = performance.now();

    function tick(): void {
      const now = performance.now();
      timestamps.push(now);
      count++;

      if (count < frames) {
        requestAnimationFrame(tick);
        return;
      }

      const deltas: number[] = [];
      for (let i = 1; i < timestamps.length; i++) {
        deltas.push(timestamps[i] - timestamps[i - 1]);
      }

      const duration = timestamps[timestamps.length - 1] - t0;

      const sum = deltas.reduce((a, b) => a + b, 0);
      const avg = deltas.length > 0 ? sum / deltas.length : 0;
      const min = deltas.length > 0 ? Math.min(...deltas) : 0;
      const max = deltas.length > 0 ? Math.max(...deltas) : 0;

      const variance =
        deltas.length > 0
          ? deltas.reduce((s, d) => s + (d - avg) ** 2, 0) / deltas.length
          : 0;
      const stdDev = Math.sqrt(variance);

      const info = getRendererInfo();

      resolve({
        avgFrameTime: Math.round(avg * 1000) / 1000,
        minFrameTime: Math.round(min * 1000) / 1000,
        maxFrameTime: Math.round(max * 1000) / 1000,
        stdDev: Math.round(stdDev * 1000) / 1000,
        drawCalls: info?.render.calls ?? 0,
        triangles: info?.render.triangles ?? 0,
        programs: info?.programs?.length ?? 0,
        totalFrames: frames,
        duration: Math.round(duration * 1000) / 1000,
      });
    }

    requestAnimationFrame(tick);
  });
}

/**
 * Helper for DevTools console: pass `renderer` directly.
 *
 * Usage (in browser console):
 *   captureBenchmarkFromRenderer(renderer)
 */
export function captureBenchmarkFromRenderer(
  renderer: { info: RendererInfo },
  frames?: number
): Promise<BenchmarkResult> {
  return captureBenchmark(() => renderer.info, frames);
}
