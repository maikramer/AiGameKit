import { getDataRegistry } from '../../rpg-core';
import type { SkillDef } from '../../rpg-core/types';
import type { State } from '../../../core';
import {
  ProgressionComponent,
  getSkillRank,
  spendSkillPoint,
} from '../../rpg-progression';
import { t } from '../../i18n/utils';
import { injectWidgetCss } from './shared';
import type { TabContent } from './tabbed-modal-shared';

export interface SkillsTabConfig {
  targetEntity: number;
  skillIds?: readonly string[];
}

const SKILLS_CSS = `
.hud-modal-skill-row{display:flex;align-items:center;gap:12px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(130,160,230,0.14);border-radius:10px;}
.hud-modal-skill-name{font:700 14px system-ui,Segoe UI,sans-serif;color:#eaf0fb;}
.hud-modal-skill-desc{font:500 11px system-ui,Segoe UI,sans-serif;color:#8a9ab8;margin-top:2px;}
.hud-modal-skill-rank{min-width:26px;text-align:center;font:800 16px system-ui,Segoe UI,sans-serif;color:#b18cff;}
.hud-modal-skill-plus{width:30px;height:30px;border-radius:8px;cursor:pointer;pointer-events:auto;background:linear-gradient(180deg,#5a7cff,#3a52c8);color:#fff;border:none;font:800 18px system-ui,Segoe UI,sans-serif;line-height:1;box-shadow:0 3px 8px rgba(0,0,0,0.35);}
.hud-modal-skill-plus:disabled{opacity:0.35;pointer-events:none;}
.hud-modal-skill-points{font:700 14px system-ui,Segoe UI,sans-serif;color:#b18cff;padding:4px 0 2px;}
`;

export function createSkillsTab(
  state: State,
  cfg: SkillsTabConfig
): TabContent {
  injectWidgetCss(SKILLS_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-skills-list';
  root.style.cssText = 'display:flex;flex-direction:column;gap:12px;';

  const pointsEl = document.createElement('div');
  pointsEl.className = 'hud-modal-skill-points';
  root.appendChild(pointsEl);

  const registry = getDataRegistry(state);
  const allDefs = registry.all<SkillDef>('skill');
  const defs =
    cfg.skillIds && cfg.skillIds.length > 0
      ? cfg.skillIds
          .map((id) => registry.get<SkillDef>('skill', id))
          .filter((d): d is SkillDef => !!d)
      : allDefs;

  const rows = new Map<
    string,
    {
      name: HTMLElement;
      desc: HTMLElement;
      rank: HTMLElement;
      plus: HTMLButtonElement;
    }
  >();

  for (const def of defs) {
    const row = document.createElement('div');
    row.className = 'hud-modal-skill-row';
    const txt = document.createElement('div');
    txt.style.cssText = 'flex:1;';
    const name = document.createElement('div');
    name.className = 'hud-modal-skill-name';
    const desc = document.createElement('div');
    desc.className = 'hud-modal-skill-desc';
    txt.append(name, desc);
    const rank = document.createElement('span');
    rank.className = 'hud-modal-skill-rank';
    const plus = document.createElement('button');
    plus.type = 'button';
    plus.className = 'hud-modal-skill-plus';
    plus.textContent = '+';
    plus.addEventListener('click', () => {
      if (spendSkillPoint(state, cfg.targetEntity, def.id)) refresh();
    });
    row.append(txt, rank, plus);
    root.appendChild(row);
    rows.set(def.id, { name, desc, rank, plus });
  }

  function refresh(): void {
    const pts = ProgressionComponent.unspentPoints[cfg.targetEntity] ?? 0;
    pointsEl.textContent = t(state, 'modal.skillPoints', { n: String(pts) });
    for (const def of defs) {
      const r = rows.get(def.id);
      if (!r) continue;
      r.name.textContent = def.name || t(state, `skill.${def.id}.name`);
      r.desc.textContent = def.description || '';
      const rank = getSkillRank(state, cfg.targetEntity, def.id);
      r.rank.textContent = String(rank);
      r.plus.disabled = pts <= 0 || rank >= def.maxRank;
    }
  }

  refresh();

  return { root, refresh };
}
