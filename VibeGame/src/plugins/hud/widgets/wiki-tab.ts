import { getDataRegistry } from '../../rpg-core';
import type { WikiPageDef } from '../../rpg-core/types';
import type { State, XMLValue } from '../../../core';
import { t } from '../../i18n/utils';
import { injectWidgetCss, readAttr } from './shared';
import type { TabContent } from './tabbed-modal-shared';

export interface WikiTabConfig {
  /** Prefer these ids (registry order otherwise). */
  pageIds?: readonly string[];
  /** Inline pages from XML children (merged over registry). */
  pages?: readonly WikiPageDef[];
}

const WIKI_CSS = `
.hud-modal-wiki{display:grid;grid-template-columns:148px 1fr;gap:14px;min-height:280px;}
.hud-modal-wiki-nav{display:flex;flex-direction:column;gap:4px;overflow-y:auto;max-height:52vh;padding-right:4px;border-right:1px solid rgba(130,160,230,0.12);}
.hud-modal-wiki-cat{font:800 10px "Segoe UI",system-ui,sans-serif;letter-spacing:0.7px;text-transform:uppercase;color:#9aa8c4;padding:10px 6px 4px;}
.hud-modal-wiki-link{text-align:left;padding:8px 10px;border-radius:8px;border:1px solid transparent;background:transparent;color:#c5d0e6;font:600 12px "Segoe UI",system-ui,sans-serif;cursor:pointer;pointer-events:auto;}
.hud-modal-wiki-link:hover{background:rgba(130,160,230,0.12);}
.hud-modal-wiki-link[data-active="true"]{background:rgba(201,176,122,0.14);border-color:rgba(201,176,122,0.35);color:#f3e7c4;}
.hud-modal-wiki-article{display:flex;flex-direction:column;gap:10px;overflow-y:auto;max-height:52vh;padding:2px 4px 8px 2px;}
.hud-modal-wiki-title{font:800 18px "Segoe UI",system-ui,sans-serif;color:#eef2fb;letter-spacing:0.3px;}
.hud-modal-wiki-body{font:500 13px/1.55 "Segoe UI",system-ui,sans-serif;color:#b7c3da;white-space:pre-wrap;}
.hud-modal-wiki-empty{color:#7c8aa8;font:600 13px "Segoe UI",system-ui,sans-serif;padding:24px 8px;}
`;

function parseWikiPage(
  attrs: Record<string, XMLValue>,
  body?: string
): WikiPageDef {
  const id = readAttr(attrs, 'id') ?? readAttr(attrs, 'page-id') ?? '';
  return {
    id,
    title: readAttr(attrs, 'title') ?? id,
    body: body?.trim() || readAttr(attrs, 'body') || '',
    category: readAttr(attrs, 'category'),
    icon: readAttr(attrs, 'icon'),
    order: (() => {
      const raw = readAttr(attrs, 'order');
      return raw !== undefined ? Number(raw) : undefined;
    })(),
  };
}

export function parseWikiPageChild(child: {
  tagName: string;
  attributes: Record<string, XMLValue>;
  textContent?: string;
  children?: readonly unknown[];
}): WikiPageDef | null {
  if (String(child.tagName).toLowerCase() !== 'wikipage') return null;
  const extra = child as { content?: string };
  const text = child.textContent ?? extra.content ?? '';
  return parseWikiPage(child.attributes, text);
}

function collectPages(state: State, cfg: WikiTabConfig): WikiPageDef[] {
  const registry = getDataRegistry(state);
  const fromReg = registry.all<WikiPageDef>('wiki');
  const byId = new Map<string, WikiPageDef>();
  for (const p of fromReg) byId.set(p.id, p);
  if (cfg.pages) {
    for (const p of cfg.pages) byId.set(p.id, p);
  }
  let list = [...byId.values()];
  if (cfg.pageIds && cfg.pageIds.length > 0) {
    list = cfg.pageIds
      .map((id) => byId.get(id))
      .filter((p): p is WikiPageDef => !!p);
  } else {
    list.sort((a, b) => {
      const oa = a.order ?? 0;
      const ob = b.order ?? 0;
      if (oa !== ob) return oa - ob;
      const ca = a.category ?? '';
      const cb = b.category ?? '';
      if (ca !== cb) return ca.localeCompare(cb);
      return a.title.localeCompare(b.title);
    });
  }
  return list;
}

export function createWikiTab(
  state: State,
  cfg: WikiTabConfig = {}
): TabContent {
  injectWidgetCss(WIKI_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-wiki';

  const nav = document.createElement('div');
  nav.className = 'hud-modal-wiki-nav';
  const article = document.createElement('div');
  article.className = 'hud-modal-wiki-article';
  root.append(nav, article);

  let activeId = '';
  const linkEls = new Map<string, HTMLButtonElement>();

  function showPage(page: WikiPageDef | undefined): void {
    article.textContent = '';
    if (!page) {
      const empty = document.createElement('div');
      empty.className = 'hud-modal-wiki-empty';
      empty.textContent = t(state, 'modal.wikiEmpty');
      article.appendChild(empty);
      return;
    }
    activeId = page.id;
    for (const [id, btn] of linkEls) {
      btn.dataset.active = id === activeId ? 'true' : 'false';
    }
    const title = document.createElement('div');
    title.className = 'hud-modal-wiki-title';
    title.textContent = page.icon ? `${page.icon}  ${page.title}` : page.title;
    const body = document.createElement('div');
    body.className = 'hud-modal-wiki-body';
    body.textContent = page.body;
    article.append(title, body);
  }

  function rebuildNav(pages: WikiPageDef[]): void {
    nav.textContent = '';
    linkEls.clear();
    if (pages.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'hud-modal-wiki-empty';
      empty.textContent = t(state, 'modal.wikiEmpty');
      nav.appendChild(empty);
      showPage(undefined);
      return;
    }

    let lastCat = '\0';
    for (const page of pages) {
      const cat = page.category || t(state, 'modal.wikiGeneral');
      if (cat !== lastCat) {
        const h = document.createElement('div');
        h.className = 'hud-modal-wiki-cat';
        h.textContent = cat;
        nav.appendChild(h);
        lastCat = cat;
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hud-modal-wiki-link';
      btn.textContent = page.title;
      btn.addEventListener('click', () => showPage(page));
      nav.appendChild(btn);
      linkEls.set(page.id, btn);
    }

    const keep = pages.find((p) => p.id === activeId) ?? pages[0];
    showPage(keep);
  }

  function refresh(): void {
    rebuildNav(collectPages(state, cfg));
  }

  refresh();

  return { root, refresh };
}

export { parseWikiPage };
