import type { Plugin } from 'vite';
import { vibegameForceFullReload } from './force-full-reload.ts';
import { vibegamePublicLiveServe } from './public-live-serve.ts';
import {
  type SharedAssetsOptions,
  vibegameSharedAssets,
} from './shared-assets.ts';
import { silenceTyprOpentypeNoise } from './silence-typr.ts';
import { silenceRapierWasmSourcemapNoise } from './silence-rapier-sourcemap.ts';
import { mergeWatchIgnored } from './watch-ignored.ts';

/** WASM glue breaks Vite prebundle sourcemaps in Firefox DevTools. */
const OPTIMIZE_DEPS_EXCLUDE = [
  '@pmndrs/uikit',
  'yoga-layout',
  'recast-navigation',
  '@recast-navigation/three',
  '@dimforge/rapier3d-compat',
  '@dimforge/rapier3d',
] as const;

export interface VibegameOptions {
  /**
   * Asset pool shared by every example (`examples/shared-assets/public/assets`).
   * Served after the example's own `public/` misses, and copied into the build —
   * one pool, no symlinks and no duplicated binaries per example.
   */
  sharedAssets?: SharedAssetsOptions | string;
}

export function vibegame(options: VibegameOptions = {}): Plugin[] {
  const shared =
    typeof options.sharedAssets === 'string'
      ? { dir: options.sharedAssets }
      : options.sharedAssets;
  return [
    vibegamePublicLiveServe(),
    ...(shared ? [vibegameSharedAssets(shared)] : []),
    silenceTyprOpentypeNoise(),
    silenceRapierWasmSourcemapNoise(),
    {
      name: 'aigamekit-vibegame',
      config: (config) => {
        config.resolve = config.resolve || {};
        config.resolve.alias = {
          ...config.resolve.alias,
          '@dimforge/rapier3d': '@dimforge/rapier3d-compat',
        };

        config.server = config.server || {};
        config.server.watch = config.server.watch || {};
        config.server.watch.ignored = mergeWatchIgnored(
          config.server.watch.ignored
        );

        config.optimizeDeps = config.optimizeDeps || {};
        const exclude = new Set([
          ...(config.optimizeDeps.exclude ?? []),
          ...OPTIMIZE_DEPS_EXCLUDE,
        ]);
        config.optimizeDeps.exclude = [...exclude];
      },
    },
    // Last: suppress soft HMR for engine/example TS (WebGL/WASM leak in Firefox).
    vibegameForceFullReload(),
  ];
}

export { consoleForwarding } from './console-plugin.ts';
export {
  shouldForceFullReload,
  vibegameForceFullReload,
} from './force-full-reload.ts';
export { vibegameAssetHotReload } from './hot-reload.ts';
export { initWorldHotReload } from './world-hmr-client.ts';
export { vibegameWorldHmr } from './world-hmr.ts';
export { vibegamePublicLiveServe } from './public-live-serve.ts';
export {
  collectSharedFiles,
  DEFAULT_SHARED_EXCLUDE,
  DEFAULT_SHARED_PREFIXES,
  isSharedAssetUrl,
  resolveSharedAsset,
  type SharedAssetsOptions,
  sharedAssetsMiddleware,
  vibegameSharedAssets,
} from './shared-assets.ts';
export { silenceTyprOpentypeNoise } from './silence-typr.ts';
export { silenceRapierWasmSourcemapNoise } from './silence-rapier-sourcemap.ts';
export {
  mergeWatchIgnored,
  VIBEGAME_SERVER_WATCH_IGNORED,
} from './watch-ignored.ts';
