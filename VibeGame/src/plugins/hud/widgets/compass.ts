import { Vector3 } from 'three';
import type { ParserParams, State, XMLValue } from '../../../core';
import { threeCameras } from '../../rendering/utils';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidget,
} from '../screen-layer';

/**
 * Compass widget — horizontal cardinal strip that scrolls with the camera yaw.
 *
 * Azimuth convention (matches `Math.atan2(dir.x, dir.z)`):
 *   - Camera facing +Z  → azimuth 0
 *   - Camera facing +X  → azimuth +π/2
 *   - Camera facing −Z  → azimuth ±π
 *   - Camera facing −X  → azimuth −π/2
 *
 * `north` is the world azimuth of the north direction (default 0 = +Z). The
 * eight cardinals are placed at `north + k·π/4`. A mark is centred when the
 * camera heading matches its world azimuth. DOM/CSS only — no WebGL, no pitch.
 */

export const COMPASS_DEFAULT_FOV = 1.7;
export const COMPASS_DEFAULT_NORTH = 0;
export const COMPASS_DEFAULT_NORTH_COLOR = '#ff8a6a';

const COMPASS_STYLE_ID = 'vibe-compass-style';
const COMPASS_MAJOR_COLOR = '#e8eef8';
const COMPASS_MINOR_COLOR = '#8a9ab8';
const COMPASS_TICK_COLOR = '#ffd24a';

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
const MAJOR_CARDINALS = new Set<string>(['N', 'E', 'S', 'W']);

const COMPASS_STYLE_CSS = `
.vibe-compass{position:absolute;top:14px;left:50%;transform:translateX(-50%);
width:min(300px,70vw);height:30px;overflow:hidden;z-index:11;pointer-events:none;
background:rgba(8,12,28,0.6);border-radius:8px;
border:1px solid rgba(120,150,220,0.22);backdrop-filter:blur(8px);
box-shadow:0 5px 18px rgba(0,0,0,0.28);
-webkit-mask-image:linear-gradient(90deg,transparent,#000 18%,#000 82%,transparent);
mask-image:linear-gradient(90deg,transparent,#000 18%,#000 82%,transparent);}
.vibe-compass-mark{position:absolute;top:0;left:50%;height:30px;min-width:24px;
display:flex;align-items:center;justify-content:center;text-align:center;
will-change:transform,opacity;transform:translateX(-50%);opacity:0;}
.vibe-compass-mark.major{font:700 14px system-ui,sans-serif;}
.vibe-compass-mark.minor{font:700 10px system-ui,sans-serif;}
.vibe-compass-tick{position:absolute;top:0;left:50%;width:2px;height:30px;
margin-left:-1px;background:linear-gradient(${COMPASS_TICK_COLOR},rgba(255,210,74,0));
pointer-events:none;}
`.trim();

export interface CardinalMark {
  label: string;
  az: number;
  major: boolean;
}

export interface MarkTransform {
  translateX: number;
  opacity: number;
  visible: boolean;
}

interface Vector3Like {
  x: number;
  y: number;
  z: number;
}

interface AzimuthCamera {
  getWorldDirection(target: Vector3Like): Vector3Like;
}

interface MountedMark {
  el: HTMLDivElement;
  az: number;
  label: string;
}

/** Place the eight cardinal marks around `north` at π/4 intervals. */
export function cardinalAzimuths(north: number): CardinalMark[] {
  return CARDINALS.map((label, i) => {
    // N=0, NE=+1, E=+2, SE=+3, S=+4, SW=−3, W=−2, NW=−1 (units of π/4).
    const step = i <= 4 ? i : i - 8;
    return {
      label,
      az: north + (step * Math.PI) / 4,
      major: MAJOR_CARDINALS.has(label),
    };
  });
}

/** Camera heading from a world-space forward direction (atan2(x, z)). */
export function cameraAzimuth(dirX: number, dirZ: number): number {
  return Math.atan2(dirX, dirZ);
}

export function wrapAngle(a: number): number {
  let r = a;
  while (r > Math.PI) r -= Math.PI * 2;
  while (r < -Math.PI) r += Math.PI * 2;
  return r;
}

