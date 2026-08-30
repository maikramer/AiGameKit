import type { State, XMLValue } from '../../core';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../hud/screen-layer';
import { registerHudWidgetFactory } from '../hud/screen-layer';
import { injectWidgetCss, readAttr } from '../hud/widgets/shared';
import {
  HeldItem,
  ItemKind,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from './components';
import { getTrackSpline } from './data';
import { ITEM_META } from './items';
import { drainRacingBanners } from './fx-events';
import { getRaceState, beginRaceFromQualifying } from './race-state';
import {
  getStandings,
  getVehicleName,
  intervalToNeighbour,
} from './race-director';
import {
  ghostDeltaAt,
  ghostProgressU,
  completedSector,
  sectorBoundaryU,
  sampleGhostAtTime,
} from './ghost';
import { defineQuery } from '../../core';
import { ChaseCamera } from './components';
import { getCameraModeName } from './chase-camera';
import { isKeyDown } from '../input';
import { driftTier, evaluateLaunch, DRIFT_TIER2_S } from './vehicle-control';

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

/* Lap + position, top right (hidden in time-trial — the TT badge takes over) */
.race-status { top: 16px; right: 16px; text-align: right; min-width: 132px; }
.race-status .lap { font-size: 1.7rem; font-weight: 800; line-height: 1.05; }
.race-status .pos { font-size: 1.05rem; font-weight: 700; color: #ffd166; margin-top: 2px; }
.race-gap { margin-top: 6px; font-variant-numeric: tabular-nums; font-size: 0.78rem; font-weight: 700; line-height: 1.35; }
.race-gap .ahead { color: #ffd166; }
.race-gap .behind { color: rgba(255,255,255,0.72); }
.race-gap.hidden { display: none; }
.race-order {
  margin-top: 8px; padding-top: 6px;
  border-top: 1px solid rgba(255,255,255,0.12);
  font-variant-numeric: tabular-nums; font-size: 0.7rem; font-weight: 700;
  line-height: 1.45; text-align: left;
}
.race-order .me { color: #ffd166; }
.race-order.hidden { display: none; }

/* Timer, top centre */
.race-timer { top: 16px; left: 50%; transform: translateX(-50%); text-align: center; min-width: 150px; }
.race-timer .clock { font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.race-timer .laps { font-size: 0.72rem; color: rgba(255,255,255,0.7); font-variant-numeric: tabular-nums; }
.race-timer .laps b { color: #7fe7a1; font-weight: 700; }
.race-delta {
  margin-top: 4px; font-size: 1.15rem; font-weight: 800;
  font-variant-numeric: tabular-nums; letter-spacing: 0.04em;
}
.race-delta.ahead { color: #7fe7a1; }
.race-delta.behind { color: #ff6b6b; }
.race-delta.hidden { display: none; }
.race-split {
  margin-top: 2px; font-size: 0.82rem; font-weight: 800;
  font-variant-numeric: tabular-nums; letter-spacing: 0.06em;
  opacity: 0; transition: opacity 0.12s linear;
}
.race-split.show { opacity: 1; }
.race-split.ahead { color: #7fe7a1; }
.race-split.behind { color: #ff6b6b; }

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
.race-draft {
  margin-top: 6px; font-size: 0.68rem; font-weight: 800;
  letter-spacing: 0.22em; color: #38e8ff; text-transform: uppercase;
  opacity: 0; transition: opacity 0.12s linear;
}
.race-draft.show { opacity: 1; }

/* Drift-charge meter, under the nitro bar: blue at tier 1, orange at tier 2 */
.race-drift-meter { margin-top: 7px; }
.race-drift-meter .race-label { color: #38d1ff; }
.race-drift-bar { margin-top: 4px; height: 5px; border-radius: 3px; background: rgba(255,255,255,0.12); overflow: hidden; }
.race-drift-bar > i { display: block; height: 100%; width: 0%; background: #38d1ff; }
.race-drift-meter.t2 .race-drift-bar > i { background: #ffb347; }
.race-drift-meter.t2 .race-label { color: #ffb347; }

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

/* Launch rev meter: revs fill the bar, the tick marks the rocket window,
   red means the engine has been pinned too long (wheelspin incoming). */
.race-launch { margin-top: 14px; width: 230px; }
.race-launch .race-label { font-size: 0.58rem; }
.race-launch-bar { position: relative; margin-top: 4px; height: 8px; border-radius: 4px; background: rgba(255,255,255,0.16); overflow: hidden; }
.race-launch-bar::after {
  content: ''; position: absolute; top: 0; bottom: 0; left: 72%;
  width: 2px; background: rgba(127,231,161,0.95);
}
.race-launch-bar > i { display: block; height: 100%; width: 0%; background: rgba(255,255,255,0.75); }
.race-launch.sweet > .race-launch-bar > i { background: #7fe7a1; }
.race-launch.over > .race-launch-bar > i { background: #ff5d5d; }
.race-launch.over .race-label { color: #ff5d5d; }
.race-wrong {
  position: absolute; top: 46%; left: 50%; transform: translate(-50%,-50%);
  font-size: 2rem; font-weight: 900; color: #ff5d5d; letter-spacing: 0.1em;
  animation: race-blink 0.55s steps(2, start) infinite;
}
@keyframes race-blink { to { opacity: 0.25; } }

/* Results */
.race-results {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  min-width: 480px; padding: 20px 26px; text-align: left;
}
.race-results h2 { margin: 0 0 12px; font-size: 1.5rem; letter-spacing: 0.14em; text-transform: uppercase; }
.race-results table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.race-results th { font-size: 0.6rem; letter-spacing: 0.14em; text-transform: uppercase; color: rgba(255,255,255,0.55); text-align: left; padding-bottom: 6px; }
.race-results td { padding: 4px 0; font-size: 0.95rem; }
.race-results tr.me td { color: #ffd166; font-weight: 800; }
.race-results .hint { margin-top: 14px; font-size: 0.78rem; color: rgba(255,255,255,0.65); letter-spacing: 0.08em; }

/* Camera label: under the minimap so the bottom row stays clean */
.race-cam {
  position: absolute; top: 180px; left: 16px;
  font-size: 0.66rem; letter-spacing: 0.18em; color: rgba(255,255,255,0.45);
  text-transform: uppercase;
}
.race-hidden { display: none; }

/* Item slot: bottom centre, above the hint line */
.race-item {
  position: absolute; bottom: 44px; left: 50%; transform: translateX(-50%);
}
.race-item .slot {
  position: relative; width: 64px; height: 64px; border-radius: 12px;
  background: linear-gradient(150deg, rgba(10,14,22,0.8), rgba(14,20,32,0.65));
  border: 1px solid rgba(255,255,255,0.18);
  display: flex; align-items: center; justify-content: center;
  font-size: 1.9rem;
}
.race-item .key { position: absolute; top: 3px; left: 6px; font-size: 0.55rem; letter-spacing: 0.08em; color: rgba(255,255,255,0.55); }
.race-item .slot.rolling { animation: race-roulette 0.14s steps(2) infinite; }
.race-item .slot.ready { border-color: rgba(255,255,255,0.6); box-shadow: 0 0 14px rgba(255,255,255,0.2); }
.race-item .slot.turbo { color: #7fe7a1; border-color: rgba(127,231,161,0.5); }
.race-item .slot.fireball { color: #ff8a5c; border-color: rgba(255,138,92,0.5); }
.race-item .slot.oil { color: #b9a7e8; border-color: rgba(185,167,232,0.5); }
.race-item .slot.shield { color: #ffe066; border-color: rgba(255,224,102,0.5); }
@keyframes race-roulette { 0% { transform: scale(1); } 50% { transform: scale(1.08) rotate(-2deg); } 100% { transform: scale(1); } }

/* Bottom hint */
.race-hint {
  position: absolute; bottom: 6px; left: 50%; transform: translateX(-50%);
  font-size: 0.72rem; letter-spacing: 0.09em; color: rgba(255,255,255,0.5);
  white-space: nowrap;
}

/* Banner shouts: item rolls, stunts, spin-outs */
.race-pickup {
  position: absolute; top: 22%; left: 50%; transform: translate(-50%,-50%);
  font-size: 1.6rem; font-weight: 900; letter-spacing: 0.14em;
  text-transform: uppercase; text-shadow: 0 0 22px currentColor;
  opacity: 0; transition: opacity 0.12s ease-out; pointer-events: none;
}
.race-pickup.show { opacity: 1; }
.race-pickup.item { color: #ffd166; }
.race-pickup.trick { color: #7fe7a1; }
.race-pickup.spin { color: #ff5d5d; }

/* Respawn flash banner */
.race-respawn {
  position: absolute; top: 30%; left: 50%; transform: translate(-50%,-50%);
  font-size: 2.4rem; font-weight: 900; color: #ff5dff; letter-spacing: 0.22em;
  text-transform: uppercase; text-shadow: 0 0 24px rgba(255,93,255,0.8);
  opacity: 0; transition: opacity 0.15s ease-out; pointer-events: none;
}
.race-respawn.show { opacity: 1; }

.race-final {
  position: absolute; top: 28%; left: 50%; transform: translate(-50%,-50%);
  font-size: 3.2rem; font-weight: 900; color: #ffd166; letter-spacing: 0.18em;
  text-transform: uppercase; text-shadow: 0 0 28px rgba(255,209,102,0.85);
  opacity: 0; transition: opacity 0.18s ease-out; pointer-events: none;
}
.race-final.show { opacity: 1; }

.race-condition {
  margin-top: 4px; font-size: 0.62rem; font-weight: 800;
  letter-spacing: 0.18em; text-transform: uppercase;
  color: rgba(255,255,255,0.55);
}
.race-condition.wet { color: #7fd4ff; }
.race-condition.night { color: #c9b6ff; }
.race-condition.storm { color: #ff8a5c; }

/* Time-trial badge: replaces the lap/position panel, same corner */
.race-tt { position: absolute; top: 16px; right: 16px; text-align: right; min-width: 132px; }
.race-tt .title { font-size: 0.62rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase; color: #38e8ff; }
.race-tt .best { font-size: 1rem; font-weight: 700; color: #7fe7a1; margin-top: 2px; }
.race-tt .pb { font-size: 0.72rem; color: rgba(255,255,255,0.6); }
`;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '--:--.-';
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}`;
}

function formatGap(total: number, winnerTotal: number): string {
  if (total < 0 || winnerTotal < 0) return '';
  const d = total - winnerTotal;
  if (d < 0.05) return '—';
  return `+${d.toFixed(1)}`;
}

function formatDelta(delta: number): string {
  const ahead = delta < -0.04;
  const behind = delta > 0.04;
  return `${ahead ? '−' : behind ? '+' : '±'}${Math.abs(delta).toFixed(2)}`;
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
          <div class="race-delta hidden">--.-</div>
          <div class="race-split"></div>
          <div class="race-condition"></div>
        </div>
        <div class="race-panel race-status">
          <div class="race-label">Lap</div>
          <div class="lap">1/3</div>
          <div class="pos">P1</div>
          <div class="race-gap hidden"></div>
          <div class="race-order hidden"></div>
        </div>
        <div class="race-panel race-tt race-hidden">
          <div class="title">Time Trial</div>
          <div class="best">--:--.-</div>
          <div class="pb">PB --:--.-</div>
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
          <div class="race-draft">Slipstream</div>
          <div class="race-drift-meter race-hidden">
            <div class="race-label">Drift</div>
            <div class="race-drift-bar"><i></i></div>
          </div>
        </div>
        <div class="race-item">
          <div class="slot"><span class="key">1</span><span class="icon"></span></div>
        </div>
        <div class="race-pickup"></div>
        <div class="race-respawn">Respawn</div>
        <div class="race-final">Final lap</div>
        <div class="race-centre race-hidden">
          <div class="race-count">3</div>
          <div class="race-sub"></div>
          <div class="race-launch race-hidden">
            <div class="race-label">Hold W to rev</div>
            <div class="race-launch-bar"><i></i></div>
          </div>
        </div>
        <div class="race-wrong race-hidden">WRONG WAY</div>
        <div class="race-panel race-results race-hidden">
          <h2>Results</h2>
          <table>
            <thead><tr><th>Pos</th><th>Driver</th><th>Time</th><th>Gap</th><th>Best lap</th></tr></thead>
            <tbody></tbody>
          </table>
          <div class="hint">Press R to race again</div>
        </div>
        <div class="race-cam">Chase</div>
        <div class="race-hint">WASD drive · Space hold a drift, release for turbo · Shift nitro · 1 item · V look back · C camera · R restart</div>
      `;
      layer.appendChild(root);

      const q = <T extends HTMLElement>(sel: string): T =>
        root.querySelector(sel) as T;
      const canvas = q<HTMLCanvasElement>('.race-map canvas');
      const ctx = canvas.getContext('2d');
      const clockEl = q('.race-timer .clock');
      const timerLabelEl = q('.race-timer .race-label');
      const conditionEl = q('.race-condition');
      const lapsEl = q('.race-timer .laps');
      const lapEl = q('.race-status .lap');
      const posEl = q('.race-status .pos');
      const gapEl = q('.race-gap');
      const orderEl = q('.race-order');
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
      const resultsTitleEl = q('.race-results h2');
      const resultsHintEl = q('.race-results .hint');
      const resultsBody = q('.race-results tbody');
      const camEl = q('.race-cam');
      const itemSlotEl = q('.race-item .slot') as HTMLElement;
      const itemIconEl = q('.race-item .icon') as HTMLElement;
      const ttEl = q('.race-tt');
      const ttBestEl = q('.race-tt .best');
      const ttPbEl = q('.race-tt .pb');
      const respawnEl = q('.race-respawn');
      const finalEl = q('.race-final');
      const pickupEl = q('.race-pickup');
      const statusEl = q('.race-status');
      const deltaEl = q('.race-delta');
      const splitEl = q('.race-split');
      const draftEl = q('.race-draft');
      const driftMeterEl = q('.race-drift-meter');
      const driftBarEl = q('.race-drift-bar > i');
      const launchEl = q('.race-launch');
      const launchBarEl = q('.race-launch-bar > i');
      let pickupFlashUntil = 0;
      let finalLapUntil = 0;
      let finalLapGeneration = -1;
      let splitPrevU = 0;
      let splitUntil = 0;
      let splitText = '';
      let splitAhead = false;
      let splitGeneration = -1;
      let splitLap = -1;

      let projection: MapProjection | null = null;
      let mapPath: Path2D | null = null;
      let mapTrack = -1;
      let lastResultsGeneration = -1;
      let enterArmed = true;

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
          const playerLapTime =
            player !== undefined
              ? Math.max(0, race.raceTime - RaceTracker.lapStartTime[player])
              : race.raceTime;
          const showLapClock =
            (race.phase === 'racing' || race.phase === 'countdown') &&
            player !== undefined;
          clockEl.textContent = formatTime(
            showLapClock ? playerLapTime : race.raceTime
          );
          timerLabelEl.textContent =
            race.session === 'qualifying'
              ? 'Qualifying'
              : showLapClock
                ? 'Lap'
                : 'Race';
          const cond = race.condition;
          conditionEl.textContent =
            cond === 'dry' ? '' : cond.replace('-', ' ');
          conditionEl.className = `race-condition ${cond === 'dry' ? '' : cond}`;
          if (player !== undefined) {
            const totalLaps =
              race.totalLaps || Track.totalLaps[trackEid ?? 0] || 3;
            const lap = Math.min(totalLaps, RaceTracker.lap[player] + 1);
            lapEl.textContent = `${lap}/${totalLaps}`;
            posEl.textContent = `P${RaceTracker.position[player] || 1}${
              race.entrants > 1 ? ` / ${race.entrants}` : ''
            }`;
            lapsEl.innerHTML = `Race ${formatTime(race.raceTime)} · Last ${formatTime(
              RaceTracker.lastLapTime[player]
            )} · Best <b>${formatTime(RaceTracker.bestLapTime[player])}</b>`;

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

            draftEl.classList.toggle('show', Vehicle.draft[player] > 0.28);

            // ---- Drift charge meter ------------------------------------------
            // Visible the moment a slide starts charging; the fill is the
            // charge, the colour is the tier the driver will be paid.
            const charge = Vehicle.driftCharge[player] ?? 0;
            const meterActive = charge > 0.02;
            driftMeterEl.classList.toggle('race-hidden', !meterActive);
            if (meterActive) {
              const tier = driftTier(charge);
              driftBarEl.style.width = `${Math.min(100, (charge / DRIFT_TIER2_S) * 100)}%`;
              driftMeterEl.classList.toggle('t2', tier === 2);
            }

            const lapTime = Math.max(
              0,
              race.raceTime - RaceTracker.lapStartTime[player]
            );
            const u = ghostProgressU(player);
            const delta = ghostDeltaAt(u, lapTime);
            if (delta === null || race.phase !== 'racing') {
              deltaEl.classList.add('hidden');
            } else {
              deltaEl.classList.remove('hidden');
              const ahead = delta < -0.04;
              const behind = delta > 0.04;
              deltaEl.classList.toggle('ahead', ahead);
              deltaEl.classList.toggle('behind', behind);
              deltaEl.textContent = formatDelta(delta);
            }

            const lapLen = Track.length[trackEid ?? 0] || 0;
            const lapIdx = RaceTracker.lap[player];
            if (race.generation !== splitGeneration || lapIdx !== splitLap) {
              splitGeneration = race.generation;
              splitLap = lapIdx;
              splitPrevU = u;
              splitUntil = 0;
              splitText = '';
            }
            const crossed = completedSector(splitPrevU, u, lapLen);
            if (crossed !== null && race.phase === 'racing') {
              const splitDelta = ghostDeltaAt(
                sectorBoundaryU(crossed, lapLen),
                lapTime
              );
              if (splitDelta !== null) {
                splitAhead = splitDelta < -0.04;
                splitText = `S${crossed} ${formatDelta(splitDelta)}`;
                splitUntil = performance.now() + 2200;
              }
            }
            splitPrevU = u;
            const showSplit =
              splitText !== '' && performance.now() < splitUntil;
            splitEl.textContent = splitText;
            splitEl.classList.toggle('show', showSplit);
            splitEl.classList.toggle('ahead', showSplit && splitAhead);
            splitEl.classList.toggle('behind', showSplit && !splitAhead);

            // ---- Wrong way ---------------------------------------------------
            wrongEl.classList.toggle(
              'race-hidden',
              RaceTracker.wrongWay[player] !== 1
            );

            // ---- Item slot + roulette ----------------------------------------
            const rolling = (HeldItem.rouletteTimer[player] ?? 0) > 0;
            const held = HeldItem.item[player] ?? ItemKind.None;
            itemSlotEl.classList.toggle('rolling', rolling);
            itemSlotEl.classList.toggle('ready', held !== ItemKind.None);
            if (rolling) {
              // Cycle the icons while the roulette spins.
              const spin = Math.floor(performance.now() / 90) % 4;
              itemIconEl.textContent = ITEM_META[spin + 1]!.icon;
              itemSlotEl.className = 'slot rolling';
            } else {
              itemIconEl.textContent =
                held === ItemKind.None ? '' : ITEM_META[held]!.icon;
              itemSlotEl.className = `slot ${
                held === ItemKind.None ? '' : ITEM_META[held]!.cls
              }`;
            }

            // ---- Time-trial badge --------------------------------------------
            // The TT badge replaces the lap/position panel in the same corner;
            // only one of the two is ever visible.
            const isTimeTrial = (Track.checkpointCount[trackEid ?? 0] || 0) > 0;
            ttEl.classList.toggle('race-hidden', !isTimeTrial);
            statusEl.classList.toggle('race-hidden', isTimeTrial);
            if (isTimeTrial) {
              ttBestEl.textContent = `Current ${formatTime(race.raceTime)}`;
              ttPbEl.textContent = `PB ${formatTime(RaceTracker.bestLapTime[player])}`;
            }

            // ---- Interval to the cars immediately ahead / behind -------------
            const showGaps =
              !isTimeTrial && race.phase === 'racing' && race.entrants > 1;
            if (showGaps) {
              const ahead = intervalToNeighbour(player, 'ahead');
              const behind = intervalToNeighbour(player, 'behind');
              const lines: string[] = [];
              if (ahead) {
                lines.push(
                  `<div class="ahead">▲ ${ahead.name} +${ahead.seconds.toFixed(1)}</div>`
                );
              }
              if (behind) {
                lines.push(
                  `<div class="behind">▼ ${behind.name} −${behind.seconds.toFixed(1)}</div>`
                );
              }
              gapEl.innerHTML = lines.join('');
              gapEl.classList.toggle('hidden', lines.length === 0);
            } else {
              gapEl.classList.add('hidden');
            }

            const liveOrder = getStandings();
            const showOrder =
              !isTimeTrial &&
              liveOrder.length > 1 &&
              race.phase !== 'idle' &&
              race.phase !== 'finished';
            if (showOrder) {
              orderEl.innerHTML = liveOrder
                .map((eid, i) => {
                  return `<div class="${eid === player ? 'me' : ''}">P${i + 1} ${getVehicleName(eid)}</div>`;
                })
                .join('');
              orderEl.classList.remove('hidden');
            } else {
              orderEl.classList.add('hidden');
            }

            // ---- Final lap banner --------------------------------------------
            const onFinalLap =
              race.phase === 'racing' &&
              RaceTracker.lap[player] === totalLaps - 1 &&
              RaceTracker.finished[player] !== 1;
            if (race.generation !== finalLapGeneration) {
              finalLapGeneration = race.generation;
              finalLapUntil = 0;
            }
            if (onFinalLap) {
              if (finalLapUntil === 0) finalLapUntil = performance.now() + 2500;
            } else {
              finalLapUntil = 0;
            }
            finalEl.classList.toggle(
              'show',
              onFinalLap && performance.now() < finalLapUntil
            );

            // ---- Respawn flash -----------------------------------------------
            respawnEl.classList.toggle(
              'show',
              RaceTracker.respawnFlash[player] === 1
            );

            // ---- Banner shouts (items, stunts, spin-outs) ---------------------
            const banners = drainRacingBanners();
            const mine = banners.find((b) => b.eid === player);
            if (mine) {
              pickupEl.textContent = mine.text;
              pickupEl.className = `race-pickup ${mine.cls}`;
              pickupFlashUntil = performance.now() + 1500;
            }
            pickupEl.classList.toggle(
              'show',
              performance.now() < pickupFlashUntil
            );
          }

          // ---- Centre message ------------------------------------------------
          if (race.phase === 'countdown') {
            centre.classList.remove('race-hidden');
            const n = Math.ceil(race.countdown);
            countEl.textContent = n > 0 ? String(n) : 'GO!';
            countEl.classList.toggle('go', n <= 0);
            subEl.textContent = 'Get ready';
            // Launch meter: revs fill, green in the rocket window, red when
            // the engine has been pinned long enough to wheelspin.
            if (player !== undefined) {
              const rev = Vehicle.launchRev[player] ?? 0;
              const quality = evaluateLaunch(
                rev,
                Vehicle.launchHold[player] ?? 0
              );
              launchEl.classList.remove('race-hidden');
              launchBarEl.style.width = `${rev * 100}%`;
              launchEl.classList.toggle('sweet', quality === 'rocket');
              launchEl.classList.toggle('over', quality === 'wheelspin');
            }
          } else if (race.phase === 'grid') {
            centre.classList.remove('race-hidden');
            countEl.textContent = '';
            countEl.classList.remove('go');
            subEl.textContent = 'On the grid';
            launchEl.classList.add('race-hidden');
          } else if (race.phase === 'racing' && race.raceTime < 1.1) {
            centre.classList.remove('race-hidden');
            countEl.textContent = 'GO!';
            countEl.classList.add('go');
            subEl.textContent = '';
            launchEl.classList.add('race-hidden');
          } else {
            centre.classList.add('race-hidden');
          }

          // ---- Results --------------------------------------------------------
          if (race.phase === 'finished') {
            resultsEl.classList.remove('race-hidden');
            const qualiDone = race.session === 'qualifying';
            resultsTitleEl.textContent = qualiDone ? 'Qualifying' : 'Results';
            resultsHintEl.textContent = qualiDone
              ? 'Press Enter to start the race · R to requalify'
              : 'Press R to race again';
            if (lastResultsGeneration !== race.generation) {
              lastResultsGeneration = race.generation;
              const winnerTime =
                race.results.find((r) => r.totalTime >= 0)?.totalTime ?? -1;
              resultsBody.innerHTML = race.results
                .map(
                  (r) => `
                    <tr class="${r.isPlayer ? 'me' : ''}">
                      <td>${r.position}</td>
                      <td>${r.name}</td>
                      <td>${r.totalTime >= 0 ? formatTime(r.totalTime) : `Lap ${r.laps + 1}`}</td>
                      <td>${formatGap(r.totalTime, winnerTime)}</td>
                      <td>${formatTime(r.bestLap)}</td>
                    </tr>`
                )
                .join('');
            }
            const enter = isKeyDown('Enter') || isKeyDown('NumpadEnter');
            if (qualiDone && enter && enterArmed) {
              enterArmed = false;
              beginRaceFromQualifying();
            } else if (!enter) {
              enterArmed = true;
            }
          } else {
            resultsEl.classList.add('race-hidden');
            lastResultsGeneration = -1;
            enterArmed = true;
          }

          // ---- Camera label ----------------------------------------------------
          const cam = camQuery(s.world)[0];
          if (cam !== undefined) {
            camEl.textContent =
              isKeyDown('KeyV') && race.phase !== 'finished'
                ? 'Look back'
                : getCameraModeName(cam);
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

            const splineForMap =
              trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
            if (splineForMap) {
              const sf = splineForMap.positionAt(0, 0);
              const sx = sf.x * projection.scale + projection.offsetX;
              const sz = sf.z * projection.scale + projection.offsetZ;
              ctx.fillStyle = '#f4f4f4';
              ctx.fillRect(sx - 3.5, sz - 5, 7, 10);
              ctx.fillStyle = '#1a1a1a';
              ctx.fillRect(sx - 3.5, sz - 5, 3.5, 5);
              ctx.fillRect(sx, sz, 3.5, 5);
            }

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

            if (player !== undefined && race.phase === 'racing') {
              const ghostLapTime = Math.max(
                0,
                race.raceTime - RaceTracker.lapStartTime[player]
              );
              const ghostSample = sampleGhostAtTime(ghostLapTime);
              const spline =
                trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
              if (ghostSample && spline) {
                const pos = spline.positionAt(
                  ghostSample.s,
                  ghostSample.lateral
                );
                const gx = pos.x * projection.scale + projection.offsetX;
                const gz = pos.z * projection.scale + projection.offsetZ;
                ctx.beginPath();
                ctx.arc(gx, gz, 3.5, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(56,232,255,0.85)';
                ctx.fill();
              }
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
