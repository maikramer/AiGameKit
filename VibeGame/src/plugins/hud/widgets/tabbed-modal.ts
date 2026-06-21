import { emitEvent } from '../../rpg-core';
import type { State, XMLValue, ParserParams, Recipe } from '../../../core';
import { pushModal, popModal } from '../../rpg-pause';
import { t } from '../../i18n/utils';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidget,
  registerHudWidgetFactory,
} from '../screen-layer';
import { injectWidgetCss, readAttr, resolveTargetEntity } from './shared';
import type { TabContent, TabDescriptor } from './tabbed-modal-shared';
import { createSkillsTab } from './skills-tab';
import type { SkillsTabConfig } from './skills-tab';
import { createInventoryTab } from './inventory-tab';
import type { InventoryTabConfig } from './inventory-tab';
import {
  createOptionsTab,
  parseOptionDef,
  registerOptionDef,
} from './options-tab';

export const WIDGET_TYPE = 'tabbed-modal';
export const TABBED_MODAL_TAG = 'TabbedModal';
export const MODAL_ACTION = 'modal:action';

export {
  createSkillsTab,
  createInventoryTab,
  createOptionsTab,
  registerOptionDef,
};

export type { SkillsTabConfig, InventoryTabConfig };

const MODAL_CSS = `
.hud-modal-overlay{position:fixed;inset:0;z-index:100;display:none;align-items:center;justify-content:center;background:rgba(4,6,14,0.55);backdrop-filter:blur(7px);pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;}
.hud-modal-overlay[data-open="true"]{display:flex;}
.hud-modal-panel{width:min(520px,92vw);max-height:88vh;overflow:hidden;display:flex;flex-direction:column;background:linear-gradient(160deg,rgba(20,26,44,0.96),rgba(12,16,28,0.96));border:1px solid rgba(130,160,230,0.25);border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,0.6),inset 0 1px 0 rgba(255,255,255,0.06);}
.hud-modal-header{display:flex;align-items:center;gap:12px;padding:18px 22px 14px;border-bottom:1px solid rgba(130,160,230,0.14);}
.hud-modal-title{font-size:22px;font-weight:800;letter-spacing:0.5px;color:#eef3ff;flex:1;}
.hud-modal-level{font:800 13px system-ui,Segoe UI,sans-serif;color:#2a1c06;padding:4px 12px;border-radius:20px;background:radial-gradient(circle at 35% 30%,#ffe7a0,#ffc24a 60%,#d99320);border:1px solid rgba(255,225,150,0.85);}
.hud-modal-tabs{display:flex;gap:6px;padding:12px 18px 0;}
.hud-modal-tab{background:transparent;border:none;border-bottom:2px solid transparent;color:#8a9ab8;font:700 14px system-ui,Segoe UI,sans-serif;padding:8px 12px;cursor:pointer;pointer-events:auto;border-radius:8px 8px 0 0;}
.hud-modal-tab[data-active="true"]{color:#eef3ff;border-bottom-color:#8fb0ff;background:rgba(130,160,230,0.22);}
.hud-modal-content{padding:16px 22px 6px;overflow-y:auto;}
.hud-modal-pane{display:none;flex-direction:column;gap:9px;}
.hud-modal-pane[data-active="true"]{display:flex;}
.hud-modal-footer{padding:10px 22px 16px;color:#7c8aa8;font:600 11px system-ui,Segoe UI,sans-serif;text-align:center;}
.hud-modal-btn{padding:11px 16px;border-radius:10px;font:700 14px system-ui,Segoe UI,sans-serif;cursor:pointer;pointer-events:auto;width:100%;text-align:left;letter-spacing:0.3px;box-shadow:0 4px 14px rgba(0,0,0,0.3);transition:transform 0.1s ease,filter 0.1s ease;}
.hud-modal-btn:hover{transform:translateY(-1px);filter:brightness(1.15);}
.hud-modal-btn-primary{background:linear-gradient(180deg,rgba(150,110,40,0.55),rgba(95,68,20,0.55));color:#ffe08a;border:1px solid rgba(255,210,120,0.5);}
.hud-modal-btn-secondary{background:linear-gradient(180deg,rgba(30,38,60,0.8),rgba(18,24,40,0.8));color:#dbe5f5;border:1px solid rgba(120,150,220,0.28);}
`;

