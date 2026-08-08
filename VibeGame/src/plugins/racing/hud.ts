import type { State, XMLValue } from '../../core';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../hud/screen-layer';
import { registerHudWidgetFactory } from '../hud/screen-layer';
import {
  formatTime,
  injectWidgetCss,
  readAttr,
  resolveTargetEntity,
} from '../hud/widgets/shared';
import { WorldTransform } from '../transforms';
import {
  PlayerVehicle,
  RaceTracker,
  Vehicle,
} from './components';
import { getRaceState } from './race-state';

const CSS = `
/* ---- NFS Underground Neon HUD Theme ---- */
@keyframes nfs-pulse { 0%,100%{opacity:1} 50%{opacity:0.7} }
@keyframes nfs-glow { 0%{text-shadow:0 0 8px currentColor,0 0 20px currentColor} 100%{text-shadow:0 0 16px currentColor,0 0 40px currentColor,0 0 60px currentColor} }

.race-countdown {
  position: absolute; top: 22%; left: 50%; transform: translate(-50%, -50%);
  font: 900 11rem/1 system-ui, Arial, sans-serif;
  color: #00ddff;
  text-shadow: 0 0 24px #00ddff, 0 0 60px #0088cc, 0 4px 0 rgba(0,0,0,0.5);
  letter-spacing: -0.04em; pointer-events: none; user-select: none;
  opacity: 0; transition: opacity 0.12s ease-out, transform 0.12s ease-out;
}
.race-countdown.visible { opacity: 1; transform: translate(-50%, -50%) scale(1.06); animation: nfs-glow 0.6s ease-in-out infinite alternate; }
.race-countdown.go { color: #ffe14d; font-size: 9rem; text-shadow: 0 0 24px #ffe14d, 0 0 60px #ff9900, 0 4px 0 rgba(0,0,0,0.5); }
.race-countdown.finished { color: #6dff8e; font-size: 7rem; text-shadow: 0 0 24px #6dff8e, 0 0 60px #33cc55; }
.race-countdown-sub {
  position: absolute; top: calc(22% + 7rem); left: 50%; transform: translateX(-50%);
  font: 700 1.4rem/1 system-ui, Arial, sans-serif; color: rgba(0,221,255,0.9);
  text-shadow: 0 0 10px #00ddff, 0 2px 8px rgba(0,0,0,0.7); letter-spacing: 0.15em; text-transform: uppercase;
}

/* Panel base — dark glass with neon border */
.race-panel {
  background: linear-gradient(135deg, rgba(8,10,18,0.82), rgba(12,16,28,0.75));
  border: 1px solid rgba(0,200,255,0.25);
  border-radius: 8px;
  box-shadow: 0 0 15px rgba(0,150,255,0.08), inset 0 0 20px rgba(0,180,255,0.04);
  backdrop-filter: blur(8px);
}
.race-panel::before {
  content:''; position:absolute; inset:-1px; border-radius:9px;
  padding:1px; background:linear-gradient(135deg,rgba(0,220,255,0.3),transparent,rgba(0,220,255,0.15));
  -webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);
  -webkit-mask-composite:xor; mask-composite:exclude; pointer-events:none;
}

.race-lap {
  position: absolute; top: 18px; right: 20px;
  padding: 8px 16px; color: #fff;
  font: 700 1.05rem/1.2 system-ui, Arial, sans-serif;
  min-width: 96px; text-align: right;
}
.race-lap { /* extend panel */ }
.race-lap .label { font-size: 0.68rem; font-weight: 600; color: #00ddff; letter-spacing: 0.18em; text-transform: uppercase; }
.race-lap .value { font-size: 1.5rem; font-weight: 900; color: #fff; text-shadow: 0 0 8px rgba(0,200,255,0.5); }
.race-lap .best { font-size: 0.7rem; color: #aaccff; margin-top: 3px; }

.race-speedo {
  position: absolute; bottom: 22px; right: 22px;
  padding: 10px 18px; color: #fff;
  text-align: right; min-width: 130px;
}
.race-speedo .value {
  font: 900 2.4rem/1 'Segoe UI', system-ui, Arial, sans-serif;
  color: #fff; text-shadow: 0 0 12px rgba(0,200,255,0.6), 0 0 30px rgba(0,150,255,0.3);
}
.race-speedo .unit { font: 700 0.72rem/1 system-ui, Arial, sans-serif; color: #00ddff; letter-spacing: 0.2em; text-transform: uppercase; }
.race-speedo-bar {
  height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px;
  margin-top: 7px; overflow: hidden; box-shadow: inset 0 0 4px rgba(0,0,0,0.5);
}
.race-speedo-fill {
  height: 100%; width: 0%;
  background: linear-gradient(90deg, #00aaff, #00ddff 45%, #ffe14d 80%, #ff3344);
  box-shadow: 0 0 8px rgba(0,200,255,0.5), 0 0 16px rgba(0,150,255,0.25);
  transition: width 0.06s linear;
}

.race-timer {
  position: absolute; top: 18px; left: 50%; transform: translateX(-50%);
  padding: 6px 20px; color: #fff;
  font: 800 1.6rem/1 'Courier New', Courier, monospace;
  font-variant-numeric: tabular-nums; letter-spacing: 0.04em;
  text-shadow: 0 0 10px rgba(0,200,255,0.5), 0 0 24px rgba(0,120,200,0.25);
}

.race-hint {
  position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
  color: rgba(0,200,255,0.55); font: 600 0.82rem/1 system-ui, Arial, sans-serif;
  text-shadow: 0 0 8px rgba(0,180,255,0.3); letter-spacing: 0.06em;
}

/* ---- Nitro Bar (bottom-left, NFS style) ---- */
.race-nitro {
  position: absolute; bottom: 22px; left: 22px;
  padding: 10px 16px;
  min-width: 140px; text-align: left;
}
.race-nitro .label {
  font-size: 0.68rem; font-weight: 600; color: #ff6600;
  letter-spacing: 0.18em; text-transform: uppercase;
  text-shadow: 0 0 8px rgba(255,100,0,0.5);
}
.race-nitro-bar {
  height: 10px; background: rgba(255,255,255,0.08); border-radius: 5px;
  margin-top: 6px; overflow: hidden;
  box-shadow: inset 0 0 4px rgba(0,0,0,0.5), 0 0 12px rgba(255,150,0,0.15);
  border: 1px solid rgba(255,150,0,0.25);
}
.race-nitro-fill {
  height: 100%; width: 100%;
  background: linear-gradient(90deg, #ff2200, #ff6600 40%, #ffaa00 80%, #ffee00);
  box-shadow: 0 0 10px rgba(255,100,0,0.7), 0 0 20px rgba(255,60,0,0.35);
  transition: width 0.08s linear;
}
.race-nitro-fill.empty { opacity: 0.4; }
.race-nitro-fill.active {
  animation: nitro-pulse 0.15s ease-in-out infinite alternate;
  box-shadow: 0 0 14px rgba(255,150,0,0.9), 0 0 30px rgba(255,80,0,0.5);
}
@keyframes nitro-pulse { from{filter:brightness(1)} to{filter:brightness(1.5)} }
.race-nitro-hint {
  font-size: 0.65rem; color: rgba(255,180,100,0.6); margin-top: 4px;
  letter-spacing: 0.04em;
}

/* ---- Minimap Radar (top-left corner) ---- */
.race-minimap {
  position: absolute; top: 18px; left: 18px;
  width: 200px; height: 200px;
  background: linear-gradient(135deg, rgba(8,12,20,0.85), rgba(15,20,30,0.75));
  border: 1px solid rgba(0,200,255,0.2);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 0 20px rgba(0,0,0,0.4), inset 0 0 15px rgba(0,180,255,0.05);
}
.race-minimap canvas {
  width: 100%;
  height: 100%;
  display: block;
}
.race-minimap-label {
  position: absolute; bottom: 3px; left: 50%; transform: translateX(-50%);
  font: 600 0.55rem/1 system-ui, Arial, sans-serif;
  color: rgba(0,200,255,0.5); letter-spacing: 0.15em; text-transform: uppercase;
  pointer-events: none;
}

/* ---- Camera Mode Indicator (bottom-center) ---- */
.race-camera-mode {
  position: absolute; bottom: 50px; left: 50%; transform: translateX(-50%);
  padding: 4px 14px;
  font: 700 0.7rem/1 system-ui, Arial, sans-serif;
  color: rgba(0,200,255,0.6);
  letter-spacing: 0.2em; text-transform: uppercase;
  background: rgba(8,10,18,0.7);
  border: 1px solid rgba(0,200,255,0.2);
  border-radius: 4px;
  pointer-events: none;
  transition: color 0.2s, border-color 0.2s;
}
.race-camera-mode.active {
  color: #ff2244;
  border-color: rgba(255,34,68,0.4);
  text-shadow: 0 0 8px rgba(255,34,68,0.5);
}
`;

