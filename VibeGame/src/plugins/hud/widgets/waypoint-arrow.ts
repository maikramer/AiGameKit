import { Vector3 } from 'three';
import { defineQuery } from '../../../core';
import type { ParserParams, Recipe, State, XMLValue } from '../../../core';
import { PlayerController } from '../../player';
import { threeCameras } from '../../rendering/utils';
import { Transform } from '../../transforms';
import {
  type Waypoint,
  formatWaypointDistance,
  getTrackedWaypoint,
  waypointColor,
  waypointDistance,
  waypointGlyph,
} from '../waypoints';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidget,
  registerHudWidgetFactory,
} from '../screen-layer';
import { injectWidgetCss } from './shared';

/**
 * Screen-space pointer to the tracked waypoint (see `hud/waypoints`).
 *
 * On-screen the marker sits on the target itself; once the target leaves the
 * viewport the marker is clamped to the screen edge and rotates to point at
 * it. Without the off-screen half a "go here" marker is only useful when the
 * player already happens to be looking the right way, which is exactly when
 * they don't need it.
 *
 *   <WaypointArrow margin="64" max-distance="0"/>
 */

const WIDGET_TYPE = 'waypoint-arrow';
const WIDGET_ID = 'vibe:waypoint-arrow';

export const DEFAULT_ARROW_MARGIN = 58;

const ARROW_CSS = `
.hud-waypoint-arrow{position:absolute;left:0;top:0;display:none;flex-direction:column;
align-items:center;gap:2px;pointer-events:none;z-index:12;will-change:transform;
transform:translate(-50%,-50%);}
.hud-waypoint-arrow[data-visible="true"]{display:flex;}
.hud-waypoint-arrow-pointer{width:34px;height:34px;display:flex;align-items:center;
justify-content:center;}
.hud-waypoint-arrow-pointer svg{width:34px;height:34px;overflow:visible;
filter:drop-shadow(0 2px 4px rgba(0,0,0,0.6));}
.hud-waypoint-arrow-glyph{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
font:800 15px 'Trebuchet MS',system-ui,sans-serif;color:#1a1408;text-shadow:none;}
.hud-waypoint-arrow-caption{padding:1px 7px;border-radius:7px;white-space:nowrap;
background:rgba(8,10,18,0.72);border:1px solid rgba(255,255,255,0.14);
font:700 11px system-ui,Segoe UI,sans-serif;color:#e9eefb;text-shadow:0 1px 2px rgba(0,0,0,0.7);}
.hud-waypoint-arrow-dist{color:#ffd88a;}
`;

export interface ArrowPlacement {
  /** Marker centre, in CSS pixels relative to the viewport. */
  readonly x: number;
  readonly y: number;
  /** Rotation for the pointer, radians, 0 = up. */
  readonly angle: number;
  /** True when the target projects inside the viewport (arrow points down at it). */
  readonly onScreen: boolean;
}

