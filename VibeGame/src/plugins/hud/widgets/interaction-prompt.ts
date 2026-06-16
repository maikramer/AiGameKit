import { defineQuery } from '../../../core';
import type { ParserParams, Recipe, State, XMLValue } from '../../../core';
import { t } from '../../i18n/utils';
import { PlayerController } from '../../player';
import { Transform } from '../../transforms';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidget,
  registerHudWidgetFactory,
} from '../screen-layer';

/**
 * Screen-space HUD widget showing a "Press <key> <label>" hint for the single
 * nearest registered interactable within `range` of the player. Display-only —
 * activation is owned by the game's interactable scripts. Only the nearest
 * target is shown; all others are ignored.
 *
 *   <InteractionPrompt range="4.5" key="K" i18n-key-template="hint.harvest.{kind}"/>
 */

const WIDGET_TYPE = 'interaction-prompt';
const WIDGET_ID = 'vibe:interaction-prompt';
const ROOT_CLASS = 'hud-prompt';
const KEY_CLASS = 'hud-prompt-key';
const LABEL_CLASS = 'hud-prompt-label';

const DEFAULT_RANGE = 4.5;
const DEFAULT_KEY = 'F';

export type PromptPosition = 'bottom-center' | 'top-center';

export interface InteractionTarget {
  label?: string;
  i18nKey?: string;
  kind?: string;
  key?: string;
}

interface PromptConfig {
  range: number;
  key: string;
  i18nTemplate: string | undefined;
  position: PromptPosition;
  playerEid: number;
}

const stateToTargets = new WeakMap<State, Map<number, InteractionTarget>>();

function targetMap(state: State): Map<number, InteractionTarget> {
  let m = stateToTargets.get(state);
  if (!m) {
    m = new Map();
    stateToTargets.set(state, m);
  }
  return m;
}

export function registerInteractionTarget(
  state: State,
  eid: number,
  info: InteractionTarget
): void {
  targetMap(state).set(eid, info);
}

export function unregisterInteractionTarget(state: State, eid: number): void {
  targetMap(state).delete(eid);
}

export function getInteractionTargets(
  state: State
): ReadonlyMap<number, InteractionTarget> {
  return targetMap(state);
}

const playerQuery = defineQuery([PlayerController]);

function resolvePlayerEid(state: State, fallback: number): number {
  if (fallback > 0 && state.exists(fallback)) return fallback;
  const players = playerQuery(state.world);
  return players[0] ?? 0;
}

function attrString(
  attrs: Record<string, XMLValue>,
  ...names: string[]
): string | undefined {
  for (const name of names) {
    const raw = attrs[name];
    if (raw === undefined || raw === null) continue;
    const v = String(raw).trim();
    if (v.length > 0) return v;
  }
  return undefined;
}

