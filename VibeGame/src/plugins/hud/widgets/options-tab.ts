import { emitEvent } from '../../rpg-core';
import type { State, XMLValue } from '../../../core';
import { t } from '../../i18n/utils';
import { injectWidgetCss, readAttr } from './shared';
import type { TabContent } from './tabbed-modal-shared';

export const MODAL_OPTION_CHANGED = 'modal:option-changed';

export type OptionRowType = 'cycle' | 'slider' | 'toggle';

export interface OptionDef {
  id: string;
  labelKey: string;
  type: OptionRowType;
  values?: readonly string[];
  min?: number;
  max?: number;
  step?: number;
  default?: string | number | boolean;
}

interface OptionState {
  def: OptionDef;
  value: string;
}

const stateToOptions = new WeakMap<State, Map<string, OptionState>>();

function optionMap(state: State): Map<string, OptionState> {
  let m = stateToOptions.get(state);
  if (!m) {
    m = new Map();
    stateToOptions.set(state, m);
  }
  return m;
}

function defaultValue(def: OptionDef): string {
  if (def.default !== undefined) return String(def.default);
  if (def.type === 'slider')
    return def.min !== undefined ? String(def.min) : '0';
  if (def.type === 'toggle') return 'false';
  return def.values?.[0] ?? '';
}

export function registerOptionDef(state: State, def: OptionDef): void {
  const m = optionMap(state);
  if (!m.has(def.id)) {
    m.set(def.id, { def, value: defaultValue(def) });
  }
}

export function getOptionValue(state: State, id: string): string | undefined {
  return optionMap(state).get(id)?.value;
}

export function setOptionValue(state: State, id: string, value: string): void {
  const entry = optionMap(state).get(id);
  if (!entry) return;
  entry.value = value;
  emitEvent(state, MODAL_OPTION_CHANGED, { id, value });
}

function cycleValue(def: OptionDef, current: string): string {
  const values = def.values ?? [];
  if (values.length === 0) return current;
  const idx = values.indexOf(current);
  return values[(idx + 1) % values.length];
}

function activate(state: State, def: OptionDef): void {
  const entry = optionMap(state).get(def.id);
  if (!entry) return;
  if (def.type === 'toggle') {
    entry.value = entry.value === 'true' ? 'false' : 'true';
  } else if (def.type === 'cycle') {
    entry.value = cycleValue(def, entry.value);
  }
  emitEvent(state, MODAL_OPTION_CHANGED, { id: def.id, value: entry.value });
}

function parseOptionDef(attrs: Record<string, XMLValue>): OptionDef {
  const id = readAttr(attrs, 'id') ?? readAttr(attrs, 'option-id') ?? '';
  const type = (readAttr(attrs, 'type') ?? 'cycle') as OptionRowType;
  const def: OptionDef = {
    id,
    labelKey: readAttr(attrs, 'label-key') ?? id,
    type,
  };
  const rawValues = readAttr(attrs, 'values');
  if (rawValues) def.values = rawValues.split(',').map((s) => s.trim());
  const rawDefault = readAttr(attrs, 'default');
  if (rawDefault !== undefined) {
    def.default = def.type === 'slider' ? Number(rawDefault) : rawDefault;
  }
  if (def.type === 'slider') {
    const min = readAttr(attrs, 'min');
    const max = readAttr(attrs, 'max');
    const step = readAttr(attrs, 'step');
    if (min !== undefined) def.min = Number(min);
    if (max !== undefined) def.max = Number(max);
    if (step !== undefined) def.step = Number(step);
  }
  return def;
}

const OPTION_CSS = `
.hud-modal-option{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 14px;border-radius:10px;cursor:pointer;pointer-events:auto;background:linear-gradient(180deg,rgba(30,38,60,0.8),rgba(18,24,40,0.8));color:#dbe5f5;border:1px solid rgba(120,150,220,0.28);font:700 14px system-ui,Segoe UI,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,0.3);transition:transform 0.1s ease,filter 0.1s ease;}
.hud-modal-option:hover{transform:translateY(-1px);filter:brightness(1.15);}
.hud-modal-option-value{color:#ffd24a;font-weight:800;}
.hud-modal-option-slider{flex:1;max-width:160px;accent-color:#5a7cff;}
`;

export function createOptionsTab(
  state: State,
  defs?: readonly OptionDef[]
): TabContent {
  injectWidgetCss(OPTION_CSS);
  if (defs) for (const d of defs) registerOptionDef(state, d);

  const root = document.createElement('div');
  root.className = 'hud-modal-options-list';
  root.style.cssText = 'display:flex;flex-direction:column;gap:9px;';
  const rows = new Map<string, { label: HTMLElement; value: HTMLElement }>();

  function syncRow(def: OptionDef): void {
    const value = getOptionValue(state, def.id) ?? defaultValue(def);
    const r = rows.get(def.id);
    if (!r) return;
    if (def.type === 'toggle') {
      r.value.textContent =
        value === 'true' ? t(state, 'options.on') : t(state, 'options.off');
    } else {
      r.value.textContent = value;
    }
  }

  function buildRow(def: OptionDef): void {
    if (def.type === 'slider') {
      const wrap = document.createElement('label');
      wrap.className = 'hud-modal-option';
      const label = document.createElement('span');
      const slider = document.createElement('input');
      slider.type = 'range';
      slider.className = 'hud-modal-option-slider';
      slider.style.pointerEvents = 'auto';
      if (def.min !== undefined) slider.min = String(def.min);
      if (def.max !== undefined) slider.max = String(def.max);
      if (def.step !== undefined) slider.step = String(def.step);
      const value = document.createElement('span');
      value.className = 'hud-modal-option-value';
      slider.addEventListener('input', () => {
        setOptionValue(state, def.id, slider.value);
        value.textContent = slider.value;
      });
      wrap.append(label, slider, value);
      wrap.style.cursor = 'default';
      root.appendChild(wrap);
      rows.set(def.id, { label, value });
    } else {
      const btn = document.createElement('button');
      btn.className = 'hud-modal-option';
      btn.type = 'button';
      const label = document.createElement('span');
      const value = document.createElement('span');
      value.className = 'hud-modal-option-value';
      btn.append(label, value);
      btn.addEventListener('click', () => {
        activate(state, def);
        syncRow(def);
      });
      root.appendChild(btn);
      rows.set(def.id, { label, value });
    }
  }

  function rebuild(): void {
    const all = defs ?? Array.from(optionMap(state).values()).map((e) => e.def);
    rows.clear();
    root.textContent = '';
    for (const def of all) {
      buildRow(def);
      const r = rows.get(def.id)!;
      r.label.textContent = t(state, def.labelKey);
      syncRow(def);
    }
  }

  rebuild();

  return {
    root,
    refresh(): void {
      for (const def of defs ??
        (Array.from(optionMap(state).values()).map(
          (e) => e.def
        ) as OptionDef[])) {
        const r = rows.get(def.id);
        if (r) {
          r.label.textContent = t(state, def.labelKey);
          syncRow(def);
        }
      }
    },
  };
}

export { parseOptionDef };
