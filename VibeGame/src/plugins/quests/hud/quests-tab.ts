import type { State } from '../../../core';
import { t } from '../../i18n/utils';
import { injectWidgetCss } from '../../hud/widgets/shared';
import type { TabContent } from '../../hud/widgets/tabbed-modal-shared';
import { getTrackedQuest, setTrackedQuest } from '../beacon';
import { QuestState } from '../components';
import { getAllQuestDefs, getQuestIndex } from '../registry';

export interface QuestsTabConfig {
  targetEntity?: number;
}

const QUESTS_CSS = `
.hud-modal-quests{display:flex;flex-direction:column;gap:14px;}
.hud-modal-quests-section{display:flex;flex-direction:column;gap:6px;}
.hud-modal-quests-heading{font:800 13px system-ui,Segoe UI,sans-serif;letter-spacing:0.6px;text-transform:uppercase;color:#9fb2d6;}
.hud-modal-quests-row{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;background:rgba(255,255,255,0.03);border:1px solid rgba(130,160,230,0.12);color:#e6ecf8;font:600 13px system-ui,Segoe UI,sans-serif;}
.hud-modal-quests-progress{margin-left:auto;font:700 12px system-ui,Segoe UI,sans-serif;color:#ffe08a;}
.hud-modal-quests-empty{color:#7c8aa8;font:600 13px system-ui,Segoe UI,sans-serif;}
.hud-modal-quests-check{color:#7fe0a0;font-weight:800;}
.hud-modal-quests-track{margin-left:8px;padding:3px 10px;border-radius:7px;cursor:pointer;
pointer-events:auto;font:700 11px system-ui,Segoe UI,sans-serif;letter-spacing:0.3px;
background:rgba(30,38,60,0.85);border:1px solid rgba(120,150,220,0.35);color:#cddcf6;}
.hud-modal-quests-track:hover{filter:brightness(1.2);}
.hud-modal-quests-track[data-tracked="true"]{background:rgba(150,110,40,0.55);
border-color:rgba(255,210,120,0.6);color:#ffe08a;}
`;

function buildSection(heading: string, rows: HTMLElement[]): HTMLElement {
  const section = document.createElement('div');
  section.className = 'hud-modal-quests-section';
  const h = document.createElement('div');
  h.className = 'hud-modal-quests-heading';
  h.textContent = heading;
  section.appendChild(h);
  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'hud-modal-quests-empty';
    empty.textContent = '—';
    section.appendChild(empty);
  } else {
    for (const r of rows) section.appendChild(r);
  }
  return section;
}

export function createQuestsTab(
  state: State,
  _cfg: QuestsTabConfig
): TabContent {
  injectWidgetCss(QUESTS_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-quests';

  const activeSection = buildSection(t(state, 'quests.active'), []);
  const completedSection = buildSection(t(state, 'quests.completed'), []);
  const failedSection = buildSection(t(state, 'quests.failed'), []);
  root.append(activeSection, completedSection, failedSection);

  function refresh(s: State): void {
    const defs = getAllQuestDefs(s);
    const activeRows: HTMLElement[] = [];
    const completedRows: HTMLElement[] = [];
    const failedRows: HTMLElement[] = [];

    for (const def of defs) {
      const idx = getQuestIndex(s, def.id);
      if (idx < 0) continue;
      const row = document.createElement('div');
      row.className = 'hud-modal-quests-row';
      const label = document.createElement('span');
      label.textContent = def.title;
      row.appendChild(label);

      if (QuestState.completed[idx] === 1) {
        const mark = document.createElement('span');
        mark.className = 'hud-modal-quests-check';
        mark.textContent = '✓';
        row.appendChild(mark);
        completedRows.push(row);
      } else if (QuestState.active[idx] === 1) {
        const prog = document.createElement('span');
        prog.className = 'hud-modal-quests-progress';
        const goal = Math.max(1, def.objective.count);
        prog.textContent = `${Math.min(goal, QuestState.progress[idx])}/${goal}`;
        row.appendChild(prog);

        // Pin the HUD arrow/tracker at this quest. With several quests running
        // the automatic "nearest urgent marker" pick is often not the one the
        // player means to do next, so let them say so.
        const track = document.createElement('button');
        track.type = 'button';
        track.className = 'hud-modal-quests-track';
        const isTracked = getTrackedQuest(s) === def.id;
        track.dataset.tracked = String(isTracked);
        track.textContent = t(
          s,
          isTracked ? 'quests.tracking' : 'quests.track'
        );
        track.onclick = (): void => {
          setTrackedQuest(s, isTracked ? null : def.id);
          refresh(s);
        };
        row.appendChild(track);
        activeRows.push(row);
      } else if (QuestState.completed[idx] === 2) {
        failedRows.push(row);
      }
    }

    activeSection.replaceChildren(
      ...buildSection(t(s, 'quests.active'), activeRows).children
    );
    completedSection.replaceChildren(
      ...buildSection(t(s, 'quests.completed'), completedRows).children
    );
    failedSection.replaceChildren(
      ...buildSection(t(s, 'quests.failed'), failedRows).children
    );
  }

  refresh(state);

  return { root, refresh };
}