const DEFAULT_KEY = 'Escape';

const stateToTabs = new WeakMap<State, Map<string, TabDescriptor[]>>();
const stateToControllers = new WeakMap<State, Map<string, ModalController>>();

interface ModalController {
  open(): void;
  close(): void;
  toggle(): void;
  isOpen(): boolean;
}

function tabList(state: State, modalId: string): TabDescriptor[] {
  let m = stateToTabs.get(state);
  if (!m) {
    m = new Map();
    stateToTabs.set(state, m);
  }
  let list = m.get(modalId);
  if (!list) {
    list = [];
    m.set(modalId, list);
  }
  return list;
}

export function registerModalTab(
  state: State,
  modalId: string,
  tab: TabDescriptor
): void {
  const list = tabList(state, modalId);
  if (!list.some((t) => t.id === tab.id)) list.push(tab);
}

function controllerMap(state: State): Map<string, ModalController> {
  let m = stateToControllers.get(state);
  if (!m) {
    m = new Map();
    stateToControllers.set(state, m);
  }
  return m;
}

export function openModal(state: State, id: string): void {
  controllerMap(state).get(id)?.open();
}

export function closeModal(state: State, id: string): void {
  controllerMap(state).get(id)?.close();
}

export function toggleModal(state: State, id: string): void {
  controllerMap(state).get(id)?.toggle();
}

export function isModalOpen(state: State, id: string): boolean {
  return controllerMap(state).get(id)?.isOpen() ?? false;
}

export interface TabbedModalConfig {
  tabs?: readonly TabDescriptor[];
  titleKey?: string;
  level?: () => number;
  onSave?: () => void;
  onLoad?: () => void;
  onRestart?: () => void;
}

function menuTab(
  _state: State,
  modalId: string,
  cfg: TabbedModalConfig
): TabDescriptor {
  return {
    id: 'menu',
    labelKey: 'modal.tab.menu',
    build(s: State): TabContent {
      const root = document.createElement('div');
      root.className = 'hud-modal-menu';
      const resume = document.createElement('button');
      resume.type = 'button';
      resume.className = 'hud-modal-btn hud-modal-btn-primary';
      resume.textContent = `▶  ${t(s, 'modal.resume')}`;
      resume.addEventListener('click', () => closeModal(s, modalId));
      const save = document.createElement('button');
      save.type = 'button';
      save.className = 'hud-modal-btn hud-modal-btn-secondary';
      save.textContent = `💾  ${t(s, 'modal.save')}`;
      save.addEventListener('click', () => {
        if (cfg.onSave) cfg.onSave();
        else emitEvent(s, MODAL_ACTION, { modal: modalId, action: 'save' });
      });
      const load = document.createElement('button');
      load.type = 'button';
      load.className = 'hud-modal-btn hud-modal-btn-secondary';
      load.textContent = `📂  ${t(s, 'modal.load')}`;
      load.addEventListener('click', () => {
        if (cfg.onLoad) cfg.onLoad();
        else emitEvent(s, MODAL_ACTION, { modal: modalId, action: 'load' });
      });
      const restart = document.createElement('button');
      restart.type = 'button';
      restart.className = 'hud-modal-btn hud-modal-btn-secondary';
      restart.textContent = `↻  ${t(s, 'modal.restart')}`;
      restart.addEventListener('click', () => {
        if (cfg.onRestart) cfg.onRestart();
        else if (typeof location !== 'undefined') location.reload();
      });
      root.append(resume, save, load, restart);
      return { root, refresh() {} };
    },
  };
}

