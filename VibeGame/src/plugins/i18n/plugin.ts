import type { Plugin } from '../../core';
import { I18nText } from './components';
import { I18nResolveSystem } from './systems';

export const I18nPlugin: Plugin = {
  systems: [I18nResolveSystem],
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
  },
};
