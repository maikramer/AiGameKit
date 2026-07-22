# Core Profiler (context.md)

<!-- LLM:OVERVIEW -->

Process-wide frame profiler session used by `Scheduler`, `GameRuntime`, and `ProfilerPlugin`. When disabled (`mode === 'off'`), hot paths take a single boolean check. When enabled (`sample` or `deep`), records per-system and per-group milliseconds into a 120-frame ring, supports manual `withSpan` / `beginSpan` / `endSpan`, and optionally emits `performance.mark` / `measure` for Chrome Performance.

<!-- /LLM:OVERVIEW -->

## Layout

```
profiler/
├── context.md  # This file
├── index.ts    # Re-exports
└── session.ts  # Global session state + API
```

## Frame ownership

- **Headless / `state.step` alone**: scheduler begins and ends the frame around ECS groups.
- **GameRuntime**: calls `beginExternalProfilerFrame` before `step`, times the WebGL submit with `profileRenderPass`, then `endExternalProfilerFrame` so `render` is included in the same snapshot.
