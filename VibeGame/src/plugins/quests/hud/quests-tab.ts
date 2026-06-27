import type { State } from '../../../core';
import { injectWidgetCss } from '../../hud/widgets/shared';
import type { TabContent } from '../../hud/widgets/tabbed-modal-shared';
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

  const activeSection = buildSection('Ativas', []);
  const completedSection = buildSection('Completas', []);
  const failedSection = buildSection('Falhadas', []);
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
        activeRows.push(row);
      } else if (QuestState.completed[idx] === 2) {
        failedRows.push(row);
      }
    }

    activeSection.replaceChildren(
      ...buildSection('Ativas', activeRows).children
    );
    completedSection.replaceChildren(
      ...buildSection('Completas', completedRows).children
    );
    failedSection.replaceChildren(
      ...buildSection('Falhadas', failedRows).children
    );
  }

  refresh(state);

  return { root, refresh };
}
