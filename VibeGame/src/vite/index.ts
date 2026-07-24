import type { Plugin } from 'vite';
import { vibegamePublicLiveServe } from './public-live-serve';
import { silenceTyprOpentypeNoise } from './silence-typr';
import { mergeWatchIgnored } from './watch-ignored';

/** WASM / yoga glue breaks Vite prebundle sourcemaps in Firefox DevTools. */
const OPTIMIZE_DEPS_EXCLUDE = [
  '@pmndrs/uikit',
  'yoga-layout',
  'recast-navigation',
  '@recast-navigation/three',
] as const;

export function vibegame(): Plugin[] {
  return [
    vibegamePublicLiveServe(),
    silenceTyprOpentypeNoise(),
    {
      name: 'vibegame',
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
  ];
}

export { consoleForwarding } from './console-plugin';
export { vibegameAssetHotReload } from './hot-reload';
export { vibegamePublicLiveServe } from './public-live-serve';
export { silenceTyprOpentypeNoise } from './silence-typr';
export {
  mergeWatchIgnored,
  VIBEGAME_SERVER_WATCH_IGNORED,
} from './watch-ignored';