/**
 * Resolve a mark's horizontal placement relative to the camera heading.
 * `halfWidth` is half the strip width in CSS pixels; marks further than `fov`
 * (radians) from the heading are hidden.
 */
export function markTransform(
  markAz: number,
  camAz: number,
  fov: number,
  halfWidth: number
): MarkTransform {
  const off = wrapAngle(markAz - camAz);
  const absOff = Math.abs(off);
  if (absOff > fov) return { translateX: 0, opacity: 0, visible: false };
  const translateX = (off / fov) * halfWidth;
  const fade = 1 - absOff / fov;
  return { translateX, opacity: 0.25 + fade * 0.75, visible: true };
}

function parseFov(value: XMLValue | undefined): number {
  if (value === undefined || value === null) return COMPASS_DEFAULT_FOV;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : COMPASS_DEFAULT_FOV;
}

function parseNorth(value: XMLValue | undefined): number {
  if (value === undefined || value === null) return COMPASS_DEFAULT_NORTH;
  const n = Number(value);
  return Number.isFinite(n) ? n : COMPASS_DEFAULT_NORTH;
}

function parseNorthColor(value: XMLValue | undefined): string {
  if (typeof value === 'string' && value.length > 0) return value;
  return COMPASS_DEFAULT_NORTH_COLOR;
}

function ensureCompassStyle(): void {
  if (document.getElementById(COMPASS_STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = COMPASS_STYLE_ID;
  style.textContent = COMPASS_STYLE_CSS;
  document.head.appendChild(style);
}

// Must be a real THREE.Vector3: camera.getWorldDirection() calls target.set()
// internally, which a plain {x,y,z} object does not have.
const _camDir = new Vector3();

function firstCameraAzimuth(): number | null {
  const cam = threeCameras.values().next().value as AzimuthCamera | undefined;
  if (!cam || typeof cam.getWorldDirection !== 'function') return null;
  cam.getWorldDirection(_camDir);
  return cameraAzimuth(_camDir.x, _camDir.z);
}

export interface CompassConfig {
  fov: number;
  north: number;
  northColor: string;
}

/** Build a Compass widget from XML attributes (see `<Compass>` recipe). */
export function createCompassWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const config: CompassConfig = {
    fov: parseFov(attributes.fov),
    north: parseNorth(attributes.north),
    northColor: parseNorthColor(attributes['mark-color-north']),
  };
  return {
    id: 'compass',
    mount: (layer: HTMLDivElement, _s: State): WidgetHandle => {
      ensureCompassStyle();
      const root = document.createElement('div');
      root.className = 'vibe-compass';
      root.setAttribute('aria-hidden', 'true');

      const tick = document.createElement('div');
      tick.className = 'vibe-compass-tick';
      root.appendChild(tick);

      const marks: MountedMark[] = [];
      for (const cardinal of cardinalAzimuths(config.north)) {
        const el = document.createElement('div');
        el.className = `vibe-compass-mark ${cardinal.major ? 'major' : 'minor'}`;
        el.dataset.cardinal = cardinal.label;
        el.textContent = cardinal.label;
        el.style.color =
          cardinal.label === 'N'
            ? config.northColor
            : cardinal.major
              ? COMPASS_MAJOR_COLOR
              : COMPASS_MINOR_COLOR;
        root.appendChild(el);
        marks.push({ el, az: cardinal.az, label: cardinal.label });
      }

      layer.appendChild(root);

      const update = (): void => {
        const camAz = firstCameraAzimuth();
        if (camAz === null) return;
        const halfWidth = root.clientWidth / 2;
        if (halfWidth === 0) return;
        for (const mark of marks) {
          const t = markTransform(mark.az, camAz, config.fov, halfWidth);
          if (!t.visible) {
            mark.el.style.opacity = '0';
            continue;
          }
          mark.el.style.transform = `translateX(calc(-50% + ${t.translateX}px))`;
          mark.el.style.opacity = String(t.opacity);
        }
      };

      return {
        root,
        update,
        unmount: (): void => {
          root.remove();
        },
      };
    },
  };
}

/** `<Compass>` recipe parser — builds and registers a Compass widget. */
export function compassParser({ element, state }: ParserParams): void {
  registerHudWidget(state, createCompassWidget(element.attributes, state));
}
