import type { State } from 'vibegame';
import { setInputMovementSuppressed } from '../../../../src/plugins/input/systems';
import { setGamePaused } from '../game/pause';
import {
  SKILL_DEFS,
  getSkillLevel,
  getSkillPoints,
  spendSkillPoint,
  type SkillKey,
} from '../game/skills';
import { createInventoryPanel } from './inventory-panel';

/** A single row in the Options tab: a label plus a current value that cycles or
 * toggles when the row is clicked (language, music, …). */
export interface PauseOption {
  labelKey: string;
  value: () => string;
  activate: () => void;
}

export interface PauseMenuCtx {
  state: State;
  translate: (key: string, vars?: Record<string, string>) => string;
  locale: () => 'en' | 'pt';
  getLevel: () => number;
  onSave: () => void;
  onLoad: () => void;
  options: PauseOption[];
}

export interface PauseMenu {
  toggle: (open?: boolean) => void;
  isOpen: () => boolean;
  refresh: () => void;
}

type TabKey = 'menu' | 'skills' | 'inventory' | 'options';

function menuButton(
  onClick: () => void,
  primary = false
): HTMLButtonElement {
  const b = document.createElement('button');
  const base = primary
    ? 'background:linear-gradient(180deg,rgba(150,110,40,0.55),rgba(95,68,20,0.55));color:#ffe08a;border:1px solid rgba(255,210,120,0.5);'
    : 'background:linear-gradient(180deg,rgba(30,38,60,0.8),rgba(18,24,40,0.8));color:#dbe5f5;border:1px solid rgba(120,150,220,0.28);';
  b.style.cssText =
    base +
    'padding:11px 16px;border-radius:10px;font:700 14px system-ui,Segoe UI,sans-serif;' +
    'cursor:pointer;pointer-events:auto;width:100%;text-align:left;letter-spacing:0.3px;' +
    'box-shadow:0 4px 14px rgba(0,0,0,0.3);transition:transform 0.1s ease,filter 0.1s ease;';
  b.addEventListener('click', onClick);
  b.addEventListener('mouseenter', () => {
    b.style.transform = 'translateY(-1px)';
    b.style.filter = 'brightness(1.15)';
  });
  b.addEventListener('mouseleave', () => {
    b.style.transform = '';
    b.style.filter = '';
  });
  return b;
}

/** Tabbed pause overlay (Menu / Skills / Inventory). Owns the freeze side
 * effects (timeScale=0, input suppression, shared pause flag). Skills + bag
 * data live in their own modules; this is purely the view + wiring. */
