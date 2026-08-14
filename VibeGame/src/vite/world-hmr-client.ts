/**
 * Client-side handler for VibeGame world XML hot-reload. Receives the
 * `vibegame:world` HMR event and asks the runtime to hot-swap the `<scene>`
 * content. Normally injected automatically by `vibegameWorldHmr()` — import
 * manually only when `injectClient: false`.
 */
export function initWorldHotReload() {
  if (typeof import.meta === 'undefined' || !import.meta.hot) return;

  import.meta.hot.on('vibegame:world', (data: { file: string }) => {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('vibegame:world-reload', { detail: data })
    );
  });
}
