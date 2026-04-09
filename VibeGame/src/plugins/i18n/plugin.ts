import type { Adapter, Plugin } from '../../core';
import { internString } from '../hud/context';
import { I18nText } from './components';
import { i18nTextRecipe } from './recipes';
import { I18nResolveSystem } from './systems';

/**
 * Internationalization plugin.
 *
 * Depends on the **hud** plugin being loaded first (uses `internString` from
 * hud/context to translate string keys into integer indices).
 */
export const I18nPlugin: Plugin = {
    systems: [I18nResolveSystem],
    recipes: [i18nTextRecipe],
    components: {
        'i18n-text': I18nText,
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