let cssInjected = false;
function ensureCss(): void {
  if (cssInjected) return;
  injectWidgetCss(CSS);
  cssInjected = true;
}

function kmh(speedMps: number): number {
  return Math.max(0, Math.round(Math.abs(speedMps) * 3.6));
}

/** Extract yaw (heading) from a quaternion — same as vehicle-control.ts. */
function eulerY(x: number, y: number, z: number, w: number): number {
  const siny_cosp = 2 * (w * y + z * x);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  return Math.atan2(siny_cosp, cosy_cosp);
}

// ---- Countdown / phase widget -------------------------------------------

function createCountdownWidget(
  _attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  return {
    id: 'race-countdown',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-countdown';
      const sub = document.createElement('div');
      sub.className = 'race-countdown-sub';
      layer.appendChild(root);
      layer.appendChild(sub);

      const update = (): void => {
        const race = getRaceState();
        if (race.phase === 'countdown') {
          const n = Math.ceil(race.countdown);
          root.textContent = n > 0 ? String(n) : 'GO!';
          root.className = 'race-countdown visible' + (n <= 0 ? ' go' : '');
          sub.textContent = n > 0 ? 'Get ready' : '';
        } else if (race.phase === 'finished') {
          root.textContent = 'FINISH';
          root.className = 'race-countdown visible finished';
          sub.textContent = 'Press R to restart';
        } else if (race.phase === 'idle') {
          root.textContent = '';
          root.className = 'race-countdown';
          sub.textContent = '';
        } else {
          root.textContent = '';
          root.className = 'race-countdown';
          sub.textContent = '';
        }
      };

      update();
      return {
        root,
        update,
        unmount: () => {
          root.remove();
          sub.remove();
        },
      };
    },
  };
}

