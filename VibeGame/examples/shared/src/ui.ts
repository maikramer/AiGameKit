/**
 * Shared example kit — glue both shipped examples reuse.
 *
 * These are DOM/engine helpers too game-flavoured for the engine itself but
 * identical across examples. Each module is single-responsibility and imports
 * only the public `vibegame` barrel, so the deep-import CI gate stays green.
 */

export interface ToastOptions {
  /** Text color (and default border/glow tint). Default soft violet. */
  color?: string;
  /** Border color. Defaults to `color`. */
  borderColor?: string;
  /** Background rgba. */
  background?: string;
  /** Vertical position (CSS `top`). Default `18%`. */
  top?: string;
  /** Font shorthand. */
  font?: string;
  /** Optional max width (e.g. long mystic lines). */
  maxWidth?: string;
  /** Extra box-shadow. */
  glow?: string;
  /** Extra text-shadow (e.g. a `currentColor` glow). */
  textGlow?: string;
  /** How long the toast stays visible (ms). Default 2200. */
  durationMs?: number;
}

let toastEl: HTMLDivElement | null = null;
let toastTimeout: ReturnType<typeof setTimeout> | null = null;

/**
 * Centre-screen toast — one shared DOM node reused by every caller (the
 * per-game modules each used to build their own copy of this).
 */
export function showToast(message: string, opts: ToastOptions = {}): void {
  if (typeof document === 'undefined') return;
  if (!toastEl) {
    toastEl = document.createElement('div');
    toastEl.style.cssText =
      'position:fixed;left:50%;transform:translateX(-50%);' +
      'border-radius:8px;padding:12px 22px;z-index:1000;text-align:center;' +
      'opacity:0;transition:opacity 0.2s;';
    document.body.appendChild(toastEl);
  }
  const color = opts.color ?? '#e8e0ff';
  toastEl.style.top = opts.top ?? '18%';
  toastEl.style.color = color;
  toastEl.style.border = `2px solid ${opts.borderColor ?? color}`;
  toastEl.style.background = opts.background ?? 'rgba(18,14,28,0.94)';
  toastEl.style.font = opts.font ?? '17px Georgia,serif';
  toastEl.style.maxWidth = opts.maxWidth ?? '';
  toastEl.style.boxShadow =
    opts.glow ?? '0 0 22px rgba(120,90,255,0.35)';
  toastEl.style.textShadow = opts.textGlow ?? '';
  toastEl.textContent = message;
  toastEl.style.opacity = '1';
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    if (toastEl) toastEl.style.opacity = '0';
  }, opts.durationMs ?? 2200);
}
