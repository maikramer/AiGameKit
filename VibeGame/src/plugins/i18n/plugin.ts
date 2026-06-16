import type { Adapter, Plugin } from '../../core';
import { internString } from '../hud/context';
import { I18nConfig, I18nText } from './components';
import { i18nConfigRecipe, i18nTextRecipe } from './recipes';
import { I18nAutoDefaultsSystem, I18nResolveSystem } from './systems';

/**
 * Internationalization plugin.
 *
 * **Requires {@link HudPlugin}** to be registered first. I18nResolveSystem
 * resolves `i18n-text` keys into HUD panel text via `internString` and
 * `getStringAt` from `hud/context`, and writes resolved strings to the
 * `HudPanel.textIndex` component. Without the hud plugin the i18n system
 * has nowhere to render translated text and will effectively be a no-op.
 *
 * Engine EN defaults can be auto-loaded via `<I18n auto-engine-defaults="true"/>`.
 *
 * @example
 * ```ts
 * // Register hud first, then i18n:
 * import { withPlugin } from 'vibegame';
 * withPlugin(HudPlugin).withPlugin(I18nPlugin).run();
 * ```
 *
 * @see I18nResolveSystem
 * @see I18nAutoDefaultsSystem
 * @see HudPanel
 */
export const I18nPlugin: Plugin = {
  systems: [I18nAutoDefaultsSystem, I18nResolveSystem],
  recipes: [i18nTextRecipe, i18nConfigRecipe],
  components: {
    'i18n-text': I18nText,
    'i18n-config': I18nConfig,
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
      'i18n-config': {
        autoEngineDefaults: 0,
        applied: 0,
      },
    },
    adapters: {
      'i18n-text': {
        key: ((entity: number, value: string, state) => {
          I18nText.keyIndex[entity] = internString(state, value);
        }) as Adapter,
      },
      'i18n-config': {
        'auto-engine-defaults': ((entity: number, value: string) => {
          I18nConfig.autoEngineDefaults[entity] = value === 'true' ? 1 : 0;
        }) as Adapter,
      },
    },
  },
};
