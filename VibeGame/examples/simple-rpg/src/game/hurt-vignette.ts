// Full-screen red vignette flashed when the hero takes a hit. Pure DOM: the
// postprocessing stack stays untouched (the game's effect band is deliberately
// subtle — bloom 0.18, vignette 0.36) and a CSS radial-gradient costs nothing
// per frame.

export interface HurtVignette {
  /** Punch the overlay in; it fades out on its own. 1 = normal hit. */
  flash(intensity?: number): void;
  dispose(): void;
}

export function mountHurtVignette(): HurtVignette {
  const el = document.createElement('div');
  el.id = 'hurt-vignette';
  el.setAttribute('aria-hidden', 'true');
  el.style.cssText = [
    'position: fixed',
    'inset: 0',
    'pointer-events: none',
    'z-index: 80',
    'background: radial-gradient(ellipse at center, transparent 42%, rgba(168, 18, 18, 0.62) 100%)',
    'opacity: 0',
    'transition: opacity 0.35s ease-out',
  ].join('; ');
  document.body.appendChild(el);

  let fadeTimer: ReturnType<typeof setTimeout> | undefined;

  return {
    flash(intensity = 1) {
      const peak = Math.min(0.85, 0.3 + 0.35 * intensity);
      el.style.transition = 'opacity 0.04s ease-out';
      el.style.opacity = String(peak);
      if (fadeTimer !== undefined) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(() => {
        el.style.transition = 'opacity 0.35s ease-out';
        el.style.opacity = '0';
      }, 60);
    },
    dispose() {
      if (fadeTimer !== undefined) clearTimeout(fadeTimer);
      el.remove();
    },
  };
}