export interface Ndc {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/**
 * Turn a projected NDC point into a screen placement, clamping off-screen
 * targets to a rectangle inset by `margin`.
 *
 * `z > 1` means the point is behind the camera. Three's `project()` divides by
 * a negative w there, mirroring x/y — so the raw NDC of something directly
 * behind you reads as "slightly off to the other side", and an arrow built
 * from it points the wrong way. Negating both axes puts it back on the correct
 * side before the clamp.
 */
export function computeArrowPlacement(
  ndc: Ndc,
  width: number,
  height: number,
  margin = DEFAULT_ARROW_MARGIN
): ArrowPlacement {
  const behind = ndc.z > 1;
  const nx = behind ? -ndc.x : ndc.x;
  const ny = behind ? -ndc.y : ndc.y;

  const cx = width / 2;
  const cy = height / 2;
  const sx = (nx * 0.5 + 0.5) * width;
  const sy = (-ny * 0.5 + 0.5) * height;

  const onScreen = !behind && Math.abs(nx) <= 1 && Math.abs(ny) <= 1;
  if (onScreen) {
    return { x: sx, y: sy, angle: 0, onScreen: true };
  }

  let dx = sx - cx;
  let dy = sy - cy;
  if (behind) {
    // A point exactly behind the camera projects onto the centre; nudge it
    // downwards so the arrow still has a direction to show ("turn around").
    if (Math.abs(dx) < 1e-3 && Math.abs(dy) < 1e-3) dy = 1;
  }
  const limitX = Math.max(1, cx - margin);
  const limitY = Math.max(1, cy - margin);
  const scale = Math.min(
    limitX / Math.max(Math.abs(dx), 1e-6),
    limitY / Math.max(Math.abs(dy), 1e-6)
  );
  dx *= scale;
  dy *= scale;

  return {
    x: cx + dx,
    y: cy + dy,
    angle: Math.atan2(dx, -dy),
    onScreen: false,
  };
}

interface ArrowConfig {
  readonly margin: number;
  /** Hide the arrow past this distance in metres; 0 = never hide. */
  readonly maxDistance: number;
  readonly showLabel: boolean;
}

const playerQuery = defineQuery([PlayerController, Transform]);

function readNumber(
  attrs: Record<string, XMLValue>,
  name: string,
  fallback: number
): number {
  const raw = attrs[name];
  if (raw === undefined || raw === null) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function parseConfig(attrs: Record<string, XMLValue>): ArrowConfig {
  return {
    margin: readNumber(attrs, 'margin', DEFAULT_ARROW_MARGIN),
    maxDistance: readNumber(attrs, 'max-distance', 0),
    showLabel: String(attrs['show-label'] ?? 'true') !== 'false',
  };
}

const POINTER_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true">' +
  '<path d="M12 1.5 L21.5 21 L12 16.4 L2.5 21 Z" ' +
  'fill="currentColor" stroke="rgba(0,0,0,0.65)" stroke-width="1.4" ' +
  'stroke-linejoin="round"/></svg>';

const _worldPos = new Vector3();

export function waypointArrowFactory(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const cfg = parseConfig(attributes);

  return {
    id: WIDGET_ID,
    mount(layer: HTMLDivElement): WidgetHandle {
      injectWidgetCss(ARROW_CSS);

      const root = document.createElement('div');
      root.className = 'hud-waypoint-arrow';
      root.dataset.visible = 'false';

      const pointer = document.createElement('div');
      pointer.className = 'hud-waypoint-arrow-pointer';
      pointer.style.position = 'relative';
      pointer.innerHTML = POINTER_SVG;

      const glyph = document.createElement('span');
      glyph.className = 'hud-waypoint-arrow-glyph';
      pointer.appendChild(glyph);

      const caption = document.createElement('div');
      caption.className = 'hud-waypoint-arrow-caption';
      const labelEl = document.createElement('span');
      const distEl = document.createElement('span');
      distEl.className = 'hud-waypoint-arrow-dist';
      caption.append(labelEl, document.createTextNode(' '), distEl);

      root.append(pointer, caption);
      layer.appendChild(root);

      const hide = (): void => {
        root.dataset.visible = 'false';
      };

      const draw = (
        wp: Waypoint,
        placement: ArrowPlacement,
        distance: number
      ): void => {
        const color = waypointColor(wp);
        pointer.style.color = color;
        glyph.textContent = placement.onScreen ? waypointGlyph(wp) : '';
        // On-screen the pointer reads better as a downward marker planted on
        // the target; off-screen it rotates to show which way to turn.
        pointer.style.transform = `rotate(${
          placement.onScreen ? Math.PI : placement.angle
        }rad)`;
        // Keep the glyph upright inside the rotated pointer.
        glyph.style.transform = `translate(-50%,-50%) rotate(${
          placement.onScreen ? -Math.PI : -placement.angle
        }rad)`;
        labelEl.textContent = cfg.showLabel ? (wp.label ?? '') : '';
        distEl.textContent = formatWaypointDistance(distance);
        root.style.transform = `translate(${placement.x}px,${placement.y}px) translate(-50%,-50%)`;
        root.dataset.visible = 'true';
      };

      return {
        root,
        update(state: State): void {
          if (state.headless) return;
          const players = playerQuery(state.world);
          const player = players[0];
          if (player === undefined) return hide();

          const px = Transform.posX[player];
          const pz = Transform.posZ[player];
          const wp = getTrackedWaypoint(state, px, pz);
          if (!wp) return hide();

          const distance = waypointDistance(wp, px, pz);
          if (cfg.maxDistance > 0 && distance > cfg.maxDistance) return hide();

          const camera = threeCameras.values().next().value;
          if (!camera) return hide();

          const width = layer.clientWidth || window.innerWidth;
          const height = layer.clientHeight || window.innerHeight;
          if (width === 0 || height === 0) return hide();

          _worldPos.set(wp.x, wp.y, wp.z);
          _worldPos.project(camera);
          const placement = computeArrowPlacement(
            _worldPos,
            width,
            height,
            cfg.margin
          );
          draw(wp, placement, distance);
        },
        unmount(): void {
          root.remove();
        },
      };
    },
  };
}

registerHudWidgetFactory(WIDGET_TYPE, waypointArrowFactory);

export const waypointArrowRecipe: Recipe = {
  name: 'WaypointArrow',
  components: [],
  parserAttributes: ['margin', 'max-distance', 'show-label'],
};

export function waypointArrowParser({ element, state }: ParserParams): void {
  registerHudWidget(state, waypointArrowFactory(element.attributes, state));
}