export function createTabbedModalWidget(
  attributes: Record<string, XMLValue>,
  state: State,
  config?: TabbedModalConfig
): HudWidget {
  const modalId = readAttr(attributes, 'id') ?? 'pause';
  const pauseOnOpen = readAttr(attributes, 'pause-on-open') !== 'false';
  const toggleKey = readAttr(attributes, 'key') ?? DEFAULT_KEY;
  const titleKey =
    config?.titleKey ?? readAttr(attributes, 'title-key') ?? 'modal.pause';

  if (config?.tabs) {
    for (const tab of config.tabs) registerModalTab(state, modalId, tab);
  }

  const widgetId = `vibe:tabbed-modal:${modalId}`;

  return {
    id: widgetId,
    mount(layer: HTMLDivElement, mountState: State): WidgetHandle {
      injectWidgetCss(MODAL_CSS);

      const overlay = document.createElement('div');
      overlay.className = 'hud-modal-overlay';
      overlay.dataset.open = 'false';
      overlay.dataset.modalId = modalId;

      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeModal(mountState, modalId);
      });

      const panel = document.createElement('div');
      panel.className = 'hud-modal-panel';

      const header = document.createElement('div');
      header.className = 'hud-modal-header';
      const titleEl = document.createElement('div');
      titleEl.className = 'hud-modal-title';
      const levelEl = document.createElement('div');
      levelEl.className = 'hud-modal-level';
      header.append(titleEl, levelEl);

      const tabBar = document.createElement('div');
      tabBar.className = 'hud-modal-tabs';

      const contentWrap = document.createElement('div');
      contentWrap.className = 'hud-modal-content';

      const footer = document.createElement('div');
      footer.className = 'hud-modal-footer';
      footer.textContent = t(mountState, 'modal.hint');

      panel.append(header, tabBar, contentWrap, footer);
      overlay.appendChild(panel);
      layer.appendChild(overlay);

      const tabs = tabList(mountState, modalId);
      const allTabs: TabDescriptor[] =
        tabs.length > 0 ? tabs : [menuTab(mountState, modalId, config ?? {})];

      const panes = new Map<
        string,
        {
          tab: TabDescriptor;
          btn: HTMLButtonElement;
          pane: HTMLElement;
          content: TabContent;
        }
      >();
      let activeTabId = allTabs[0]?.id ?? '';

      for (const tab of allTabs) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'hud-modal-tab';
        btn.dataset.tabId = tab.id;
        btn.addEventListener('click', () => selectTab(tab.id));
        tabBar.appendChild(btn);

        const pane = document.createElement('div');
        pane.className = 'hud-modal-pane';
        pane.dataset.tabId = tab.id;
        const content = tab.build(mountState);
        pane.appendChild(content.root);
        contentWrap.appendChild(pane);

        panes.set(tab.id, { tab, btn, pane, content });
      }

      function selectTab(id: string): void {
        activeTabId = id;
        for (const [tid, p] of panes) {
          const active = tid === id;
          p.btn.dataset.active = active ? 'true' : 'false';
          p.pane.dataset.active = active ? 'true' : 'false';
        }
        panes.get(id)?.content.refresh(mountState);
      }

      function refreshLabels(): void {
        titleEl.textContent = `⏸  ${t(mountState, titleKey)}`;
        const lvl = config?.level?.() ?? 0;
        levelEl.textContent =
          lvl > 0 ? `${t(mountState, 'modal.level')} ${lvl}` : '';
        for (const [tid, p] of panes) {
          p.btn.textContent = t(mountState, p.tab.labelKey);
          if (tid === activeTabId) p.content.refresh(mountState);
        }
        footer.textContent = t(mountState, 'modal.hint');
      }

      let open = false;

      function applyOpen(next: boolean): void {
        if (next === open) return;
        open = next;
        overlay.dataset.open = open ? 'true' : 'false';
        if (open) {
          if (pauseOnOpen) pushModal(mountState, modalId);
          selectTab(allTabs[0]?.id ?? '');
          refreshLabels();
        } else if (pauseOnOpen) {
          popModal(mountState, modalId);
        }
      }

      const controller: ModalController = {
        open: () => applyOpen(true),
        close: () => applyOpen(false),
        toggle: () => applyOpen(!open),
        isOpen: () => open,
      };
      controllerMap(mountState).set(modalId, controller);

      function onKeydown(e: KeyboardEvent): void {
        if (e.key.toLowerCase() === toggleKey.toLowerCase()) {
          e.preventDefault();
          controller.toggle();
        }
      }
      if (typeof document !== 'undefined') {
        document.addEventListener('keydown', onKeydown);
      }

      selectTab(activeTabId);

      return {
        root: overlay,
        update(_s: State): void {
          if (open) refreshLabels();
        },
        unmount(): void {
          if (open && pauseOnOpen) popModal(mountState, modalId);
          if (typeof document !== 'undefined') {
            document.removeEventListener('keydown', onKeydown);
          }
          controllerMap(mountState).delete(modalId);
          overlay.remove();
        },
      };
    },
  };
}

