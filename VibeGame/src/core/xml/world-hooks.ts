/**
 * World XML hooks — the seam between authored XML and generated geometry.
 *
 * A world split into `<Include>` files (the format every non-toy example ends
 * up in) is only assembled inside the runtime, long after game code has run:
 * `document.querySelector('RaceTrack')` in `main.ts` finds nothing, because the
 * circuit lives in `/world/circuit.xml` and has not been fetched yet.
 *
 * A hook runs on the fully expanded document, just before it becomes entities,
 * so generated attributes (a circuit centerline, a road path, a procedurally
 * placed district) can be written into tags that were authored in their own
 * file. Without it, anything computed in TypeScript has to stay inline in
 * `index.html` — which is exactly the "one giant file" the includes exist to
 * avoid.
 */

import type { ParsedElement } from './types';
import type { findElements } from './traverser';

/**
 * Mutates the expanded world root before entities are created.
 *
 * The tree is the parsed XML (`ParsedElement`), not DOM: use
 * {@link findElements} to reach a tag and write plain values into
 * `element.attributes` — recipes accept the same strings an author would type.
 */
export type WorldXmlHook = (root: ParsedElement) => void;

const hooks: WorldXmlHook[] = [];

/**
 * Register a hook. Returns an unregister function.
 *
 * Hooks run in registration order on every world parse (a runtime restart
 * re-runs them), so keep them idempotent — set attributes, do not append.
 */
export function onWorldXml(hook: WorldXmlHook): () => void {
  hooks.push(hook);
  return () => {
    const i = hooks.indexOf(hook);
    if (i >= 0) hooks.splice(i, 1);
  };
}

/** Drop every hook (tests / teardown). */
export function clearWorldXmlHooks(): void {
  hooks.length = 0;
}

/**
 * Run every hook on `root`. A throwing hook is logged by the caller-supplied
 * `onError` and skipped: a broken generator must not take the whole world down.
 */
export function applyWorldXmlHooks(
  root: ParsedElement,
  onError?: (error: unknown) => void
): void {
  for (const hook of hooks) {
    try {
      hook(root);
    } catch (error) {
      onError?.(error);
    }
  }
}