export function createPauseMenu(ctx: PauseMenuCtx): PauseMenu {
  const { state, translate: t } = ctx;
  let open = false;
  let tab: TabKey = 'menu';

  const root = document.createElement('div');
  root.style.cssText =
    'position:fixed;inset:0;z-index:2500;display:none;' +
    'align-items:center;justify-content:center;' +
    'background:rgba(4,6,14,0.55);backdrop-filter:blur(7px);' +
    'pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;';

  const panel = document.createElement('div');
  panel.style.cssText =
    'width:min(520px,92vw);max-height:88vh;overflow:hidden;display:flex;flex-direction:column;' +
    'background:linear-gradient(160deg,rgba(20,26,44,0.96),rgba(12,16,28,0.96));' +
    'border:1px solid rgba(130,160,230,0.25);border-radius:18px;' +
    'box-shadow:0 24px 70px rgba(0,0,0,0.6),inset 0 1px 0 rgba(255,255,255,0.06);';

  // Header.
  const header = document.createElement('div');
  header.style.cssText =
    'display:flex;align-items:center;gap:12px;padding:18px 22px 14px;' +
    'border-bottom:1px solid rgba(130,160,230,0.14);';
  const titleEl = document.createElement('div');
  titleEl.style.cssText =
    'font-size:22px;font-weight:800;letter-spacing:0.5px;color:#eef3ff;flex:1;';
  const levelChip = document.createElement('div');
  levelChip.style.cssText =
    'font:800 13px system-ui;color:#2a1c06;padding:4px 12px;border-radius:20px;' +
    'background:radial-gradient(circle at 35% 30%,#ffe7a0,#ffc24a 60%,#d99320);' +
    'border:1px solid rgba(255,225,150,0.85);';
  header.append(titleEl, levelChip);

  // Tabs.
  const tabBar = document.createElement('div');
  tabBar.style.cssText = 'display:flex;gap:6px;padding:12px 18px 0;';
  const tabEls: Record<TabKey, HTMLButtonElement> = {} as never;
  const contentWrap = document.createElement('div');
  contentWrap.style.cssText = 'padding:16px 22px 6px;overflow-y:auto;';

  const menuContent = document.createElement('div');
  menuContent.style.cssText = 'display:flex;flex-direction:column;gap:9px;';
  const skillsContent = document.createElement('div');
  skillsContent.style.cssText = 'display:none;flex-direction:column;gap:12px;';
  const invContent = document.createElement('div');
  invContent.style.cssText = 'display:none;flex-direction:column;gap:12px;';
  const optionsContent = document.createElement('div');
  optionsContent.style.cssText = 'display:none;flex-direction:column;gap:9px;';

  const TAB_KEYS: TabKey[] = ['menu', 'skills', 'inventory', 'options'];

  function setTab(next: TabKey): void {
    tab = next;
    menuContent.style.display = next === 'menu' ? 'flex' : 'none';
    skillsContent.style.display = next === 'skills' ? 'flex' : 'none';
    invContent.style.display = next === 'inventory' ? 'flex' : 'none';
    optionsContent.style.display = next === 'options' ? 'flex' : 'none';
    for (const key of TAB_KEYS) {
      const active = key === next;
      const el = tabEls[key];
      el.style.background = active ? 'rgba(130,160,230,0.22)' : 'transparent';
      el.style.color = active ? '#eef3ff' : '#8a9ab8';
      el.style.borderBottom = active
        ? '2px solid #8fb0ff'
        : '2px solid transparent';
    }
    if (next === 'inventory') inv.refresh(ctx.locale());
  }
  for (const key of TAB_KEYS) {
    const el = document.createElement('button');
    el.style.cssText =
      'background:transparent;border:none;border-bottom:2px solid transparent;' +
      'color:#8a9ab8;font:700 14px system-ui;padding:8px 12px;cursor:pointer;' +
      'pointer-events:auto;border-radius:8px 8px 0 0;';
    el.addEventListener('click', () => setTab(key));
    tabEls[key] = el;
    tabBar.appendChild(el);
  }

  // Menu tab.
  const resumeBtn = menuButton(() => api.toggle(false), true);
  const saveBtn = menuButton(() => ctx.onSave());
  const loadBtn = menuButton(() => ctx.onLoad());
  const restartBtn = menuButton(() => location.reload());
  menuContent.append(resumeBtn, saveBtn, loadBtn, restartBtn);

  // Skills tab.
  const pointsEl = document.createElement('div');
  pointsEl.style.cssText = 'font:700 14px system-ui;color:#b18cff;padding:4px 0 2px;';
  skillsContent.appendChild(pointsEl);
  const skillRows: Record<
    SkillKey,
    {
      name: HTMLDivElement;
      desc: HTMLDivElement;
      val: HTMLSpanElement;
      plus: HTMLButtonElement;
    }
  > = {} as never;
  for (const def of SKILL_DEFS) {
    const row = document.createElement('div');
    row.style.cssText =
      'display:flex;align-items:center;gap:12px;padding:10px 12px;' +
      'background:rgba(255,255,255,0.03);border:1px solid rgba(130,160,230,0.14);border-radius:10px;';
    const txt = document.createElement('div');
    txt.style.cssText = 'flex:1;';
    const name = document.createElement('div');
    name.style.cssText = 'font:700 14px system-ui;color:#eaf0fb;';
    const desc = document.createElement('div');
    desc.style.cssText = 'font:500 11px system-ui;color:#8a9ab8;margin-top:2px;';
    txt.append(name, desc);
    const val = document.createElement('span');
    val.style.cssText = `min-width:26px;text-align:center;font:800 16px system-ui;color:${def.color};`;
    const plus = document.createElement('button');
    plus.textContent = '+';
    plus.style.cssText =
      'width:30px;height:30px;border-radius:8px;cursor:pointer;pointer-events:auto;' +
      'background:linear-gradient(180deg,#5a7cff,#3a52c8);color:#fff;border:none;' +
      'font:800 18px system-ui;line-height:1;box-shadow:0 3px 8px rgba(0,0,0,0.35);';
    plus.addEventListener('click', () => {
      if (spendSkillPoint(def.key)) api.refresh();
    });
    row.append(txt, val, plus);
    skillsContent.appendChild(row);
    skillRows[def.key] = { name, desc, val, plus };
  }

  // Inventory tab.
  const inv = createInventoryPanel();
  invContent.appendChild(inv.root);

  // Options tab — one clickable row per option (label + cycling value).
  const optionRows = ctx.options.map((opt) => {
    const btn = menuButton(() => {
      opt.activate();
      api.refresh();
    });
    btn.style.display = 'flex';
    btn.style.justifyContent = 'space-between';
    btn.style.alignItems = 'center';
    const label = document.createElement('span');
    const value = document.createElement('span');
    value.style.cssText = 'color:#ffd24a;font-weight:800;';
    btn.textContent = '';
    btn.append(label, value);
    optionsContent.appendChild(btn);
    return { opt, label, value };
  });

  contentWrap.append(menuContent, skillsContent, invContent, optionsContent);

  const footer = document.createElement('div');
  footer.style.cssText =
    'padding:10px 22px 16px;color:#7c8aa8;font:600 11px system-ui;text-align:center;';

  panel.append(header, tabBar, contentWrap, footer);
  root.appendChild(panel);
  document.body.appendChild(root);

  const api: PauseMenu = {
    isOpen: () => open,
    toggle(next?: boolean): void {
      const want = next ?? !open;
      if (want === open) return;
      open = want;
      setGamePaused(want);
      state.time.timeScale = want ? 0 : 1;
      setInputMovementSuppressed(want);
      root.style.display = want ? 'flex' : 'none';
      if (want) {
        setTab('menu');
        api.refresh();
      }
    },
    refresh(): void {
      const lvl = ctx.getLevel();
      const pts = getSkillPoints();
      titleEl.textContent = `⏸  ${t('pause.title')}`;
      levelChip.textContent = `${t('tip.level')} ${lvl}`;
      tabEls.menu.textContent = t('pause.tab.menu');
      tabEls.skills.textContent = t('pause.tab.skills') + (pts > 0 ? ' ●' : '');
      tabEls.inventory.textContent = t('pause.tab.inventory');
      tabEls.options.textContent = t('pause.tab.options');
      resumeBtn.textContent = `▶  ${t('pause.resume')}`;
      saveBtn.textContent = `💾  ${t('pause.save')}`;
      loadBtn.textContent = `📂  ${t('pause.load')}`;
      restartBtn.textContent = `↻  ${t('pause.restart')}`;
      for (const r of optionRows) {
        r.label.textContent = t(r.opt.labelKey);
        r.value.textContent = r.opt.value();
      }
      pointsEl.textContent = t('pause.points', { n: String(pts) });
      footer.textContent = t('pause.hint');
      for (const def of SKILL_DEFS) {
        const r = skillRows[def.key];
        r.name.textContent = t(def.nameKey);
        r.desc.textContent = t(def.descKey);
        r.val.textContent = String(getSkillLevel(def.key));
        const dim = pts <= 0;
        r.plus.style.opacity = dim ? '0.35' : '1';
        r.plus.style.pointerEvents = dim ? 'none' : 'auto';
      }
      if (tab === 'inventory') inv.refresh(ctx.locale());
    },
  };

  setTab('menu');
  return api;
}
