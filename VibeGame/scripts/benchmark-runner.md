# Performance Benchmark

## Via Playwright (browser)

```js
// After the engine is running, grab the renderer from the RenderingContext:
const state = window.__vibegame_state;  // or however you access State
const ctx = getRenderingContext(state);
const { captureBenchmarkFromRenderer } = await import('/scripts/benchmark-snippet.ts');
const result = await captureBenchmarkFromRenderer(ctx.renderer);
console.log(JSON.stringify(result, null, 2));
```

## Via DevTools Console

Open DevTools console in any running VibeGame app (e.g. simple-rpg), paste:

```js
// Assumes renderer is accessible via the engine internals
const { captureBenchmark } = await import('/scripts/benchmark-snippet.ts');
const result = await captureBenchmark(() => renderer.info, 300);
console.log(JSON.stringify(result, null, 2));
```

## Result fields

| Field          | Description                      |
| -------------- | -------------------------------- |
| `avgFrameTime` | Mean frame delta (ms)            |
| `minFrameTime` | Fastest frame (ms)               |
| `maxFrameTime` | Slowest frame (ms)               |
| `stdDev`       | Standard deviation (ms)          |
| `drawCalls`    | `renderer.info.render.calls`     |
| `triangles`    | `renderer.info.render.triangles` |
| `programs`     | Number of active WebGL programs  |
| `totalFrames`  | Frames sampled                   |
| `duration`     | Total wall-clock time (ms)       |
