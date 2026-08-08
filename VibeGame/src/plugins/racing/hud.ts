import type { State, XMLValue } from '../../core';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../hud/screen-layer';
import { registerHudWidgetFactory } from '../hud/screen-layer';
import { injectWidgetCss, readAttr } from '../hud/widgets/shared';
import { PlayerVehicle, RaceTracker, Track, Vehicle } from './components';
import { getTrackSpline } from './data';
import { getRaceState } from './race-state';
import { getStandings } from './race-director';
import { defineQuery } from '../../core';
import { ChaseCamera } from './components';
import { getCameraModeName } from './chase-camera';

const playerQuery = defineQuery([PlayerVehicle, Vehicle]);
const trackQuery = defineQuery([Track]);
const camQuery = defineQuery([ChaseCamera]);

const CSS = `
.race-hud, .race-hud * { box-sizing: border-box; }
.race-hud {
  position: absolute; inset: 0; pointer-events: none; user-select: none;
  font-family: 'Segoe UI', system-ui, Arial, sans-serif; color: #fff;
  text-shadow: 0 2px 8px rgba(0,0,0,0.55);
}
.race-panel {
  position: absolute;
  background: linear-gradient(150deg, rgba(10,14,22,0.78), rgba(14,20,32,0.62));
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 10px;
  padding: 8px 14px;
  backdrop-filter: blur(6px);
}
.race-label {
  font-size: 0.62rem; font-weight: 700; letter-spacing: 0.16em;
  text-transform: uppercase; color: rgba(255,255,255,0.62);
}

/* Lap + position, top right */
.race-status { top: 16px; right: 16px; text-align: right; min-width: 132px; }
.race-status .lap { font-size: 1.7rem; font-weight: 800; line-height: 1.05; }
.race-status .pos { font-size: 1.05rem; font-weight: 700; color: #ffd166; margin-top: 2px; }

/* Timer, top centre */
.race-timer { top: 16px; left: 50%; transform: translateX(-50%); text-align: center; min-width: 150px; }
.race-timer .clock { font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.race-timer .laps { font-size: 0.72rem; color: rgba(255,255,255,0.7); font-variant-numeric: tabular-nums; }
.race-timer .laps b { color: #7fe7a1; font-weight: 700; }

/* Speed, bottom right */
.race-speed { bottom: 18px; right: 18px; text-align: right; min-width: 148px; }
.race-speed .value { font-size: 2.6rem; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.race-speed .unit { font-size: 0.66rem; letter-spacing: 0.2em; color: rgba(255,255,255,0.6); }
.race-speed .gear { position: absolute; left: 14px; bottom: 10px; font-size: 1.5rem; font-weight: 800; color: #7fd4ff; }
.race-speed-bar { margin-top: 6px; height: 4px; border-radius: 2px; background: rgba(255,255,255,0.16); overflow: hidden; }
.race-speed-bar > i { display: block; height: 100%; width: 0%; background: linear-gradient(90deg,#7fd4ff,#ffd166,#ff5d5d); }

/* Boost, bottom left */
.race-boost { bottom: 18px; left: 18px; min-width: 168px; }
.race-boost-bar { margin-top: 5px; height: 9px; border-radius: 5px; background: rgba(255,255,255,0.14); overflow: hidden; }
.race-boost-bar > i { display: block; height: 100%; width: 0%; background: linear-gradient(90deg,#38e8ff,#7f5dff); transition: width 0.08s linear; }
.race-boost.ready .race-label { color: #38e8ff; }

/* Minimap, top left */
.race-map { top: 16px; left: 16px; padding: 8px; }
.race-map canvas { display: block; }

/* Centre messages */
.race-centre {
  position: absolute; top: 26%; left: 50%; transform: translate(-50%,-50%);
  text-align: center; pointer-events: none;
}
.race-count { font-size: 8rem; font-weight: 900; line-height: 1; letter-spacing: -0.03em; }
.race-count.go { color: #7fe7a1; }
.race-sub { font-size: 1rem; font-weight: 700; letter-spacing: 0.22em; text-transform: uppercase; color: rgba(255,255,255,0.8); }
.race-wrong {
  position: absolute; top: 46%; left: 50%; transform: translate(-50%,-50%);
  font-size: 2rem; font-weight: 900; color: #ff5d5d; letter-spacing: 0.1em;
  animation: race-blink 0.55s steps(2, start) infinite;
}
@keyframes race-blink { to { opacity: 0.25; } }

/* Results */
.race-results {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  min-width: 420px; padding: 20px 26px; text-align: left;
}
.race-results h2 { margin: 0 0 12px; font-size: 1.5rem; letter-spacing: 0.14em; text-transform: uppercase; }
.race-results table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.race-results th { font-size: 0.6rem; letter-spacing: 0.14em; text-transform: uppercase; color: rgba(255,255,255,0.55); text-align: left; padding-bottom: 6px; }
.race-results td { padding: 4px 0; font-size: 0.95rem; }
.race-results tr.me td { color: #ffd166; font-weight: 800; }
.race-results .hint { margin-top: 14px; font-size: 0.78rem; color: rgba(255,255,255,0.65); letter-spacing: 0.08em; }

/* Bottom hint */
.race-hint {
  position: absolute; bottom: 6px; left: 50%; transform: translateX(-50%);
  font-size: 0.72rem; letter-spacing: 0.09em; color: rgba(255,255,255,0.5);
}
.race-cam { position: absolute; bottom: 62px; left: 50%; transform: translateX(-50%); font-size: 0.66rem; letter-spacing: 0.18em; color: rgba(255,255,255,0.45); text-transform: uppercase; }
.race-hidden { display: none; }
`;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '--:--.-';
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}`;
}

const MAP_SIZE = 148;

interface MapProjection {
  scale: number;
  offsetX: number;
  offsetZ: number;
}

/**
 * The whole racing HUD as one widget.
 *
 * A single `<HudWidget type="race-hud">` renders the lot — timer, lap and
 * position, speed and gear, boost, minimap, countdown, wrong-way warning and
 * the results table. Splitting it into eight widgets (as the old build did)
 * meant eight DOM subtrees, eight per-frame updates and an `target` attribute
 * on every one of them that the HUD recipe did not even accept.
 */
function createRaceHud(attributes: Record<string, XMLValue>): HudWidget {
  const id = readAttr(attributes, 'id') ?? 'race-hud';

  return {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      injectWidgetCss(CSS);
      const root = document.createElement('div');
      root.className = 'race-hud';
      root.innerHTML = `
        <div class="race-panel race-map">
          <canvas width="${MAP_SIZE}" height="${MAP_SIZE}"></canvas>
        </div>
        <div class="race-panel race-timer">
          <div class="race-label">Race</div>
          <div class="clock">0:00.0</div>
          <div class="laps">Last --:--.- · Best <b>--:--.-</b></div>
        </div>
        <div class="race-panel race-status">
          <div class="race-label">Lap</div>
          <div class="lap">1/3</div>
          <div class="pos">P1</div>
        </div>
        <div class="race-panel race-speed">
          <div class="gear">1</div>
          <div class="value">0</div>
          <div class="unit">km/h</div>
          <div class="race-speed-bar"><i></i></div>
        </div>
        <div class="race-panel race-boost">
          <div class="race-label">Nitro</div>
          <div class="race-boost-bar"><i></i></div>
        </div>
        <div class="race-centre race-hidden">
          <div class="race-count">3</div>
          <div class="race-sub"></div>
        </div>
        <div class="race-wrong race-hidden">WRONG WAY</div>
        <div class="race-panel race-results race-hidden">
          <h2>Results</h2>
          <table>
            <thead><tr><th>Pos</th><th>Driver</th><th>Time</th><th>Best lap</th></tr></thead>
            <tbody></tbody>
          </table>
          <div class="hint">Press R to race again</div>
        </div>
        <div class="race-cam">Chase</div>
        <div class="race-hint">WASD / arrows drive · Space handbrake · Shift nitro · C camera · R restart</div>
      `;
      layer.appendChild(root);

      const q = <T extends HTMLElement>(sel: string): T =>
        root.querySelector(sel) as T;
      const canvas = q<HTMLCanvasElement>('.race-map canvas');
      const ctx = canvas.getContext('2d');
      const clockEl = q('.race-timer .clock');
      const lapsEl = q('.race-timer .laps');
      const lapEl = q('.race-status .lap');
      const posEl = q('.race-status .pos');
      const speedEl = q('.race-speed .value');
      const gearEl = q('.race-speed .gear');
      const speedBar = q('.race-speed-bar > i');
      const boostPanel = q('.race-boost');
      const boostBar = q('.race-boost-bar > i');
      const centre = q('.race-centre');
      const countEl = q('.race-count');
      const subEl = q('.race-sub');
      const wrongEl = q('.race-wrong');
      const resultsEl = q('.race-results');
      const resultsBody = q('.race-results tbody');
      const camEl = q('.race-cam');

      let projection: MapProjection | null = null;
      let mapPath: Path2D | null = null;
      let mapTrack = -1;
      let lastResultsGeneration = -1;

      const buildMap = (trackEid: number): void => {
        const spline = getTrackSpline(trackEid);
        if (!spline || !ctx) return;
        const b = spline.bounds();
        const spanX = Math.max(1, b.maxX - b.minX);
        const spanZ = Math.max(1, b.maxZ - b.minZ);
        const pad = 12;
        const scale = (MAP_SIZE - pad * 2) / Math.max(spanX, spanZ);
        projection = {
          scale,
          offsetX:
            pad + (MAP_SIZE - pad * 2 - spanX * scale) / 2 - b.minX * scale,
          offsetZ:
            pad + (MAP_SIZE - pad * 2 - spanZ * scale) / 2 - b.minZ * scale,
        };
        const path = new Path2D();
        spline.forEachSample((i, x, _y, z) => {
          const px = x * scale + projection!.offsetX;
          const pz = z * scale + projection!.offsetZ;
          if (i === 0) path.moveTo(px, pz);
          else path.lineTo(px, pz);
        });
        path.closePath();
        mapPath = path;
        mapTrack = trackEid;
      };

      return {
        root,
        update(s: State) {
          const race = getRaceState();
          const player = playerQuery(s.world)[0];
          const trackEid = trackQuery(s.world)[0];
          if (trackEid !== undefined && trackEid !== mapTrack)
            buildMap(trackEid);

          // ---- Timer + laps ------------------------------------------------
          clockEl.textContent = formatTime(race.raceTime);
          if (player !== undefined) {
            const totalLaps =
              race.totalLaps || Track.totalLaps[trackEid ?? 0] || 3;
            const lap = Math.min(totalLaps, RaceTracker.lap[player] + 1);
            lapEl.textContent = `${lap}/${totalLaps}`;
            posEl.textContent = `P${RaceTracker.position[player] || 1}${
              race.entrants > 1 ? ` / ${race.entrants}` : ''
            }`;
            lapsEl.innerHTML = `Last ${formatTime(RaceTracker.lastLapTime[player])} · Best <b>${formatTime(
              RaceTracker.bestLapTime[player]
            )}</b>`;

            // ---- Speed + gear ---------------------------------------------
            const kmh = Math.abs(Vehicle.speed[player]) * 3.6;
            speedEl.textContent = String(Math.round(kmh));
            const gear = Vehicle.gear[player];
            gearEl.textContent = gear === 0 ? 'R' : String(gear);
            const maxKmh = (Vehicle.maxSpeed[player] || 40) * 3.6 * 1.25;
            speedBar.style.width = `${Math.min(100, (kmh / maxKmh) * 100)}%`;

            // ---- Boost ------------------------------------------------------
            const capacity = Vehicle.boostCapacity[player] || 0;
            if (capacity > 0) {
              boostPanel.classList.remove('race-hidden');
              const frac = Math.max(
                0,
                Math.min(1, Vehicle.boost[player] / capacity)
              );
              boostBar.style.width = `${frac * 100}%`;
              boostPanel.classList.toggle('ready', frac > 0.25);
            } else {
              boostPanel.classList.add('race-hidden');
            }

            // ---- Wrong way ---------------------------------------------------
            wrongEl.classList.toggle(
              'race-hidden',
              RaceTracker.wrongWay[player] !== 1
            );
          }

          // ---- Centre message ------------------------------------------------
          if (race.phase === 'countdown') {
            centre.classList.remove('race-hidden');
            const n = Math.ceil(race.countdown);
            countEl.textContent = n > 0 ? String(n) : 'GO!';
            countEl.classList.toggle('go', n <= 0);
            subEl.textContent = 'Get ready';
          } else if (race.phase === 'grid') {
            centre.classList.remove('race-hidden');
            countEl.textContent = '';
            countEl.classList.remove('go');
            subEl.textContent = 'On the grid';
          } else if (race.phase === 'racing' && race.raceTime < 1.1) {
            centre.classList.remove('race-hidden');
            countEl.textContent = 'GO!';
            countEl.classList.add('go');
            subEl.textContent = '';
          } else {
            centre.classList.add('race-hidden');
          }

          // ---- Results --------------------------------------------------------
          if (race.phase === 'finished') {
            resultsEl.classList.remove('race-hidden');
            if (lastResultsGeneration !== race.generation) {
              lastResultsGeneration = race.generation;
              resultsBody.innerHTML = race.results
                .map(
                  (r) => `
                    <tr class="${r.isPlayer ? 'me' : ''}">
                      <td>${r.position}</td>
                      <td>${r.name}</td>
                      <td>${r.totalTime >= 0 ? formatTime(r.totalTime) : `Lap ${r.laps + 1}`}</td>
                      <td>${formatTime(r.bestLap)}</td>
                    </tr>`
                )
                .join('');
            }
          } else {
            resultsEl.classList.add('race-hidden');
            lastResultsGeneration = -1;
          }

          // ---- Camera label ----------------------------------------------------
          const cam = camQuery(s.world)[0];
          if (cam !== undefined) {
            const name = getCameraModeName(cam);
            camEl.textContent = name;
          }

          // ---- Minimap ----------------------------------------------------------
          if (ctx && projection && mapPath) {
            ctx.clearRect(0, 0, MAP_SIZE, MAP_SIZE);
            ctx.lineWidth = 4;
            ctx.strokeStyle = 'rgba(255,255,255,0.18)';
            ctx.stroke(mapPath);
            ctx.lineWidth = 1.6;
            ctx.strokeStyle = 'rgba(160,220,255,0.75)';
            ctx.stroke(mapPath);

            const standings = getStandings();
            for (const eid of standings) {
              const spline =
                trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
              if (!spline) break;
              const pos = spline.positionAt(
                Vehicle.trackS[eid],
                Vehicle.trackLateral[eid]
              );
              const px = pos.x * projection.scale + projection.offsetX;
              const pz = pos.z * projection.scale + projection.offsetZ;
              const isPlayer = eid === player;
              ctx.beginPath();
              ctx.arc(px, pz, isPlayer ? 4 : 3, 0, Math.PI * 2);
              ctx.fillStyle = isPlayer ? '#ffd166' : 'rgba(255,120,120,0.85)';
              ctx.fill();
            }
          }
        },
        unmount() {
          root.remove();
        },
      };
    },
  };
}

let registered = false;

/** Register the racing HUD widget factory (idempotent). */
export function registerRacingHudFactories(): void {
  if (registered) return;
  registered = true;
  const factory: HudWidgetFactory = (attributes) => createRaceHud(attributes);
  registerHudWidgetFactory('race-hud', factory);
}
