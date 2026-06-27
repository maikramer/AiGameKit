import type { State, XMLValue } from '../../../core';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidgetFactory,
} from '../../hud/screen-layer';
import { acceptQuest, endDialogue, getActiveDialogue } from '../dialogue';
import { QuestGiver, QuestState, QUEST_STATE_AVAILABLE } from '../components';

const WIDGET_TYPE = 'dialogue-balloon';
const WIDGET_ID = 'vibe:dialogue-balloon';
const ROOT_CLASS = 'hud-dialogue-balloon';

const BALLOON_CSS = `
.hud-dialogue-balloon{position:absolute;left:50%;bottom:8%;transform:translateX(-50%);width:min(560px,92vw);display:none;flex-direction:column;gap:10px;padding:16px 18px;background:linear-gradient(160deg,rgba(18,14,10,0.94),rgba(10,8,16,0.94));border:1px solid rgba(212,175,90,0.55);border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,0.55),inset 0 1px 0 rgba(255,225,150,0.08);color:#f0e6d2;font-family:Georgia,"Times New Roman",serif;pointer-events:auto;z-index:50;}
.hud-dialogue-balloon[data-open="true"]{display:flex;}
.hud-dialogue-balloon-head{display:flex;align-items:center;gap:14px;}
.hud-dialogue-balloon-portrait{width:96px;height:96px;flex:0 0 auto;border-radius:10px;object-fit:cover;border:2px solid rgba(212,175,90,0.7);background:rgba(255,255,255,0.04);}
.hud-dialogue-balloon-title{font-size:20px;font-weight:700;letter-spacing:0.4px;color:#ffe9a8;}
.hud-dialogue-balloon-lines{display:flex;flex-direction:column;gap:6px;font-size:15px;line-height:1.45;color:#e6dcc4;}
.hud-dialogue-balloon-actions{display:flex;gap:10px;flex-wrap:wrap;}
.hud-dialogue-balloon-btn{padding:9px 16px;border-radius:9px;font:700 14px Georgia,serif;cursor:pointer;pointer-events:auto;letter-spacing:0.3px;box-shadow:0 4px 14px rgba(0,0,0,0.35);transition:transform 0.1s ease,filter 0.1s ease;}
.hud-dialogue-balloon-btn:hover{transform:translateY(-1px);filter:brightness(1.15);}
.hud-dialogue-balloon-btn-accept{background:linear-gradient(180deg,rgba(150,110,40,0.6),rgba(95,68,20,0.6));color:#ffe08a;border:1px solid rgba(255,210,120,0.55);}
.hud-dialogue-balloon-btn-decline{background:linear-gradient(180deg,rgba(30,38,60,0.8),rgba(18,24,40,0.8));color:#dbe5f5;border:1px solid rgba(120,150,220,0.3);}
`;

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

/**
 * HUD widget rendering the dialogue bubble (portrait + title + lines + action
 * buttons). Visibility tracks {@link getActiveDialogue}. Mirrors the
 * interaction-prompt widget lifecycle (factory -> mount -> update -> unmount).
 */
export function dialogueBalloonFactory(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const defaultPortrait = attrString(attributes, 'portrait-url', 'portrait');

  return {
    id: WIDGET_ID,
    mount(layer: HTMLDivElement, _mountState: State): WidgetHandle {
      const style = document.createElement('style');
      style.textContent = BALLOON_CSS;
      if (typeof document !== 'undefined' && document.head) {
        document.head.appendChild(style);
      }

      const root = document.createElement('div');
      root.className = ROOT_CLASS;
      root.dataset.open = 'false';

      const head = document.createElement('div');
      head.className = 'hud-dialogue-balloon-head';
      const portrait = document.createElement('img');
      portrait.className = 'hud-dialogue-balloon-portrait';
      portrait.alt = '';
      const title = document.createElement('div');
      title.className = 'hud-dialogue-balloon-title';
      head.append(portrait, title);

      const linesEl = document.createElement('div');
      linesEl.className = 'hud-dialogue-balloon-lines';

      const actions = document.createElement('div');
      actions.className = 'hud-dialogue-balloon-actions';
      const accept = document.createElement('button');
      accept.type = 'button';
      accept.className =
        'hud-dialogue-balloon-btn hud-dialogue-balloon-btn-accept';
      accept.textContent = 'Aceitar';
      const decline = document.createElement('button');
      decline.type = 'button';
      decline.className =
        'hud-dialogue-balloon-btn hud-dialogue-balloon-btn-decline';
      decline.textContent = 'Recusar';
      const close = document.createElement('button');
      close.type = 'button';
      close.className =
        'hud-dialogue-balloon-btn hud-dialogue-balloon-btn-decline';
      close.textContent = 'Fechar';
      actions.append(accept, decline, close);

      root.append(head, linesEl, actions);
      layer.appendChild(root);

      let boundState: State | null = null;
      const closeWidget = (): void => {
        if (boundState) endDialogue(boundState);
      };
      accept.onclick = (): void => {
        if (!boundState) return;
        const active = getActiveDialogue(boundState);
        if (active) acceptQuest(boundState, active.speakerEid, active.def);
        endDialogue(boundState);
      };
      decline.onclick = closeWidget;
      close.onclick = closeWidget;

      function fillLine(text: string, progress: number, count: number): string {
        return text.replaceAll(
          '{remaining}',
          String(Math.max(0, count - progress))
        );
      }

      return {
        root,
        update(state: State): void {
          boundState = state;
          const active = getActiveDialogue(state);
          if (!active) {
            root.dataset.open = 'false';
            return;
          }
          const def = active.def;
          const giverState = QuestGiver.state[active.speakerEid];
          const count = Math.max(1, def.objective.count);
          const progress =
            giverState === QUEST_STATE_AVAILABLE
              ? 0
              : (QuestState.progress[QuestGiver.questId[active.speakerEid]] ??
                0);

          portrait.src = def.portrait ?? defaultPortrait ?? '';
          portrait.style.display = portrait.src ? '' : 'none';
          title.textContent = def.title;

          const source =
            active.phase === 'intro'
              ? def.lines_intro
              : active.phase === 'progress'
                ? def.lines_progress
                : def.lines_complete;
          linesEl.textContent = '';
          for (const text of source) {
            const p = document.createElement('p');
            p.textContent = fillLine(text, progress, count);
            linesEl.appendChild(p);
          }

          const canAccept = giverState === QUEST_STATE_AVAILABLE;
          accept.style.display = canAccept ? '' : 'none';
          decline.style.display = canAccept ? '' : 'none';
          close.style.display = canAccept ? 'none' : '';

          root.dataset.open = 'true';
        },
        unmount(): void {
          boundState = null;
          style.remove();
          root.remove();
        },
      };
    },
  };
}

registerHudWidgetFactory(WIDGET_TYPE, dialogueBalloonFactory);
