import type { Plugin } from 'vite';
import { mergeWatchIgnored } from './watch-ignored';

export function vibegame(): Plugin[] {
  return [
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
      },
    },
  ];
}

export { consoleForwarding } from './console-plugin';
export { vibegameAssetHotReload } from './hot-reload';
export {
  mergeWatchIgnored,
  VIBEGAME_SERVER_WATCH_IGNORED,
} from './watch-ignored';
