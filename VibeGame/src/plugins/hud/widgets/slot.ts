// Shared HUD slot card factory — the hotbar, the ability bar and any quick-use
// row build the same glassy card (icon, key badge, glass gradient) from this.

export interface HudSlotSpec {
  /** Icon: an image path (rendered as <img>) or an emoji (rendered as text). */
  icon: string;
  label: string;
  /** Shortcut key shown on the top-left badge. */
  key: string;
  /** Border tint (hex, gets an alpha suffix). */
  color: string;
  size?: number;
  iconFontSize?: number;
  iconImgSize?: number;
  /** Extra badge text on the bottom-right (e.g. an owned count). */
  count?: number;
}

export interface HudSlot {
  root: HTMLDivElement;
  keyBadge: HTMLSpanElement;
  countBadge: HTMLSpanElement;
}

/** One glassy slot card: icon + key badge (+ optional count), mounted by the caller. */
export function createHudSlot(spec: HudSlotSpec): HudSlot {
  const size = spec.size ?? 54;
  const root = document.createElement('div');
  root.style.cssText =
    `position:relative;width:${size}px;height:${size}px;border-radius:11px;` +
    'display:flex;align-items:center;justify-content:center;' +
    `font-size:${spec.iconFontSize ?? 26}px;line-height:1;` +
    `border:1px solid ${spec.color}55;` +
    'background:linear-gradient(135deg,rgba(14,18,34,0.78),rgba(10,14,26,0.66));' +
    'backdrop-filter:blur(10px);box-shadow:0 5px 18px rgba(0,0,0,0.3);' +
    'pointer-events:auto;';

  if (spec.icon.includes('/')) {
    const img = document.createElement('img');
    img.src = spec.icon;
    img.alt = spec.label;
    img.style.cssText =
      `width:${spec.iconImgSize ?? 42}px;height:${spec.iconImgSize ?? 42}px;` +
      'object-fit:contain;';
    root.appendChild(img);
  } else {
    root.textContent = spec.icon;
  }
  root.title = `[${spec.key}] ${spec.label}`;

  const keyBadge = document.createElement('span');
  keyBadge.textContent = spec.key;
  keyBadge.style.cssText =
    'position:absolute;top:-7px;left:-7px;min-width:17px;height:17px;padding:0 4px;' +
    'border-radius:5px;background:#1b2238;color:#cfe;' +
    'border:1px solid rgba(255,255,255,0.18);' +
    'font:800 11px system-ui,sans-serif;' +
    'display:flex;align-items:center;justify-content:center;';

  const countBadge = document.createElement('span');
  countBadge.style.cssText =
    'position:absolute;bottom:-6px;right:-6px;min-width:17px;height:17px;padding:0 4px;' +
    'border-radius:5px;background:#20304a;color:#e8f2ff;' +
    'border:1px solid rgba(255,255,255,0.18);' +
    'font:700 10px system-ui,sans-serif;' +
    'display:none;align-items:center;justify-content:center;';

  if (spec.count !== undefined && spec.count > 0) {
    countBadge.style.display = 'flex';
    countBadge.textContent = String(spec.count);
  }

  root.append(keyBadge, countBadge);
  return { root, keyBadge, countBadge };
}
