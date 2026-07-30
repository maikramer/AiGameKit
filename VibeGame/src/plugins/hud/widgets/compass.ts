import { Vector3 } from 'three';
import { defineQuery } from '../../../core';
import type { ParserParams, State, XMLValue } from '../../../core';
import { PlayerController } from '../../player';
import { threeCameras } from '../../rendering/utils';
import { Transform } from '../../transforms';
import {
  type Waypoint,
  formatWaypointDistance,
  getTrackedWaypointId,
  getWaypoints,
  waypointColor,
  waypointDistance,
  waypointGlyph,
} from '../waypoints';
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
.vibe-compass{position:absolute;top:12px;left:50%;transform:translateX(-50%);
width:min(280px,68vw);height:28px;overflow:hidden;z-index:11;pointer-events:none;
background:linear-gradient(145deg,rgba(18,16,22,0.88),rgba(10,12,16,0.75));
border-radius:10px;border:1px solid rgba(196,148,72,0.32);
backdrop-filter:blur(12px);box-shadow:0 5px 18px rgba(0,0,0,0.32),inset 0 1px 0 rgba(255,220,160,0.1);
-webkit-mask-image:linear-gradient(90deg,transparent,#000 16%,#000 84%,transparent);
mask-image:linear-gradient(90deg,transparent,#000 16%,#000 84%,transparent);}
.vibe-compass-mark{position:absolute;top:0;left:50%;height:28px;min-width:24px;
display:flex;align-items:center;justify-content:center;text-align:center;
will-change:transform,opacity;transform:translateX(-50%);opacity:0;}
.vibe-compass-mark.major{font:700 13px 'Trebuchet MS',system-ui,sans-serif;letter-spacing:0.04em;}
.vibe-compass-mark.minor{font:700 10px 'Trebuchet MS',system-ui,sans-serif;}
.vibe-compass-tick{position:absolute;top:0;left:50%;width:2px;height:28px;
margin-left:-1px;background:linear-gradient(${COMPASS_TICK_COLOR},rgba(255,210,74,0));
pointer-events:none;}
.vibe-compass-pip{position:absolute;top:1px;left:50%;display:flex;flex-direction:column;
align-items:center;line-height:1;transform:translateX(-50%);opacity:0;
will-change:transform,opacity;pointer-events:none;}
.vibe-compass-pip-glyph{font:800 12px 'Trebuchet MS',system-ui,sans-serif;
text-shadow:0 1px 2px rgba(0,0,0,0.8);}
.vibe-compass-pip-dist{font:700 8px system-ui,sans-serif;color:#cdd8ee;
text-shadow:0 1px 2px rgba(0,0,0,0.8);}
`.trim();

/** Waypoint pips are drawn in front of the cardinal strip, capped so a busy
 * quest log can't turn the compass into an unreadable wall of glyphs. */
const COMPASS_MAX_PIPS = 6;

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

interface MountedPip {
  el: HTMLDivElement;
  glyphEl: HTMLSpanElement;
  distEl: HTMLSpanElement;
}

const compassPlayerQuery = defineQuery([PlayerController, Transform]);

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

/**
 * World azimuth of a target as seen from a point — same `atan2(x, z)`
 * convention as {@link cameraAzimuth}, so a pip lines up with the camera
 * heading using the very same {@link markTransform}.
 */
export function waypointAzimuth(
  fromX: number,
  fromZ: number,
  toX: number,
  toZ: number
): number {
  return Math.atan2(toX - fromX, toZ - fromZ);
}

/**
 * Markers further than this (metres) are dropped from the strip — the tracked
 * one excepted. A quest giver on the far side of the map contributes nothing
 * but a duplicate glyph at whatever bearing it happens to sit.
 */
export const COMPASS_PIP_MAX_DISTANCE = 160;

export interface CompassPip {
  readonly id: string;
  readonly az: number;
  readonly distance: number;
  readonly color: string;
  readonly glyph: string;
  readonly tracked: boolean;
}

/**
 * Resolve the waypoints to show on the strip: within
 * {@link COMPASS_PIP_MAX_DISTANCE} (plus the tracked one at any range),
 * nearest first, capped at `limit`. Pure — the runtime draw and the tests
 * share it.
 */
export function selectCompassPips(
  state: State,
  fromX: number,
  fromZ: number,
  limit: number = COMPASS_MAX_PIPS,
  maxDistance: number = COMPASS_PIP_MAX_DISTANCE
): CompassPip[] {
  const trackedId = getTrackedWaypointId(state);
  const pips: CompassPip[] = [];
  for (const wp of getWaypoints(state).values()) {
    const tracked = wp.id === trackedId;
    const distance = waypointDistance(wp as Waypoint, fromX, fromZ);
    if (!tracked && distance > maxDistance) continue;
    pips.push({
      id: wp.id,
      az: waypointAzimuth(fromX, fromZ, wp.x, wp.z),
      distance,
      color: waypointColor(wp as Waypoint),
      glyph: waypointGlyph(wp as Waypoint),
      tracked,
    });
  }
  // Tracked first, then nearest — the pinned marker must survive the cap.
  pips.sort(
    (a, b) => Number(b.tracked) - Number(a.tracked) || a.distance - b.distance
  );
  return pips.slice(0, Math.max(0, limit));
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

      // Fixed pip pool: pips come and go as quests are accepted/handed in, and
      // recreating the elements each frame would thrash the DOM on a widget
      // that redraws at 30 Hz.
      const pipSlots: MountedPip[] = [];
      for (let i = 0; i < COMPASS_MAX_PIPS; i++) {
        const el = document.createElement('div');
        el.className = 'vibe-compass-pip';
        const glyphEl = document.createElement('span');
        glyphEl.className = 'vibe-compass-pip-glyph';
        const distEl = document.createElement('span');
        distEl.className = 'vibe-compass-pip-dist';
        el.append(glyphEl, distEl);
        root.appendChild(el);
        pipSlots.push({ el, glyphEl, distEl });
      }

      layer.appendChild(root);

      let lastUpdateAt = -Infinity;
      let lastCameraAzimuth = Number.NaN;
      const update = (state: State): void => {
        const now = state.time.elapsed;
        const camAz = firstCameraAzimuth();
        if (camAz === null) return;
        if (
          now - lastUpdateAt < 1 / 30 &&
          Math.abs(wrapAngle(camAz - lastCameraAzimuth)) < 1e-4
        ) {
          return;
        }
        lastUpdateAt = now;
        lastCameraAzimuth = camAz;
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
        drawPips(state, camAz, halfWidth);
      };

      function drawPips(state: State, camAz: number, halfWidth: number): void {
        const players = compassPlayerQuery(state.world);
        const player = players[0];
        const pips =
          player === undefined
            ? []
            : selectCompassPips(
                state,
                Transform.posX[player],
                Transform.posZ[player]
              );
        for (let i = 0; i < pipSlots.length; i++) {
          const slot = pipSlots[i];
          const pip = pips[i];
          if (!pip) {
            slot.el.style.opacity = '0';
            continue;
          }
          const t = markTransform(pip.az, camAz, config.fov, halfWidth);
          if (!t.visible) {
            slot.el.style.opacity = '0';
            continue;
          }
          slot.glyphEl.textContent = pip.glyph;
          slot.glyphEl.style.color = pip.color;
          slot.glyphEl.style.fontSize = pip.tracked ? '14px' : '12px';
          slot.distEl.textContent = formatWaypointDistance(pip.distance);
          slot.el.style.transform = `translateX(calc(-50% + ${t.translateX}px))`;
          // Pips must stay legible at the edges where cardinals fade out.
          slot.el.style.opacity = String(
            pip.tracked ? 1 : Math.max(0.55, t.opacity)
          );
        }
      }

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
