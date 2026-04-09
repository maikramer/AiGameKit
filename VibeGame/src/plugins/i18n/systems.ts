import { defineQuery, type System } from '../../core';
import { HudPanel } from '../hud/components';
import { getStringAt, internString } from '../hud/context';
import { I18nText } from './components';
import { t } from './utils';

const i18nHudQuery = defineQuery([I18nText, HudPanel]);

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
