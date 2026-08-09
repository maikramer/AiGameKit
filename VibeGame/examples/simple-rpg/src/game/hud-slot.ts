// Shared HUD slot factory — the ability bar and the hotbar used to build
// near-identical slot cards (icon, key badge, glass gradient) inline.

export interface HudSlotSpec {
  /** Icon: a PNG path (rendered as <img>) or an emoji (rendered as text). */
  icon: string;
  label: string;
  /** Shortcut key shown on the top-left badge. */
  key: string;
  /** Border tint (hex, gets an alpha suffix). */
  color: string;
  size?: number;
  iconFontSize?: number;
  iconImgSize?: number;
}

export interface HudSlot {
  root: HTMLDivElement;
  keyBadge: HTMLSpanElement;
}

/** One glassy slot card: icon + key badge, mounted by the caller. */
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

  root.appendChild(keyBadge);
  return { root, keyBadge };
}
