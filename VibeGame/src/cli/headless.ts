import { JSDOM } from 'jsdom';
import * as path from 'path';
import { State } from '../core/ecs/state';
import { expandIncludes, XMLParser } from '../core/xml';
import { parseXMLToEntities } from '../core/recipes/parser';
import type { Plugin } from '../core/ecs/types';

let domInitialized = false;

function ensureDom(): void {
  if (domInitialized) return;
  if (typeof DOMParser !== 'undefined') {
    domInitialized = true;
    return;
  }

  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  (global as Record<string, unknown>).DOMParser = dom.window.DOMParser;
  (global as Record<string, unknown>).document = dom.window.document;
  domInitialized = true;
}

function normalizeBooleanAttributes(html: string): string {
  return html.replace(
    /<([a-z-]+)([^>]*?)(\s+)([a-z-]+)(?=\s*>|\s+[a-z])/gi,
    (match, tag, before, space, attr) => {
      if (before.includes(`${attr}=`) || before.includes(`${attr} =`)) {
        return match;
      }
      // Bare boolean attribute (`<Fog enabled>`) → `enabled="true"`. The old
      // `=""` form produced the same empty value the browser DOM yields,
      // which the property parser rejects with "value is empty".
      return `<${tag}${before}${space}${attr}="true"`;
    }
  );
}

export interface HeadlessOptions {
  plugins?: Plugin[];
}

export function createHeadlessState(options: HeadlessOptions = {}): State {
  ensureDom();
  const state = new State();
  state.headless = true;
  if (options.plugins) {
    for (const plugin of options.plugins) {
      state.registerPlugin(plugin);
    }
  }
  return state;
}

export function parseWorldXml(state: State, xml: string): void {
  ensureDom();
  const normalized = normalizeBooleanAttributes(xml);
  if (normalized.trim() === '') {
    return;
  }
  const wrapped = normalized.includes('<world')
    ? normalized
    : `<world>${normalized}</world>`;
  const result = XMLParser.parse(wrapped);
  parseXMLToEntities(state, result.root);
}

export async function parseWorldXmlWithIncludes(
  state: State,
  xml: string,
  options: {
    /** Map Include src → absolute file path, or directory that hosts site-root paths. */
    resolveInclude: (src: string) => string;
  }
): Promise<void> {
  const { readFile } = await import('fs/promises');
  const expanded = await expandIncludes(xml, {
    load: async (src) => readFile(options.resolveInclude(src), 'utf-8'),
  });
  parseWorldXml(state, expanded);
}

export async function loadWorldFromFile(
  state: State,
  filePath: string,
  options: {
    /**
     * Directory that mirrors the game `public/` root for `/world/…` includes.
     * Defaults to the directory containing `filePath`.
     */
    publicDir?: string;
  } = {}
): Promise<void> {
  const { readFile } = await import('fs/promises');
  const content = await readFile(filePath, 'utf-8');
  const publicDir = options.publicDir ?? path.dirname(filePath);

  const resolveInclude = (src: string): string => {
    if (src.startsWith('/')) {
      return path.join(publicDir, src.replace(/^\//, ''));
    }
    return path.resolve(publicDir, src);
  };

  const worldMatch = content.match(/<world[^>]*>([\s\S]*?)<\/world>/i);
  const sceneMatch = content.match(/<scene[^>]*>([\s\S]*?)<\/scene\s*>/i);
  const body = worldMatch
    ? worldMatch[0]
    : sceneMatch
      ? sceneMatch[1]!
      : content;

  await parseWorldXmlWithIncludes(state, body, { resolveInclude });
}
