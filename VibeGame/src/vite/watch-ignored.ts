import type { WatchOptions } from 'vite';

/**
 * Chokidar / Vite `server.watch.ignored` patterns for game examples.
 *
 * Keeps HMR on source (`src/`, HTML, TS) while skipping VCS, caches,
 * pipeline workdirs, and huge runtime blobs that blow the inotify limit
 * (ENOSPC). Static files under `public/` are still served — only watch
 * is disabled.
 */
export const VIBEGAME_SERVER_WATCH_IGNORED: readonly string[] = [
  // Knowledge-graph / agent caches (huge AST trees)
  '**/graphify-out/**',

  // VCS + package installs (re-include linked vibegame for HMR)
  '**/.git/**',
  '**/node_modules/**',
  '!**/node_modules/vibegame/**',

  // Build / tool caches
  '**/dist/**',
  '**/build/**',
  '**/coverage/**',
  '**/.cache/**',
  '**/.turbo/**',
  '**/.vite/**',
  '**/.rollup.cache/**',

  // GameAssets / GPU pipeline artefacts (not app source)
  '**/sample-gameassets/**',
  '**/.gameassets_work/**',
  '**/_intermediate/**',
  '**/_part3d_*/**',
  '**/_unused_*/**',
  '**/_rig_backup*/**',
  '**/_retarget_*/**',
  '**/_city_gen/**',
  '**/manifest*.yaml.bak*',
  '**/*.bak',
  '**/*.bak_*',
  '**/*.orig',

  // Local debug / agent scratch
  '**/.playwright-mcp/**',
  '**/.sisyphus/**',
  '**/logs/**',
  '**/playwright-report/**',
  '**/test-results/**',

  // Huge runtime blobs — no HMR watch (ENOSPC). Still served at runtime;
  // `vibegamePublicLiveServe` covers files added after Vite's public Set snapshot.
  '**/public/assets/**',
  '**/*.glb',
  '**/*.gltf',
  '**/*.ktx2',
  '**/*.wasm',
];

/** Vite/chokidar `server.watch.ignored` (string | RegExp | fn | array). */
export type WatchIgnored = NonNullable<WatchOptions['ignored']>;

/**
 * Merge Vibegame ENOSPC defaults with any existing Vite `ignored` value.
 *
 * Preserves user matchers as-is (including `AnymatchFn`); always returns an
 * array so defaults and prior config both apply.
 */
export function mergeWatchIgnored(
  existing: WatchOptions['ignored']
): WatchIgnored {
  const fromUser = Array.isArray(existing)
    ? existing
    : existing !== undefined
      ? [existing]
      : [];
  return [...VIBEGAME_SERVER_WATCH_IGNORED, ...fromUser];
}