function attrNumber(
  attrs: Record<string, XMLValue>,
  fallback: number,
  ...names: string[]
): number {
  const v = attrString(attrs, ...names);
  if (v === undefined) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function parseConfig(attrs: Record<string, XMLValue>): PromptConfig {
  const position =
    (attrString(attrs, 'position') as PromptPosition | undefined) ??
    'bottom-center';
  return {
    range: attrNumber(attrs, DEFAULT_RANGE, 'range', 'prompt-range'),
    key: attrString(attrs, 'key') ?? DEFAULT_KEY,
    i18nTemplate: attrString(attrs, 'i18n-key-template', 'i18n-key'),
    position,
    playerEid: attrNumber(attrs, 0, 'player-eid'),
  };
}

function resolveLabel(
  state: State,
  cfg: PromptConfig,
  target: InteractionTarget
): string {
  if (target.label) return target.label;
  if (cfg.i18nTemplate) {
    const hasKindSlot = cfg.i18nTemplate.includes('{kind}');
    if (!hasKindSlot || target.kind) {
      const key = hasKindSlot
        ? cfg.i18nTemplate.replaceAll('{kind}', target.kind!)
        : cfg.i18nTemplate;
      return t(state, key);
    }
  }
  if (target.i18nKey) return t(state, target.i18nKey);
  return '';
}

function positionCss(position: PromptPosition): string {
  return position === 'top-center'
    ? 'top:12%;bottom:auto;'
    : 'bottom:18%;top:auto;';
}

function rootCss(position: PromptPosition): string {
  return [
    'position:absolute;left:50%;',
    positionCss(position),
    'transform:translateX(-50%);',
    'display:flex;align-items:center;gap:8px;',
    'padding:8px 14px;',
    'background:rgba(8,12,28,0.72);',
    'border:1px solid rgba(90,120,200,0.35);',
    'border-radius:10px;',
    'color:#e8eef8;',
    'font:600 14px system-ui,Segoe UI,sans-serif;',
    'box-shadow:0 8px 24px rgba(0,0,0,0.35);',
    'white-space:nowrap;',
    'pointer-events:none;',
    'opacity:0;visibility:hidden;',
    'transition:opacity 0.18s ease,transform 0.18s ease;',
  ].join('');
}

const KEY_CSS =
  'display:inline-flex;align-items:center;justify-content:center;' +
  'min-width:22px;height:22px;padding:0 6px;border-radius:6px;' +
  'background:#2a3450;border:1px solid rgba(255,210,120,0.5);' +
  'color:#ffe08a;font-weight:800;font-size:13px;';

export function interactionPromptWidgetFactory(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const cfg = parseConfig(attributes);

  return {
    id: WIDGET_ID,
    mount(layer: HTMLDivElement, _mountState: State): WidgetHandle {
      const root = document.createElement('div');
      root.className = ROOT_CLASS;
      root.style.cssText = rootCss(cfg.position);
      root.dataset.visible = 'false';

      const keyEl = document.createElement('span');
      keyEl.className = KEY_CLASS;
      keyEl.style.cssText = KEY_CSS;
      keyEl.textContent = cfg.key;

      const labelEl = document.createElement('span');
      labelEl.className = LABEL_CLASS;

      root.appendChild(keyEl);
      root.appendChild(labelEl);
      layer.appendChild(root);

      const setVisible = (visible: boolean): void => {
        root.dataset.visible = visible ? 'true' : 'false';
        root.style.visibility = visible ? 'visible' : 'hidden';
        root.style.opacity = visible ? '1' : '0';
      };

      return {
        root,
        update(state: State): void {
          const playerEid = resolvePlayerEid(state, cfg.playerEid);
          if (!playerEid) {
            setVisible(false);
            return;
          }

          const px = Transform.posX[playerEid];
          const pz = Transform.posZ[playerEid];
          const rangeSq = cfg.range * cfg.range;

          let bestDist = Infinity;
          let bestTarget: InteractionTarget | null = null;

          for (const [eid, info] of targetMap(state)) {
            if (!state.exists(eid)) continue;
            const dx = Transform.posX[eid] - px;
            const dz = Transform.posZ[eid] - pz;
            const d = dx * dx + dz * dz;
            if (d <= rangeSq && d < bestDist) {
              bestDist = d;
              bestTarget = info;
            }
          }

          if (bestTarget) {
            keyEl.textContent = bestTarget.key ?? cfg.key;
            labelEl.textContent = resolveLabel(state, cfg, bestTarget);
            setVisible(true);
          } else {
            setVisible(false);
          }
        },
        unmount(): void {
          root.remove();
        },
      };
    },
  };
}

registerHudWidgetFactory(WIDGET_TYPE, interactionPromptWidgetFactory);

export const interactionPromptRecipe: Recipe = {
  name: 'InteractionPrompt',
  components: [],
  parserAttributes: [
    'range',
    'prompt-range',
    'key',
    'i18n-key-template',
    'i18n-key',
    'position',
    'player-eid',
  ],
};

export function interactionPromptParser({
  element,
  state,
}: ParserParams): void {
  const widget = interactionPromptWidgetFactory(element.attributes, state);
  registerHudWidget(state, widget);
}