registerHudWidgetFactory(WIDGET_TYPE, (attrs, state) =>
  createTabbedModalWidget(attrs, state)
);

export function buildTabsFromChildren(
  state: State,
  _modalId: string,
  children: readonly {
    tagName: string;
    attributes: Record<string, XMLValue>;
    children: readonly unknown[];
  }[],
  targetEntity: number
): TabDescriptor[] {
  const tabs: TabDescriptor[] = [];
  for (const child of children) {
    // The XML parser lowercases custom tag names, so compare case-insensitively.
    const tag = String(child.tagName).toLowerCase();
    const labelKey =
      readAttr(child.attributes, 'label-key') ??
      // Strip a trailing "tab" so <SkillsTab> → modal.tab.skills, matching the
      // engine default dictionary keys.
      `modal.tab.${tag.replace(/tab$/, '')}`;
    const tabId = readAttr(child.attributes, 'id') ?? tag;
    if (tag === 'skillstab') {
      tabs.push({
        id: tabId,
        labelKey,
        build: (s) => createSkillsTab(s, { targetEntity }),
      });
    } else if (tag === 'inventorytab') {
      const cols = readAttr(child.attributes, 'columns');
      tabs.push({
        id: tabId,
        labelKey,
        build: (s) =>
          createInventoryTab(s, {
            targetEntity,
            columns: cols ? Number(cols) : undefined,
          }),
      });
    } else if (tag === 'optionstab') {
      const defs = (
        child.children as readonly {
          tagName: string;
          attributes: Record<string, XMLValue>;
        }[]
      )
        .filter((c) => String(c.tagName).toLowerCase() === 'optionrow')
        .map((c) => parseOptionDef(c.attributes));
      for (const d of defs) registerOptionDef(state, d);
      tabs.push({
        id: tabId,
        labelKey,
        build: (s) => createOptionsTab(s, defs),
      });
    } else {
      tabs.push({
        id: tabId,
        labelKey,
        build: (s) => {
          const root = document.createElement('div');
          root.textContent = t(s, labelKey);
          return { root, refresh() {} };
        },
      });
    }
  }
  return tabs;
}

export const tabbedModalRecipe: Recipe = {
  name: TABBED_MODAL_TAG,
  components: [],
  parserAttributes: [
    'id',
    'pause-on-open',
    'key',
    'target-entity',
    'title-key',
  ],
  parserOwnsChildren: true,
};

export function tabbedModalParser({ element, state }: ParserParams): void {
  const modalId = readAttr(element.attributes, 'id') ?? 'pause';
  const targetEntity =
    resolveTargetEntity(state, readAttr(element.attributes, 'target-entity')) ??
    0;
  const tabs = buildTabsFromChildren(
    state,
    modalId,
    element.children as never,
    targetEntity
  );
  const widget = createTabbedModalWidget(element.attributes, state, { tabs });
  registerHudWidget(state, widget);
}
