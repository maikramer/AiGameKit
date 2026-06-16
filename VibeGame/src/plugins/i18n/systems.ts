import { defineQuery, type System } from '../../core';
import { HudPanel } from '../hud/components';
import { getStringAt, internString } from '../hud/context';
import { I18nConfig, I18nText } from './components';
import { loadEngineDefaultDictionary } from './engine-defaults';
import { t } from './utils';

const i18nHudQuery = defineQuery([I18nText, HudPanel]);
const i18nConfigQuery = defineQuery([I18nConfig]);

export const I18nResolveSystem: System = {
  group: 'simulation',
  update: (state) => {
    for (const eid of i18nHudQuery(state.world)) {
      if (I18nText.resolved[eid] === 1) continue;
      const key = getStringAt(state, I18nText.keyIndex[eid]);
      const text = t(state, key);
      HudPanel.textIndex[eid] = internString(state, text);
      I18nText.resolved[eid] = 1;
    }
  },
};

export const I18nAutoDefaultsSystem: System = {
  group: 'setup',
  update: (state) => {
    for (const eid of i18nConfigQuery(state.world)) {
      if (I18nConfig.applied[eid] === 1) continue;
      if (I18nConfig.autoEngineDefaults[eid] === 1) {
        loadEngineDefaultDictionary(state);
      }
      I18nConfig.applied[eid] = 1;
    }
  },
};
