import { getDataRegistry } from '../../rpg-core';
import type { SkillDef } from '../../rpg-core/types';
import type { State } from '../../../core';
import {
  ProgressionComponent,
  getSkillRank,
  skillPrereqsMet,
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
.hud-modal-skills{display:flex;flex-direction:column;gap:14px;}
.hud-modal-skill-points{font:700 13px "Segoe UI",system-ui,sans-serif;color:#c9b07a;letter-spacing:0.4px;padding:2px 0 4px;}
.hud-modal-skill-tree{display:flex;flex-direction:column;gap:0;position:relative;}
.hud-modal-skill-tier{display:flex;justify-content:center;flex-wrap:wrap;gap:14px;padding:10px 4px;position:relative;}
.hud-modal-skill-tier + .hud-modal-skill-tier::before{content:"";position:absolute;top:0;left:12%;right:12%;height:1px;background:linear-gradient(90deg,transparent,rgba(201,176,122,0.35),transparent);}
.hud-modal-skill-node{width:132px;min-height:108px;display:flex;flex-direction:column;align-items:center;gap:6px;padding:12px 10px 10px;border-radius:14px;background:linear-gradient(165deg,rgba(36,44,68,0.92),rgba(18,24,40,0.95));border:1px solid rgba(160,180,220,0.22);box-shadow:0 8px 22px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.06);text-align:center;position:relative;}
.hud-modal-skill-node[data-locked="true"]{opacity:0.48;filter:grayscale(0.35);}
.hud-modal-skill-node[data-maxed="true"]{border-color:rgba(201,176,122,0.55);box-shadow:0 8px 22px rgba(0,0,0,0.35),inset 0 0 0 1px rgba(201,176,122,0.18);}
.hud-modal-skill-icon{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:18px;background:radial-gradient(circle at 35% 30%,rgba(255,230,170,0.25),rgba(40,50,80,0.9));border:1px solid rgba(201,176,122,0.4);color:#f0e2b8;}
.hud-modal-skill-name{font:700 12px "Segoe UI",system-ui,sans-serif;color:#eef2fb;line-height:1.2;}
.hud-modal-skill-desc{font:500 10px "Segoe UI",system-ui,sans-serif;color:#8e9cb8;line-height:1.35;min-height:2.6em;}
.hud-modal-skill-meta{display:flex;align-items:center;gap:8px;margin-top:auto;}
.hud-modal-skill-rank{font:800 13px "Segoe UI",system-ui,sans-serif;color:#c9b07a;min-width:28px;}
.hud-modal-skill-plus{width:28px;height:28px;border-radius:8px;cursor:pointer;pointer-events:auto;background:linear-gradient(180deg,#6a82d8,#3d4fa8);color:#fff;border:1px solid rgba(180,200,255,0.35);font:800 16px "Segoe UI",system-ui,sans-serif;line-height:1;box-shadow:0 3px 8px rgba(0,0,0,0.35);}
.hud-modal-skill-plus:disabled{opacity:0.32;pointer-events:none;}
.hud-modal-skill-req{font:600 9px "Segoe UI",system-ui,sans-serif;color:#a88a6a;letter-spacing:0.2px;}
`;

const TIER_ICONS = ['◆', '◇', '✦', '✧', '❖'];

function resolveDefs(state: State, cfg: SkillsTabConfig): SkillDef[] {
  const registry = getDataRegistry(state);
  const allDefs = registry.all<SkillDef>('skill');
  if (cfg.skillIds && cfg.skillIds.length > 0) {
    return cfg.skillIds
      .map((id) => registry.get<SkillDef>('skill', id))
      .filter((d): d is SkillDef => !!d);
  }
  return [...allDefs];
}

function sortDefs(defs: SkillDef[]): SkillDef[] {
  return [...defs].sort((a, b) => {
    const ta = a.tier ?? 0;
    const tb = b.tier ?? 0;
    if (ta !== tb) return ta - tb;
    const ca = a.column ?? 0;
    const cb = b.column ?? 0;
    if (ca !== cb) return ca - cb;
    return a.id.localeCompare(b.id);
  });
}

function groupByTier(defs: SkillDef[]): Map<number, SkillDef[]> {
  const map = new Map<number, SkillDef[]>();
  for (const def of defs) {
    const tier = def.tier ?? 0;
    let list = map.get(tier);
    if (!list) {
      list = [];
      map.set(tier, list);
    }
    list.push(def);
  }
  return map;
}

export function createSkillsTab(
  state: State,
  cfg: SkillsTabConfig
): TabContent {
  injectWidgetCss(SKILLS_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-skills';

  const pointsEl = document.createElement('div');
  pointsEl.className = 'hud-modal-skill-points';
  root.appendChild(pointsEl);

  const tree = document.createElement('div');
  tree.className = 'hud-modal-skill-tree';
  root.appendChild(tree);

  const defs = sortDefs(resolveDefs(state, cfg));
  const byTier = groupByTier(defs);

  const rows = new Map<
    string,
    {
      node: HTMLElement;
      name: HTMLElement;
      desc: HTMLElement;
      rank: HTMLElement;
      plus: HTMLButtonElement;
      req: HTMLElement;
    }
  >();

  const tiers = [...byTier.keys()].sort((a, b) => a - b);
  for (const tier of tiers) {
    const tierEl = document.createElement('div');
    tierEl.className = 'hud-modal-skill-tier';
    tierEl.dataset.tier = String(tier);
    for (const def of byTier.get(tier)!) {
      const node = document.createElement('div');
      node.className = 'hud-modal-skill-node';
      node.dataset.skillId = def.id;

      const icon = document.createElement('div');
      icon.className = 'hud-modal-skill-icon';
      icon.textContent = def.icon ?? TIER_ICONS[tier % TIER_ICONS.length]!;

      const name = document.createElement('div');
      name.className = 'hud-modal-skill-name';

      const desc = document.createElement('div');
      desc.className = 'hud-modal-skill-desc';

      const req = document.createElement('div');
      req.className = 'hud-modal-skill-req';

      const meta = document.createElement('div');
      meta.className = 'hud-modal-skill-meta';
      const rank = document.createElement('span');
      rank.className = 'hud-modal-skill-rank';
      const plus = document.createElement('button');
      plus.type = 'button';
      plus.className = 'hud-modal-skill-plus';
      plus.textContent = '+';
      plus.title = '+1';
      plus.addEventListener('click', () => {
        if (spendSkillPoint(state, cfg.targetEntity, def.id)) refresh();
      });
      meta.append(rank, plus);

      node.append(icon, name, desc, req, meta);
      tierEl.appendChild(node);
      rows.set(def.id, { node, name, desc, rank, plus, req });
    }
    tree.appendChild(tierEl);
  }

  function refresh(): void {
    const pts = ProgressionComponent.unspentPoints[cfg.targetEntity] ?? 0;
    pointsEl.textContent = t(state, 'modal.skillPoints', { n: String(pts) });
    const nameById = new Map(defs.map((d) => [d.id, d.name || d.id]));

    for (const def of defs) {
      const r = rows.get(def.id);
      if (!r) continue;
      r.name.textContent = def.name || t(state, `skill.${def.id}.name`);
      r.desc.textContent = def.description || '';
      const rank = getSkillRank(state, cfg.targetEntity, def.id);
      r.rank.textContent = `${rank}/${def.maxRank}`;
      const prereqsOk = skillPrereqsMet(state, cfg.targetEntity, def);
      const locked = !prereqsOk && rank === 0;
      r.node.dataset.locked = locked ? 'true' : 'false';
      r.node.dataset.maxed = rank >= def.maxRank ? 'true' : 'false';
      if (def.requires && def.requires.length > 0 && locked) {
        const labels = def.requires.map((id) => nameById.get(id) ?? id);
        r.req.textContent = t(state, 'modal.skillRequires', {
          names: labels.join(', '),
        });
      } else {
        r.req.textContent = '';
      }
      r.plus.disabled = pts <= 0 || rank >= def.maxRank || !prereqsOk;
    }
  }

  refresh();

  return { root, refresh };
}