// ---- Lap counter widget --------------------------------------------------

function createLapWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target');
  return {
    id: 'race-lap',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-lap race-panel';
      const label = document.createElement('div');
      label.className = 'label';
      label.textContent = 'LAP';
      const value = document.createElement('div');
      value.className = 'value';
      value.textContent = '1/3';
      const best = document.createElement('div');
      best.className = 'best';
      best.textContent = 'Best --:--';
      root.appendChild(label);
      root.appendChild(value);
      root.appendChild(best);
      layer.appendChild(root);

      const update = (): void => {
        const race = getRaceState();
        const eid =
          resolveTargetEntity(state as State, targetRaw, ['hero', 'player']) ?? 0;
        let lap = 0;
        let total = race.totalLaps;
        let bestLap = -1;
        if (eid && state.hasComponent(eid, RaceTracker)) {
          lap = RaceTracker.lap[eid];
          bestLap = RaceTracker.bestLapTime[eid];
          // Show the lap in progress (1-indexed) until finished.
          if (RaceTracker.finished[eid]) lap = total;
          else lap = Math.min(lap + 1, total);
        }
        value.textContent = `${Math.max(1, lap)}/${total}`;
        best.textContent = bestLap > 0 ? `Best ${formatTime(bestLap)}` : 'Best --:--';
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

// ---- Speedometer widget --------------------------------------------------

function createSpeedoWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target');
  return {
    id: 'race-speedo',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-speedo race-panel';
      const value = document.createElement('div');
      value.className = 'value';
      value.textContent = '0';
      const unit = document.createElement('div');
      unit.className = 'unit';
      unit.textContent = 'KM/H';
      const bar = document.createElement('div');
      bar.className = 'race-speedo-bar';
      const fill = document.createElement('div');
      fill.className = 'race-speedo-fill';
      bar.appendChild(fill);
      root.appendChild(value);
      root.appendChild(unit);
      root.appendChild(bar);
      layer.appendChild(root);

      const update = (): void => {
        const eid =
          resolveTargetEntity(state as State, targetRaw, ['hero', 'player']) ?? 0;
        let speed = 0;
        let frac = 0;
        if (eid && state.hasComponent(eid, Vehicle)) {
          speed = Vehicle.speed[eid];
          const max = Vehicle.maxSpeed[eid] || 1;
          frac = Math.max(0, Math.min(1, Math.abs(speed) / max));
        }
        value.textContent = String(kmh(speed));
        fill.style.width = `${frac * 100}%`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

// ---- Race timer widget ---------------------------------------------------

function createTimerWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target');
  return {
    id: 'race-timer',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-timer race-panel';
      root.textContent = '0:00.0';
      layer.appendChild(root);

      const update = (): void => {
        const race = getRaceState();
        const eid =
          resolveTargetEntity(state as State, targetRaw, ['hero', 'player']) ?? 0;
        if (race.phase === 'racing') {
          const t = state.time.realtimeSinceStartup - race.raceStartTime;
          root.textContent = formatTimeTenths(t);
        } else if (race.phase === 'finished' && eid) {
          root.textContent = formatTimeTenths(RaceTracker.finishTime[eid]);
        } else {
          root.textContent = '0:00.0';
        }
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

// ---- Controls hint widget ------------------------------------------------

// ---- Nitro bar widget -----------------------------------------------------

function createNitroWidget(
  _attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  return {
    id: 'race-nitro',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-nitro race-panel';
      const label = document.createElement('div');
      label.className = 'label';
      label.textContent = 'NITRO';
      const bar = document.createElement('div');
      bar.className = 'race-nitro-bar';
      const fill = document.createElement('div');
      fill.className = 'race-nitro-fill';
      bar.appendChild(fill);
      const hint = document.createElement('div');
      hint.className = 'race-nitro-hint';
      hint.textContent = '[SHIFT] hold';
      root.appendChild(label);
      root.appendChild(bar);
      root.appendChild(hint);
      layer.appendChild(root);

      const update = (): void => {
        // Read nitro state from system (stored on state by NitroSystem).
        const nitro = (state as unknown as { __nitro?: { amount: number; active: number; maxAmount: number } }).__nitro;
        if (nitro) {
          const pct = (nitro.amount / nitro.maxAmount) * 100;
          fill.style.width = `${pct}%`;
          fill.classList.toggle('empty', pct < 10);
          fill.classList.toggle('active', !!nitro.active && pct > 5);
        } else {
          fill.style.width = '100%';
        }
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

function createMinimapWidget(
  _attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  /* Minimap radar — draws track outline + car dot on a 160×160 canvas. */
  function buildRoot(): HTMLDivElement {
    ensureCss();
    const root = document.createElement('div');
    root.className = 'race-minimap';

    const label = document.createElement('div');
    label.className = 'race-minimap-label';
    label.textContent = 'TRACK';
    root.appendChild(label);

    const canvas = document.createElement('canvas');
    canvas.width = 200;
    canvas.height = 200;
    root.appendChild(canvas);

    return root;
  }

  function renderFrame(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement): void {
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    // Background grid.
    ctx.strokeStyle = 'rgba(0,200,255,0.08)';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 8; i++) {
      ctx.beginPath(); ctx.moveTo((w / 8) * i, 0); ctx.lineTo((w / 8) * i, h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, (h / 8) * i); ctx.lineTo(w, (h / 8) * i); ctx.stroke();
    }

    // Track outline from centerline stored by TrackSpawnSystem.
    const trackData = (state as any).__trackCenterline as number[] | undefined;
    if (!trackData || trackData.length < 4) {
      ctx.fillStyle = 'rgba(0,200,255,0.3)';
      ctx.font = '11px monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('NO SIGNAL', w / 2, h / 2);
      addScanlines(ctx, w, h);
      return;
    }

    // Build points & bounds.
    const pts: [number, number][] = [];
    const n = Math.floor(trackData.length / 2);
    for (let i = 0; i < n; i++) pts.push([trackData[i * 2]!, trackData[i * 2 + 1]!]);

    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const [x, z] of pts) {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    const pad = 16;
    const rangeX = (maxX - minX) || 1;
    const rangeZ = (maxZ - minZ) || 1;
    const scale = Math.min((w - pad * 2) / rangeX, (h - pad * 2) / rangeZ);

    const toScreen = (x: number, z: number): [number, number] => [
      pad + (x - minX) * scale,
      pad + (z - minZ) * scale,
    ];

    // Road shape — glowing cyan.
    ctx.beginPath();
    ctx.strokeStyle = '#00ddff';
    ctx.lineWidth = 2.5;
    ctx.shadowColor = '#00ddff';
    ctx.shadowBlur = 6;
    const first = toScreen(pts[0][0], pts[0][1]);
    ctx.moveTo(first[0], first[1]);
    for (let i = 1; i < pts.length; i++) { const sp = toScreen(pts[i][0], pts[i][1]); ctx.lineTo(sp[0], sp[1]); }
    if (pts.length > 2) ctx.lineTo(first[0], first[1]);
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Car dot.
    const vehicleEid = resolveTargetEntity(state as State, '', ['hero', 'player']) ?? 0;
    if (vehicleEid > 0 && state.hasComponent(vehicleEid, WorldTransform)) {
      const px = WorldTransform.posX[vehicleEid];
      const pz = WorldTransform.posZ[vehicleEid];
      if (Number.isFinite(px) && Number.isFinite(pz)) {
        const cp = toScreen(px, pz);
        ctx.beginPath(); ctx.fillStyle = '#ff2244'; ctx.shadowColor = '#ff2244'; ctx.shadowBlur = 8;
        ctx.arc(cp[0], cp[1], 4, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;

        // Direction indicator.
        const yaw = eulerY(WorldTransform.rotX[vehicleEid], WorldTransform.rotY[vehicleEid], WorldTransform.rotZ[vehicleEid], WorldTransform.rotW[vehicleEid]);
        ctx.beginPath(); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5;
        ctx.moveTo(cp[0], cp[1]); ctx.lineTo(cp[0] + Math.sin(yaw) * 10, cp[1] - Math.cos(yaw) * 10); ctx.stroke();
      }
    }

    // Start/finish marker.
    if (pts.length >= 2) {
      const st = toScreen(pts[0][0], pts[0][1]);
      const next = toScreen(pts[1][0], pts[1][1]);
      const dx = next[0] - st[0]; const dz = next[1] - st[1];
      const perpX = -dz; const perpZ = dx;
      const pl = Math.hypot(perpX, perpZ) || 1;
      ctx.beginPath(); ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
      ctx.moveTo(st[0] + (perpX / pl) * 8, st[1] + (perpZ / pl) * 8);
      ctx.lineTo(st[0] - (perpX / pl) * 8, st[1] - (perpZ / pl) * 8); ctx.stroke(); ctx.setLineDash([]);
    }

    addScanlines(ctx, w, h);
  }

  function addScanlines(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    ctx.fillStyle = 'rgba(0,0,0,0.03)';
    for (let y = 0; y < h; y += 2) ctx.fillRect(0, y, w, 1);
  }

  return {
    id: 'race-minimap',
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = buildRoot();
      const canvas = root.querySelector('canvas') as HTMLCanvasElement;
      const ctx = canvas!.getContext('2d')!;
      renderFrame(ctx, canvas);
      layer.appendChild(root);
      return {
        root,
        update(): void { renderFrame(ctx, canvas); },
        unmount(): void { root.remove(); },
      };
    },
  };
}

function createCameraModeWidget(
  _attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  return {
    id: 'race-camera-mode',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-camera-mode';
      root.textContent = 'CHASE';
      layer.appendChild(root);

      const modeLabels: Record<string, string> = {
        chase: 'CHASE',
        orbit: 'ORBIT',
        bumper: 'BUMPER',
        cockpit: 'COCKPIT',
        cinematic: 'CINEMATIC',
      };

      const update = (): void => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const mode = (state as any).__cinematicMode as string | undefined;
        root.textContent = mode ? (modeLabels[mode] || mode.toUpperCase()) : 'CHASE';
        // Pulse briefly on mode change.
        root.classList.toggle('active', mode !== 'chase');
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

function createHintWidget(
  _attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  return {
    id: 'race-hint',
    mount(layer: HTMLDivElement): WidgetHandle {
      ensureCss();
      const root = document.createElement('div');
      root.className = 'race-hint';
      root.textContent = 'WASD drive · Space drift · SHIFT nitro · C camera · R restart';
      layer.appendChild(root);
      return { root, update: () => {}, unmount: () => root.remove() };
    },
  };
}

function formatTimeTenths(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, '0')}`;
}

// ---- Registration --------------------------------------------------------

export function registerRacingHudFactories(): void {
  const factories: Record<string, HudWidgetFactory> = {
    'race-countdown': createCountdownWidget,
    'race-lap': createLapWidget,
    'race-speedo': createSpeedoWidget,
    'race-timer': createTimerWidget,
    'race-nitro': createNitroWidget,
    'race-minimap': createMinimapWidget,
    'race-camera-mode': createCameraModeWidget,
    'race-hint': createHintWidget,
  };
  for (const [type, factory] of Object.entries(factories)) {
    registerHudWidgetFactory(type, factory);
  }
}

// Kept for tests / external use.
export { PlayerVehicle };
