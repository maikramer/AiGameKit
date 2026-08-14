/**
 * Expand `<Include src="…">` (and `<include>`) before Scene XML parsing.
 * Browser: fetch URL. Headless: inject `load` that reads from disk.
 */

export const MAX_INCLUDE_DEPTH = 8;

export type IncludeLoader = (src: string) => Promise<string>;

export interface ExpandIncludesOptions {
  /** Resolve `src` to fragment XML text (Scene children, no outer html). */
  load: IncludeLoader;
  maxDepth?: number;
}

function parseSrcAttr(attrText: string): string | null {
  const dq = attrText.match(/\bsrc\s*=\s*"([^"]*)"/i);
  if (dq) return dq[1]!.trim();
  const sq = attrText.match(/\bsrc\s*=\s*'([^']*)'/i);
  if (sq) return sq[1]!.trim();
  return null;
}

/** Strip wrappers so includes can be full Scene/world files or bare fragments. */
export function unwrapIncludeFragment(content: string): string {
  let text = content.replace(/^\uFEFF/, '').trim();
  text = text.replace(/^<\?xml[^>]*\?>\s*/i, '');

  const scene = text.match(/<scene\b[^>]*>([\s\S]*)<\/scene\s*>/i);
  if (scene) return scene[1]!.trim();

  const world = text.match(/<world\b[^>]*>([\s\S]*)<\/world\s*>/i);
  if (world) return world[1]!.trim();

  return text;
}

function normalizeIncludeSrc(src: string): string {
  const t = src.trim();
  if (t === '') {
    throw new Error('[Include] src= is empty');
  }
  return t;
}

function isInsideHtmlComment(xml: string, index: number): boolean {
  const before = xml.slice(0, index);
  const lastOpen = before.lastIndexOf('<!--');
  if (lastOpen < 0) return false;
  const lastClose = before.lastIndexOf('-->');
  return lastClose < lastOpen;
}

function findNextInclude(
  xml: string
): { start: number; end: number; attrText: string } | null {
  const re = /<include\b/gi;
  let open: RegExpExecArray | null;
  while ((open = re.exec(xml)) !== null) {
    if (isInsideHtmlComment(xml, open.index)) continue;

    const start = open.index;
    const afterOpen = start + open[0].length;
    const rest = xml.slice(afterOpen);
    const gt = rest.indexOf('>');
    if (gt < 0) {
      throw new Error('[Include] malformed tag (missing >)');
    }
    const attrText = rest.slice(0, gt);
    if (attrText.trimEnd().endsWith('/')) {
      return {
        start,
        end: afterOpen + gt + 1,
        attrText: attrText.replace(/\/\s*$/, ''),
      };
    }
    const closeRe = /<\/include\s*>/i;
    const close = closeRe.exec(rest.slice(gt + 1));
    if (!close || close.index === undefined) {
      throw new Error(
        '[Include] unclosed tag — use <Include src="…"></Include>'
      );
    }
    const end = afterOpen + gt + 1 + close.index + close[0].length;
    return { start, end, attrText };
  }
  return null;
}

/**
 * Recursively replace Include tags with loaded fragment XML.
 * Fail-fast on cycles and depth > maxDepth.
 */
export async function expandIncludes(
  xml: string,
  options: ExpandIncludesOptions,
  stack: string[] = [],
  depth = 0
): Promise<string> {
  const maxDepth = options.maxDepth ?? MAX_INCLUDE_DEPTH;
  if (depth > maxDepth) {
    throw new Error(
      `[Include] max depth ${maxDepth} exceeded (stack: ${stack.join(' → ')})`
    );
  }

  let out = xml;
  // Iteratively expand the first Include at this level; nested Includes inside
  // loaded fragments are expanded recursively in loadAndExpand.
  while (true) {
    const hit = findNextInclude(out);
    if (!hit) break;

    const rawSrc = parseSrcAttr(hit.attrText);
    if (rawSrc === null) {
      throw new Error(
        `[Include] missing src= attribute near: <Include${hit.attrText}>`
      );
    }
    const src = normalizeIncludeSrc(rawSrc);
    if (stack.includes(src)) {
      throw new Error(
        `[Include] cycle detected: ${[...stack, src].join(' → ')}`
      );
    }

    const fragment = await loadAndExpand(src, options, stack, depth);
    out = out.slice(0, hit.start) + fragment + out.slice(hit.end);
  }

  return out;
}

async function loadAndExpand(
  src: string,
  options: ExpandIncludesOptions,
  stack: string[],
  depth: number
): Promise<string> {
  let loaded: string;
  try {
    loaded = await options.load(src);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`[Include] failed to load "${src}": ${msg}`, { cause: e });
  }
  const fragment = unwrapIncludeFragment(loaded);
  return expandIncludes(fragment, options, [...stack, src], depth + 1);
}

/** Minimal fetch-like signature — avoids `typeof fetch`, whose Bun flavour
 * carries extra members (e.g. `preconnect`) that plain wrappers lack. */
export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;

/** Browser fetch loader for site-root paths like `/world/cities/x.xml`. */
export function createFetchIncludeLoader(
  fetchImpl: FetchLike = fetch
): IncludeLoader {
  return async (src: string) => {
    const res = await fetchImpl(src);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${res.statusText}`);
    }
    return res.text();
  };
}
