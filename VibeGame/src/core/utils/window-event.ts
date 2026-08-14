/**
 * Fire a window CustomEvent without ever letting the dispatch fail the
 * caller. In hybrid/test environments `window` may be a foreign realm whose
 * dispatchEvent rejects native events — the engine must not crash on that.
 */
export function dispatchWindowEvent(name: string, detail?: unknown): void {
  if (typeof window === 'undefined') return;
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  } catch {
    // Foreign window realm — the event is best-effort.
  }
}
