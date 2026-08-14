/**
 * In-page error card for world authoring failures. The old world keeps
 * running; saving a fixed document clears the card automatically.
 */

const OVERLAY_ID = 'vibegame-error-overlay';

function ensureStyle(element: HTMLElement): void {
  element.style.position = 'fixed';
  element.style.bottom = '16px';
  element.style.left = '16px';
  element.style.zIndex = '2147483647';
  element.style.maxWidth = 'min(560px, calc(100vw - 32px))';
  element.style.fontFamily =
    "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, monospace";
  element.style.fontSize = '12px';
  element.style.lineHeight = '1.5';
  element.style.color = '#ffb4b4';
  element.style.background = 'rgba(24, 8, 8, 0.92)';
  element.style.border = '1px solid rgba(255, 77, 79, 0.55)';
  element.style.borderRadius = '10px';
  element.style.padding = '12px 14px';
  element.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.45)';
  element.style.backdropFilter = 'blur(6px)';
  element.style.whiteSpace = 'pre-wrap';
  element.style.wordBreak = 'break-word';
  element.style.cursor = 'pointer';
}

export function showErrorOverlay(title: string, detail?: string): void {
  if (typeof document === 'undefined') return;

  let card = document.getElementById(OVERLAY_ID);
  if (!card) {
    card = document.createElement('div');
    card.id = OVERLAY_ID;
    card.title = 'Click to dismiss';
    ensureStyle(card);
    card.addEventListener('click', () => hideErrorOverlay());
    document.body.appendChild(card);
  }

  card.textContent = detail ? `⚠ ${title}\n${detail}` : `⚠ ${title}`;
}

export function hideErrorOverlay(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(OVERLAY_ID)?.remove();
}
