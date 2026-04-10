import type { Adapter, Plugin } from '../../core';
import { internString } from '../hud/context';
import { I18nText } from './components';
import { i18nTextRecipe } from './recipes';
import { I18nResolveSystem } from './systems';

/**
 * Internationalization plugin.
 *
 * **Requires {@link HudPlugin}** to be registered first. I18nResolveSystem
 * resolves `i18n-text` keys into HUD panel text via `internString` and
 * `getStringAt` from `hud/context`, and writes resolved strings to the
 * `HudPanel.textIndex` component. Without the hud plugin the i18n system
 * has nowhere to render translated text and will effectively be a no-op.
 *
 * @example
 * ```ts
 * // Register hud first, then i18n:
 * import { withPlugin } from 'vibegame';
 * withPlugin(HudPlugin).withPlugin(I18nPlugin).run();
 * ```
 *
 * @see I18nResolveSystem
 * @see HudPanel
 */
export const I18nPlugin: Plugin = {
  systems: [I18nResolveSystem],
  recipes: [i18nTextRecipe],
  components: {
    'i18n-text': I18nText,
  },
  initialize(state) {
    if (!state.getComponent('hud-panel')) {
      console.warn(
        '[i18n] I18nPlugin requires HudPlugin to function. Register HudPlugin first.'
      );
    }
  },
  config: {
    defaults: {
      'i18n-text': {
        keyIndex: 0,
        resolved: 0,
      },
    },
    adapters: {
      'i18n-text': {
        key: ((entity: number, value: string, state) => {
          I18nText.keyIndex[entity] = internString(state, value);
        }) as Adapter,
      },
    },
  },
};
