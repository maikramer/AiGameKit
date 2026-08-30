import { defineQuery, getAllEntities } from '../../core';
import type { State } from '../../core';
import {
  copyProfilerSnapshot,
  downloadProfilerSnapshot,
  getProfilerMode,
  getProfilerSnapshot,
  isProfilerFrozen,
  resetProfiler,
  toggleProfilerFreeze,
  type ProfilerSnapshot,
  type ProfilerTimingStats,
} from '../../core/profiler';
import { getActiveGltfLoadCount } from '../../extras/gltf-bridge';
import { getAdaptiveQualityTier } from '../adaptive-quality';
import { getBvhStats } from '../bvh/utils';
import { getRenderingContext } from '../rendering/utils';
import { Terrain } from '../terrain/components';
import { getTerrainStats } from '../terrain/terrain-queries';
import { getInstancePoolStats } from '../gltf-xml/auto-instance';
import { getEntityScriptFrameStats } from '../entity-script';
import {
  armAudioDebug,
  clearAudioDebugLog,
  getAudioDebugSnapshot,
  type AudioDebugSnapshot,
} from '../audio/debug-log';
import { isBusMuted, setBusMuted, stopAllBankPlays } from '../audio/bank';
import { syncProfilerTabToUrl, type ProfilerTabId } from './url';
import { getProfilerExtras, type ProfilerExtra } from './extras';
import {
  bindWorldDebugState,
  DEFAULT_NEARBY_RADIUS,
  getWorldDebugSnapshot,
  renderWorldTab,
  type WorldDebugSnapshot,
} from './world-debug';
import {
  bindPhysicsDebugState,
  getPhysicsDebugSnapshot,
  renderPhysicsTab,
  type PhysicsDebugSnapshot,
} from './physics-debug';

const PANEL_ID = 'vibegame-profiler-panel';
const REFRESH_FRAMES = 10;
const HOT_MS = 1.0;
const BUDGET_60 = 1000 / 60;
const BUDGET_30 = 1000 / 30;

const terrainQuery = defineQuery([Terrain]);

type SortMode = 'avg' | 'p95' | 'last';

export interface ProfilerPanelRuntime {
  root: HTMLDivElement;
  filterInput: HTMLInputElement;
  groupSelect: HTMLSelectElement;
  sortSelect: HTMLSelectElement;
  summaryEl: HTMLPreElement;
  systemsEl: HTMLPreElement;
  scriptsEl: HTMLPreElement;
  countersEl: HTMLPreElement;
  statusEl: HTMLSpanElement;
  freezeBtn: HTMLButtonElement;
  systemsPane: HTMLDivElement;
  audioPane: HTMLDivElement;
  audioBodyEl: HTMLPreElement;
  worldPane: HTMLDivElement;
  worldBodyEl: HTMLPreElement;
  physicsPane: HTMLDivElement;
  physicsBodyEl: HTMLPreElement;
  extrasPane: HTMLDivElement;
  tabButtons: Record<ProfilerTabId, HTMLButtonElement>;
  tab: ProfilerTabId;
  visible: boolean;
  filter: string;
  groupFilter: string;
  sortMode: SortMode;
  /** Hide systems below this average (ms). 0 = show all. */
  minAvgMs: number;
  nearbyRadius: number;
  lastWorldSnap: WorldDebugSnapshot | null;
  lastPhysicsSnap: PhysicsDebugSnapshot | null;
  lastRefreshFrame: number;
  systemNames: string[];
  onTabChange?: (tab: ProfilerTabId) => void;
}

function bar(pct: number, width = 14): string {
  const filled = Math.max(0, Math.min(width, Math.round((pct / 100) * width)));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

function fmtMs(ms: number): string {
  return ms.toFixed(2).padStart(6, ' ');
}

function fmtPct(pct: number): string {
  return pct.toFixed(1).padStart(5, ' ');
}

function shortOrigin(origin: string): string {
  if (!origin || origin === 'unknown') return '';
  return origin
    .replace(/^plugins\//, '')
    .replace(/^app\/[^/]+\//, 'app/')
    .replace(/\.ts$/, '');
}

function renderSummary(snap: ProfilerSnapshot): string {
  const headroom60 = BUDGET_60 - snap.frameAvgMs;
  const headroom30 = BUDGET_30 - snap.frameAvgMs;
  const over60 = snap.frameAvgMs > BUDGET_60;
  const lines = [
    `FPS ${snap.fps.toFixed(1)}   frame avg ${fmtMs(snap.frameAvgMs)}  p95 ${fmtMs(snap.frameP95Ms)}  (min ${snap.frameMinMs.toFixed(1)} / max ${snap.frameMaxMs.toFixed(1)})`,
    `budget 60fps ${fmtMs(BUDGET_60)}  headroom ${fmtMs(headroom60)}${over60 ? '  OVER BUDGET' : ''}   |  30fps headroom ${fmtMs(headroom30)}`,
    `mode=${snap.mode}  window=${snap.windowFrames}  frames=${snap.frameCount}${snap.frozen ? '  FROZEN' : ''}`,
    '',
    'Groups:',
  ];
  for (const g of snap.groups) {
    if (g.avgMs <= 0.001 && g.group === 'custom') continue;
    lines.push(
      `  ${g.group.padEnd(12)} ${fmtMs(g.avgMs)} ms  ${fmtPct(g.pct)}%  ${bar(g.pct)}`
    );
  }
  const hot = [...snap.systems]
    .filter((s) => s.avgMs >= 0.05)
    .sort((a, b) => b.avgMs - a.avgMs)
    .slice(0, 5);
  if (hot.length > 0) {
    lines.push('', 'Top hot:');
    for (const s of hot) {
      const origin = shortOrigin(s.origin);
      lines.push(
        `  ${fmtMs(s.avgMs)} ${s.name}${origin ? `  ← ${origin}` : ''}`
      );
    }
  }
  return lines.join('\n');
}

function filteredRows(
  snap: ProfilerSnapshot,
  filter: string,
  groupFilter: string,
  sortMode: SortMode,
  minAvgMs = 0
): ProfilerTimingStats[] {
  const q = filter.trim().toLowerCase();
  // Entity scripts have their own panel section (`script/…`); keep other customs here.
  const customs = snap.customs
    .filter((c) => !c.name.startsWith('script/'))
    .map((c) => ({
      ...c,
      name: `custom/${c.name}`,
      origin: c.origin === 'unknown' ? 'custom-span' : c.origin,
    }));
  let rows = [...snap.systems, ...customs].filter((s) => {
    if (minAvgMs > 0 && s.avgMs < minAvgMs) return false;
    if (groupFilter && groupFilter !== 'all' && s.group !== groupFilter) {
      return false;
    }
    if (!q) return true;
    const origin = shortOrigin(s.origin).toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      s.group.toLowerCase().includes(q) ||
      origin.includes(q)
    );
  });
  rows = rows.sort((a, b) => {
    if (sortMode === 'p95') return b.p95Ms - a.p95Ms;
    if (sortMode === 'last') return b.lastMs - a.lastMs;
    return b.avgMs - a.avgMs;
  });
  return rows;
}

function rowsToTsv(rows: ProfilerTimingStats[]): string {
  const header = ['avgMs', 'p95Ms', 'lastMs', 'pct', 'group', 'name', 'origin'];
  const lines = [header.join('\t')];
  for (const s of rows) {
    lines.push(
      [
        s.avgMs.toFixed(3),
        s.p95Ms.toFixed(3),
        s.lastMs.toFixed(3),
        s.pct.toFixed(2),
        s.group,
        s.name,
        s.origin,
      ].join('\t')
    );
  }
  return lines.join('\n');
}

function renderSystems(
  snap: ProfilerSnapshot,
  filter: string,
  groupFilter: string,
  sortMode: SortMode,
  minAvgMs: number
): string {
  const rows = filteredRows(snap, filter, groupFilter, sortMode, minAvgMs);
  if (rows.length === 0) return '(no systems match filter)';

  const lines = [
    'avg    p95    last   %     group        name                         origin',
    '-'.repeat(96),
  ];
  for (const s of rows.slice(0, 45)) {
    const hot = s.avgMs >= HOT_MS || s.p95Ms >= HOT_MS * 1.5 ? '!' : ' ';
    const origin = shortOrigin(s.origin);
    const name = s.name.length > 28 ? `${s.name.slice(0, 27)}…` : s.name;
    lines.push(
      `${hot}${fmtMs(s.avgMs)} ${fmtMs(s.p95Ms)} ${fmtMs(s.lastMs)} ${fmtPct(s.pct)}%  ${s.group.padEnd(12)} ${name.padEnd(28)} ${origin}`
    );
  }
  if (rows.length > 45) {
    lines.push(`… +${rows.length - 45} more (refine filter)`);
  }
  lines.push('');
  lines.push(
    'click a row to copy "name · origin" · ! = hot (≥1ms avg or high p95)'
  );
  return lines.join('\n');
}

function renderEntityScripts(
  snap: ProfilerSnapshot,
  sortMode: SortMode,
  minAvgMs: number
): string {
  const entityCounts = new Map(
    getEntityScriptFrameStats().map((s) => [s.span, s.entities])
  );
  let rows = snap.customs.filter((c) => c.name.startsWith('script/'));
  if (minAvgMs > 0) {
    rows = rows.filter((c) => c.avgMs >= minAvgMs);
  }
  rows = rows.sort((a, b) => {
    if (sortMode === 'p95') return b.p95Ms - a.p95Ms;
    if (sortMode === 'last') return b.lastMs - a.lastMs;
    return b.avgMs - a.avgMs;
  });
  if (rows.length === 0) {
    return '(no entity scripts timed — open profiler while scripts run)';
  }

  const lines = [
    'avg    p95    last   ents  name                         origin',
    '-'.repeat(72),
  ];
  for (const s of rows.slice(0, 40)) {
    const hot = s.avgMs >= HOT_MS || s.p95Ms >= HOT_MS * 1.5 ? '!' : ' ';
    const ents = String(entityCounts.get(s.name) ?? '—').padStart(4, ' ');
    const name = s.name.length > 28 ? `${s.name.slice(0, 27)}…` : s.name;
    lines.push(
      `${hot}${fmtMs(s.avgMs)} ${fmtMs(s.p95Ms)} ${fmtMs(s.lastMs)} ${ents}  ${name.padEnd(28)} ${shortOrigin(s.origin)}`
    );
  }
  if (rows.length > 40) {
    lines.push(`… +${rows.length - 40} more`);
  }
  lines.push('');
  lines.push(
    'script/<file> = update · .fixed / .late / .collision = other phases'
  );
  return lines.join('\n');
}

function collectCounters(state: State): string {
  const lines: string[] = [];
  const ctx = getRenderingContext(state);
  const renderer = ctx.renderer;
  if (renderer?.info) {
    const info = renderer.info;
    lines.push('renderer.info');
    lines.push(
      `  calls=${info.render.calls}  tris=${info.render.triangles}  points=${info.render.points}  lines=${info.render.lines}`
    );
    lines.push(
      `  geoms=${info.memory.geometries}  textures=${info.memory.textures}  programs=${info.programs?.length ?? '?'}`
    );
  } else {
    lines.push('renderer.info: (no renderer)');
  }

  try {
    const tier = getAdaptiveQualityTier(state);
    lines.push(`adaptiveQuality.tier=${tier}`);
  } catch {
    lines.push('adaptiveQuality: n/a');
  }

  lines.push(`entities=${Array.from(getAllEntities(state.world)).length}`);
  lines.push(`systems=${state.systems.size}`);
  lines.push(`gltfLoads=${getActiveGltfLoadCount()}`);

  try {
    const pools = getInstancePoolStats(state);
    lines.push(
      `gltfInstances: pools=${pools.poolCount} slots=${pools.slotCount} pending=${pools.pendingCount}`
    );
  } catch {
    lines.push('gltfInstances: n/a');
  }

  try {
    const bvh = getBvhStats(state);
    lines.push(`bvh meshes=${bvh.meshCount} entities=${bvh.entityCount}`);
  } catch {
    lines.push('bvh: n/a');
  }

  const terrains = terrainQuery(state.world);
  if (terrains.length === 0) {
    lines.push('terrain: (none)');
  } else {
    for (const eid of terrains.slice(0, 3)) {
      const stats = getTerrainStats(state, eid);
      if (!stats) {
        lines.push(`terrain[${eid}]: not ready`);
        continue;
      }
      lines.push(
        `terrain[${eid}]: chunks=${stats.activeChunks} drawCalls=${stats.drawCalls} instances=${stats.totalInstances}`
      );
    }
  }

  return lines.join('\n');
}

function stylePanel(root: HTMLDivElement): void {
  const s = root.style;
  s.position = 'fixed';
  s.top = '8px';
  s.left = '8px';
  s.zIndex = '10001';
  s.width = 'min(720px, calc(100vw - 16px))';
  s.maxHeight = 'calc(100vh - 16px)';
  s.overflow = 'auto';
  s.fontFamily = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  s.fontSize = '11px';
  s.lineHeight = '1.35';
  s.color = '#e8e8e8';
  s.background = 'rgba(12, 14, 18, 0.92)';
  s.border = '1px solid rgba(255,255,255,0.12)';
  s.borderRadius = '8px';
  s.padding = '10px 12px';
  s.pointerEvents = 'auto';
  s.userSelect = 'text';
  s.display = 'none';
  s.boxShadow = '0 8px 28px rgba(0,0,0,0.45)';
}

function styleButton(btn: HTMLButtonElement): void {
  btn.style.fontFamily = 'inherit';
  btn.style.fontSize = '11px';
  btn.style.padding = '3px 8px';
  btn.style.marginRight = '6px';
  btn.style.marginBottom = '4px';
  btn.style.cursor = 'pointer';
  btn.style.background = 'rgba(255,255,255,0.08)';
  btn.style.color = '#eee';
  btn.style.border = '1px solid rgba(255,255,255,0.18)';
  btn.style.borderRadius = '4px';
}

function styleSelect(sel: HTMLSelectElement): void {
  sel.style.fontFamily = 'inherit';
  sel.style.fontSize = '11px';
  sel.style.padding = '3px 6px';
  sel.style.marginRight = '6px';
  sel.style.background = 'rgba(0,0,0,0.35)';
  sel.style.color = '#eee';
  sel.style.border = '1px solid rgba(255,255,255,0.15)';
  sel.style.borderRadius = '4px';
}

function shortKey(key: string, max = 48): string {
  if (key.length <= max) return key;
  return `${key.slice(0, 20)}…${key.slice(-24)}`;
}

function renderAudioTab(snap: AudioDebugSnapshot): string {
  const t0 = snap.events.length > 0 ? snap.events[0]!.t : 0;
  const gameplayEvents = snap.events.filter((e) => e.kind !== 'preload');
  const lines: string[] = [
    `ctx=${snap.ctxState}  armed=${snap.armed ? 'yes' : 'no'}  master=${snap.masterVolume.toFixed(2)}  plays/s=${snap.playsLastSec}`,
    `buses: ${
      snap.buses.length === 0
        ? '(none yet)'
        : snap.buses
            .map(
              (b) =>
                `${b.name}=${b.volume.toFixed(2)}${b.muted ? '(mute)' : ''}`
            )
            .join('  ')
    }`,
  ];
  if (snap.preloadCount > 0) {
    lines.push(
      `boot preload: ${snap.preloadCount} silent cache warm(s) — not gameplay plays`
    );
  }
  lines.push('', `Active (${snap.active.length}):`);
  if (snap.active.length === 0) {
    lines.push('  (none)');
  } else {
    for (const a of snap.active.slice(0, 40)) {
      const flags = [
        a.loop ? 'loop' : '',
        a.spatial ? 'spatial' : '',
        a.origin ? `origin=${a.origin}` : '',
        a.followEid != null && a.followEid !== a.originEid
          ? `follow=${a.followEid}`
          : '',
      ]
        .filter(Boolean)
        .join(' ');
      lines.push(
        `  ${shortKey(a.key).padEnd(40)} bus=${a.bus.padEnd(6)} vol=${a.volume.toFixed(2)} age=${(a.ageMs / 1000).toFixed(1)}s ${flags}`
      );
    }
  }
  lines.push('', `Top keys (gameplay plays):`);
  if (snap.topKeys.length === 0) {
    lines.push('  (none)');
  } else {
    for (const k of snap.topKeys) {
      lines.push(`  ${String(k.count).padStart(4)}×  ${shortKey(k.key)}`);
    }
  }
  lines.push('', `Top origins (who fired):`);
  if (snap.topOrigins.length === 0) {
    lines.push('  (none)');
  } else {
    for (const o of snap.topOrigins) {
      lines.push(
        `  ${String(o.count).padStart(4)}×  ${shortKey(o.origin, 40)}`
      );
    }
  }
  if (snap.unknownKeys.length > 0) {
    lines.push('', 'Unknown keys:');
    for (const k of snap.unknownKeys) lines.push(`  ! ${k}`);
  }
  // Default log hides silent boot preloads so loading screen noise does not
  // look like world SFX; armed stacks still keep preloads in the ring/JSON.
  lines.push(
    '',
    `Recent log (${gameplayEvents.length} gameplay / ${snap.events.length} total, oldest→newest):`
  );
  const tail = gameplayEvents.slice(-60);
  for (const e of tail) {
    const rel = ((e.t - t0) / 1000).toFixed(2).padStart(7);
    const meta = [
      e.origin ? `origin=${e.origin}` : '',
      e.source,
      e.bus ? `bus=${e.bus}` : '',
      e.volume != null ? `v=${e.volume.toFixed(2)}` : '',
      e.spatial ? 'spatial' : '',
      e.detail ?? '',
    ]
      .filter(Boolean)
      .join(' ');
    const caller = e.caller ? `  ← ${e.caller.split(' ← ')[0]}` : '';
    lines.push(
      `${rel}s  ${e.kind.padEnd(7)}  ${shortKey(e.key, 28).padEnd(28)}  ${meta}${caller}`
    );
  }
  return lines.join('\n');
}

function styleTabBtn(btn: HTMLButtonElement, active: boolean): void {
  styleButton(btn);
  btn.style.marginRight = '4px';
  btn.style.opacity = active ? '1' : '0.65';
  btn.style.borderColor = active
    ? 'rgba(120,200,255,0.55)'
    : 'rgba(255,255,255,0.15)';
  btn.style.background = active ? 'rgba(40,80,120,0.55)' : 'rgba(0,0,0,0.35)';
}

export function setProfilerPanelTab(
  runtime: ProfilerPanelRuntime,
  tab: ProfilerTabId,
  opts?: { syncUrl?: boolean; notify?: boolean }
): void {
  runtime.tab = tab;
  runtime.systemsPane.style.display = tab === 'systems' ? 'block' : 'none';
  runtime.audioPane.style.display = tab === 'audio' ? 'block' : 'none';
  runtime.worldPane.style.display = tab === 'world' ? 'block' : 'none';
  runtime.physicsPane.style.display = tab === 'physics' ? 'block' : 'none';
  runtime.extrasPane.style.display = tab === 'extras' ? 'block' : 'none';
  for (const id of [
    'systems',
    'audio',
    'world',
    'physics',
    'extras',
  ] as const) {
    styleTabBtn(runtime.tabButtons[id], id === tab);
  }
  if (tab === 'audio') armAudioDebug(true);
  if (opts?.syncUrl !== false) syncProfilerTabToUrl(tab);
  if (opts?.notify !== false) runtime.onTabChange?.(tab);
}

export function createProfilerPanel(): ProfilerPanelRuntime {
  const root = document.createElement('div');
  root.id = PANEL_ID;
  stylePanel(root);

  const tabBar = document.createElement('div');
  tabBar.style.display = 'flex';
  tabBar.style.alignItems = 'center';
  tabBar.style.gap = '2px';
  tabBar.style.marginBottom = '8px';

  const title = document.createElement('strong');
  title.textContent = 'VibeGame Profiler';
  title.style.marginRight = '10px';

  const systemsTabBtn = document.createElement('button');
  systemsTabBtn.type = 'button';
  systemsTabBtn.textContent = 'Systems';
  const audioTabBtn = document.createElement('button');
  audioTabBtn.type = 'button';
  audioTabBtn.textContent = 'Audio';
  const worldTabBtn = document.createElement('button');
  worldTabBtn.type = 'button';
  worldTabBtn.textContent = 'World';
  const physicsTabBtn = document.createElement('button');
  physicsTabBtn.type = 'button';
  physicsTabBtn.textContent = 'Physics';
  const extrasTabBtn = document.createElement('button');
  extrasTabBtn.type = 'button';
  extrasTabBtn.textContent = 'Extras';

  const statusEl = document.createElement('span');
  statusEl.style.opacity = '0.8';
  statusEl.style.marginLeft = '8px';

  tabBar.append(
    title,
    systemsTabBtn,
    audioTabBtn,
    worldTabBtn,
    physicsTabBtn,
    extrasTabBtn,
    statusEl
  );

  const systemsPane = document.createElement('div');
  const audioPane = document.createElement('div');
  audioPane.style.display = 'none';
  const worldPane = document.createElement('div');
  worldPane.style.display = 'none';
  const physicsPane = document.createElement('div');
  physicsPane.style.display = 'none';
  const extrasPane = document.createElement('div');
  extrasPane.style.display = 'none';

  const header = document.createElement('div');
  header.style.display = 'flex';
  header.style.flexWrap = 'wrap';
  header.style.alignItems = 'center';
  header.style.gap = '4px';
  header.style.marginBottom = '8px';

  const filterInput = document.createElement('input');
  filterInput.type = 'search';
  filterInput.placeholder = 'filter name/group/origin…';
  filterInput.style.flex = '1 1 160px';
  filterInput.style.minWidth = '140px';
  filterInput.style.fontFamily = 'inherit';
  filterInput.style.fontSize = '11px';
  filterInput.style.padding = '4px 6px';
  filterInput.style.background = 'rgba(0,0,0,0.35)';
  filterInput.style.color = '#eee';
  filterInput.style.border = '1px solid rgba(255,255,255,0.15)';
  filterInput.style.borderRadius = '4px';

  const groupSelect = document.createElement('select');
  styleSelect(groupSelect);
  for (const g of [
    'all',
    'setup',
    'fixed',
    'simulation',
    'late',
    'draw',
    'render',
    'custom',
  ] as const) {
    const opt = document.createElement('option');
    opt.value = g;
    opt.textContent = g === 'all' ? 'group: all' : `group: ${g}`;
    groupSelect.appendChild(opt);
  }

  const sortSelect = document.createElement('select');
  styleSelect(sortSelect);
  for (const [value, label] of [
    ['avg', 'sort: avg'],
    ['p95', 'sort: p95'],
    ['last', 'sort: last'],
  ] as const) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    sortSelect.appendChild(opt);
  }

  const noiseLabel = document.createElement('label');
  noiseLabel.style.display = 'inline-flex';
  noiseLabel.style.alignItems = 'center';
  noiseLabel.style.gap = '4px';
  noiseLabel.style.marginRight = '6px';
  noiseLabel.style.opacity = '0.9';
  const noiseCheck = document.createElement('input');
  noiseCheck.type = 'checkbox';
  noiseCheck.checked = true;
  noiseCheck.title = 'Hide systems under 0.05ms avg';
  const noiseText = document.createElement('span');
  noiseText.textContent = 'hide noise';
  noiseLabel.append(noiseCheck, noiseText);

  const freezeBtn = document.createElement('button');
  freezeBtn.type = 'button';
  freezeBtn.textContent = 'Freeze';
  styleButton(freezeBtn);

  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.textContent = 'Reset';
  styleButton(resetBtn);

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.textContent = 'Copy JSON';
  styleButton(copyBtn);

  const copyTsvBtn = document.createElement('button');
  copyTsvBtn.type = 'button';
  copyTsvBtn.textContent = 'Copy TSV';
  styleButton(copyTsvBtn);

  const dlBtn = document.createElement('button');
  dlBtn.type = 'button';
  dlBtn.textContent = 'Download';
  styleButton(dlBtn);

  header.append(
    filterInput,
    groupSelect,
    sortSelect,
    noiseLabel,
    freezeBtn,
    resetBtn,
    copyBtn,
    copyTsvBtn,
    dlBtn
  );

  const summaryEl = document.createElement('pre');
  summaryEl.style.margin = '0 0 10px';
  summaryEl.style.whiteSpace = 'pre-wrap';

  const systemsLabel = document.createElement('div');
  systemsLabel.textContent = 'Systems (with origin)';
  systemsLabel.style.opacity = '0.7';
  systemsLabel.style.marginBottom = '4px';

  const systemsEl = document.createElement('pre');
  systemsEl.style.margin = '0 0 10px';
  systemsEl.style.whiteSpace = 'pre';
  systemsEl.style.cursor = 'pointer';
  systemsEl.style.overflowX = 'auto';

  const scriptsLabel = document.createElement('div');
  scriptsLabel.textContent = 'Entity scripts (per file)';
  scriptsLabel.style.opacity = '0.7';
  scriptsLabel.style.marginBottom = '4px';

  const scriptsEl = document.createElement('pre');
  scriptsEl.style.margin = '0 0 10px';
  scriptsEl.style.whiteSpace = 'pre';
  scriptsEl.style.overflowX = 'auto';

  const countersLabel = document.createElement('div');
  countersLabel.textContent = 'Counters';
  countersLabel.style.opacity = '0.7';
  countersLabel.style.marginBottom = '4px';

  const countersEl = document.createElement('pre');
  countersEl.style.margin = '0';
  countersEl.style.whiteSpace = 'pre-wrap';

  systemsPane.append(
    header,
    summaryEl,
    scriptsLabel,
    scriptsEl,
    systemsLabel,
    systemsEl,
    countersLabel,
    countersEl
  );

  const audioToolbar = document.createElement('div');
  audioToolbar.style.display = 'flex';
  audioToolbar.style.flexWrap = 'wrap';
  audioToolbar.style.gap = '4px';
  audioToolbar.style.marginBottom = '8px';

  const clearLogBtn = document.createElement('button');
  clearLogBtn.type = 'button';
  clearLogBtn.textContent = 'Clear log';
  styleButton(clearLogBtn);

  const stopAllBtn = document.createElement('button');
  stopAllBtn.type = 'button';
  stopAllBtn.textContent = 'Stop all';
  styleButton(stopAllBtn);

  const copyAudioBtn = document.createElement('button');
  copyAudioBtn.type = 'button';
  copyAudioBtn.textContent = 'Copy JSON';
  styleButton(copyAudioBtn);

  const muteSfxBtn = document.createElement('button');
  muteSfxBtn.type = 'button';
  muteSfxBtn.textContent = 'Mute sfx';
  styleButton(muteSfxBtn);

  const muteMusicBtn = document.createElement('button');
  muteMusicBtn.type = 'button';
  muteMusicBtn.textContent = 'Mute music';
  styleButton(muteMusicBtn);

  audioToolbar.append(
    clearLogBtn,
    stopAllBtn,
    copyAudioBtn,
    muteSfxBtn,
    muteMusicBtn
  );

  const audioBodyEl = document.createElement('pre');
  audioBodyEl.style.margin = '0';
  audioBodyEl.style.whiteSpace = 'pre';
  audioBodyEl.style.overflowX = 'auto';
  audioBodyEl.style.maxHeight = '70vh';
  audioBodyEl.style.overflowY = 'auto';

  audioPane.append(audioToolbar, audioBodyEl);

  const worldToolbar = document.createElement('div');
  worldToolbar.style.display = 'flex';
  worldToolbar.style.flexWrap = 'wrap';
  worldToolbar.style.gap = '4px';
  worldToolbar.style.marginBottom = '8px';
  worldToolbar.style.alignItems = 'center';

  const radiusSelect = document.createElement('select');
  styleSelect(radiusSelect);
  for (const r of [15, 30, 50, 80, 120] as const) {
    const opt = document.createElement('option');
    opt.value = String(r);
    opt.textContent = `nearby: ${r}m`;
    if (r === DEFAULT_NEARBY_RADIUS) opt.selected = true;
    radiusSelect.appendChild(opt);
  }

  const copyWorldBtn = document.createElement('button');
  copyWorldBtn.type = 'button';
  copyWorldBtn.textContent = 'Copy JSON';
  styleButton(copyWorldBtn);

  worldToolbar.append(radiusSelect, copyWorldBtn);

  const worldBodyEl = document.createElement('pre');
  worldBodyEl.style.margin = '0';
  worldBodyEl.style.whiteSpace = 'pre';
  worldBodyEl.style.overflowX = 'auto';
  worldBodyEl.style.maxHeight = '70vh';
  worldBodyEl.style.overflowY = 'auto';

  worldPane.append(worldToolbar, worldBodyEl);

  const physicsToolbar = document.createElement('div');
  physicsToolbar.style.display = 'flex';
  physicsToolbar.style.flexWrap = 'wrap';
  physicsToolbar.style.gap = '4px';
  physicsToolbar.style.marginBottom = '8px';
  physicsToolbar.style.alignItems = 'center';

  const copyPhysicsBtn = document.createElement('button');
  copyPhysicsBtn.type = 'button';
  copyPhysicsBtn.textContent = 'Copy JSON';
  styleButton(copyPhysicsBtn);

  physicsToolbar.append(copyPhysicsBtn);

  const physicsBodyEl = document.createElement('pre');
  physicsBodyEl.style.margin = '0';
  physicsBodyEl.style.whiteSpace = 'pre';
  physicsBodyEl.style.overflowX = 'auto';
  physicsBodyEl.style.maxHeight = '70vh';
  physicsBodyEl.style.overflowY = 'auto';

  physicsPane.append(physicsToolbar, physicsBodyEl);

  const hint = document.createElement('div');
  hint.style.marginTop = '8px';
  hint.style.opacity = '0.55';
  hint.textContent =
    '[P] toggle  [Shift+P] sample↔deep  [Pause] freeze  · ?profiler=audio|world|physics  · ?profilerTab=systems|audio|world|physics|extras';

  root.append(
    tabBar,
    systemsPane,
    audioPane,
    worldPane,
    physicsPane,
    extrasPane,
    hint
  );

  const runtime: ProfilerPanelRuntime = {
    root,
    filterInput,
    groupSelect,
    sortSelect,
    summaryEl,
    systemsEl,
    scriptsEl,
    countersEl,
    statusEl,
    freezeBtn,
    systemsPane,
    audioPane,
    audioBodyEl,
    worldPane,
    worldBodyEl,
    physicsPane,
    physicsBodyEl,
    extrasPane,
    tabButtons: {
      systems: systemsTabBtn,
      audio: audioTabBtn,
      world: worldTabBtn,
      physics: physicsTabBtn,
      extras: extrasTabBtn,
    },
    tab: 'systems',
    visible: false,
    filter: '',
    groupFilter: 'all',
    sortMode: 'avg',
    minAvgMs: 0.05,
    nearbyRadius: DEFAULT_NEARBY_RADIUS,
    lastWorldSnap: null,
    lastPhysicsSnap: null,
    lastRefreshFrame: 0,
    systemNames: [],
  };

  styleTabBtn(systemsTabBtn, true);
  styleTabBtn(audioTabBtn, false);
  styleTabBtn(worldTabBtn, false);
  styleTabBtn(physicsTabBtn, false);
  styleTabBtn(extrasTabBtn, false);

  systemsTabBtn.addEventListener('click', () => {
    setProfilerPanelTab(runtime, 'systems');
  });
  audioTabBtn.addEventListener('click', () => {
    setProfilerPanelTab(runtime, 'audio');
  });
  worldTabBtn.addEventListener('click', () => {
    setProfilerPanelTab(runtime, 'world');
  });
  physicsTabBtn.addEventListener('click', () => {
    setProfilerPanelTab(runtime, 'physics');
  });
  extrasTabBtn.addEventListener('click', () => {
    setProfilerPanelTab(runtime, 'extras');
  });
  radiusSelect.addEventListener('change', () => {
    const n = Number(radiusSelect.value);
    if (Number.isFinite(n) && n > 0) runtime.nearbyRadius = n;
  });
  copyWorldBtn.addEventListener('click', () => {
    const snap = runtime.lastWorldSnap;
    if (!snap) {
      runtime.statusEl.textContent = ' no world snapshot yet';
      return;
    }
    void navigator.clipboard?.writeText(JSON.stringify(snap, null, 2));
    runtime.statusEl.textContent = ' copied world JSON';
  });
  copyPhysicsBtn.addEventListener('click', () => {
    const snap = runtime.lastPhysicsSnap;
    if (!snap) {
      runtime.statusEl.textContent = ' no physics snapshot yet';
      return;
    }
    void navigator.clipboard?.writeText(JSON.stringify(snap, null, 2));
    runtime.statusEl.textContent = ' copied physics JSON';
  });

  filterInput.addEventListener('input', () => {
    runtime.filter = filterInput.value;
  });
  groupSelect.addEventListener('change', () => {
    runtime.groupFilter = groupSelect.value;
  });
  sortSelect.addEventListener('change', () => {
    runtime.sortMode = sortSelect.value as SortMode;
  });
  noiseCheck.addEventListener('change', () => {
    runtime.minAvgMs = noiseCheck.checked ? 0.05 : 0;
  });
  freezeBtn.addEventListener('click', () => {
    toggleProfilerFreeze();
    freezeBtn.textContent = isProfilerFrozen() ? 'Unfreeze' : 'Freeze';
  });
  resetBtn.addEventListener('click', () => {
    resetProfiler();
    runtime.statusEl.textContent = ' reset';
  });
  copyBtn.addEventListener('click', () => {
    void copyProfilerSnapshot();
  });
  copyTsvBtn.addEventListener('click', () => {
    const snap = getProfilerSnapshot();
    const rows = filteredRows(
      snap,
      runtime.filter,
      runtime.groupFilter,
      runtime.sortMode,
      runtime.minAvgMs
    );
    void navigator.clipboard?.writeText(rowsToTsv(rows));
    runtime.statusEl.textContent = ` copied TSV (${rows.length})`;
  });
  dlBtn.addEventListener('click', () => {
    downloadProfilerSnapshot();
  });
  systemsEl.addEventListener('click', (ev) => {
    if (runtime.systemNames.length === 0) return;
    const rect = systemsEl.getBoundingClientRect();
    const lineHeight =
      parseFloat(getComputedStyle(systemsEl).lineHeight) || 14.85;
    const idx = Math.floor((ev.clientY - rect.top) / lineHeight);
    const dataIdx = idx - 2;
    if (dataIdx < 0 || dataIdx >= runtime.systemNames.length) return;
    const payload = runtime.systemNames[dataIdx]!;
    void navigator.clipboard?.writeText(payload);
    runtime.statusEl.textContent = ` copied ${payload}`;
  });

  clearLogBtn.addEventListener('click', () => {
    // Keep silent boot cache rows so loading noise stays labeled as preload.
    clearAudioDebugLog({ keepPreload: true });
    runtime.statusEl.textContent = ' audio log cleared (kept boot preload)';
    runtime.audioBodyEl.textContent = renderAudioTab(getAudioDebugSnapshot());
  });
  stopAllBtn.addEventListener('click', () => {
    stopAllBankPlays();
    runtime.statusEl.textContent = ' stopped all bank plays';
    runtime.audioBodyEl.textContent = renderAudioTab(getAudioDebugSnapshot());
  });
  copyAudioBtn.addEventListener('click', () => {
    void navigator.clipboard?.writeText(
      JSON.stringify(getAudioDebugSnapshot(), null, 2)
    );
    runtime.statusEl.textContent = ' copied audio JSON';
  });
  muteSfxBtn.addEventListener('click', () => {
    const next = !isBusMuted('sfx');
    setBusMuted('sfx', next);
    muteSfxBtn.textContent = next ? 'Unmute sfx' : 'Mute sfx';
  });
  muteMusicBtn.addEventListener('click', () => {
    const next = !isBusMuted('music');
    setBusMuted('music', next);
    muteMusicBtn.textContent = next ? 'Unmute music' : 'Mute music';
  });

  const parent = document.body ?? document.documentElement;
  parent.appendChild(root);
  return runtime;
}

export function setProfilerPanelVisible(
  runtime: ProfilerPanelRuntime,
  visible: boolean
): void {
  runtime.visible = visible;
  runtime.root.style.display = visible ? 'block' : 'none';
}

/** Rebuild the Extras tab buttons from the game's registered extras. */
function renderExtrasTab(state: State, runtime: ProfilerPanelRuntime): void {
  const pane = runtime.extrasPane;
  pane.textContent = '';
  const extras: ProfilerExtra[] = getProfilerExtras(state);
  const title = document.createElement('div');
  title.style.marginBottom = '4px';
  title.style.opacity = '0.7';
  title.textContent =
    'Game debug tools — registered by the game via registerProfilerExtra().';
  pane.append(title);
  if (extras.length === 0) {
    const empty = document.createElement('div');
    empty.style.opacity = '0.55';
    empty.style.marginTop = '6px';
    empty.textContent = '(none registered)';
    pane.append(empty);
    return;
  }
  const grid = document.createElement('div');
  grid.style.display = 'flex';
  grid.style.flexWrap = 'wrap';
  grid.style.gap = '6px';
  grid.style.marginTop = '6px';
  for (const extra of extras) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = extra.label;
    if (extra.description) btn.title = extra.description;
    styleButton(btn);
    btn.addEventListener('click', () => {
      extra.onClick();
      runtime.statusEl.textContent = ` ${extra.id} invoked`;
    });
    grid.append(btn);
  }
  pane.append(grid);
  const desc = document.createElement('div');
  desc.style.marginTop = '8px';
  desc.style.opacity = '0.6';
  desc.style.whiteSpace = 'pre';
  desc.textContent = extras
    .filter((e) => e.description)
    .map((e) => `${e.label} — ${e.description}`)
    .join('\n');
  if (desc.textContent) pane.append(desc);
}

export function refreshProfilerPanel(
  state: State,
  runtime: ProfilerPanelRuntime
): void {
  if (!runtime.visible) return;
  bindWorldDebugState(state);
  bindPhysicsDebugState(state);
  if (state.time.frameCount - runtime.lastRefreshFrame < REFRESH_FRAMES) {
    return;
  }
  runtime.lastRefreshFrame = state.time.frameCount;

  if (runtime.tab === 'physics') {
    const physicsSnap = getPhysicsDebugSnapshot(state);
    runtime.lastPhysicsSnap = physicsSnap;
    runtime.statusEl.textContent = ` ${getProfilerMode()} · physics${isProfilerFrozen() ? ' · frozen' : ''}${
      physicsSnap.available
        ? ` · bodies=${physicsSnap.bodies.total} · colliders=${physicsSnap.colliders.total} · cct=${physicsSnap.cct.total}`
        : ''
    }`;
    runtime.physicsBodyEl.textContent = renderPhysicsTab(physicsSnap);
    return;
  }

  if (runtime.tab === 'extras') {
    renderExtrasTab(state, runtime);
    runtime.statusEl.textContent = ` ${getProfilerMode()} · extras${isProfilerFrozen() ? ' · frozen' : ''} · ${getProfilerExtras(state).length} tool(s)`;
    return;
  }

  if (runtime.tab === 'audio') {
    const audioSnap = getAudioDebugSnapshot();
    runtime.statusEl.textContent = ` ${getProfilerMode()} · audio${isProfilerFrozen() ? ' · frozen' : ''} · active=${audioSnap.active.length} · log=${audioSnap.events.length}`;
    runtime.audioBodyEl.textContent = renderAudioTab(audioSnap);
    return;
  }

  if (runtime.tab === 'world') {
    const worldSnap = getWorldDebugSnapshot(state, {
      nearbyRadius: runtime.nearbyRadius,
    });
    runtime.lastWorldSnap = worldSnap;
    const p = worldSnap.player;
    runtime.statusEl.textContent = ` ${getProfilerMode()} · world${isProfilerFrozen() ? ' · frozen' : ''}${
      p
        ? ` · ${p.name} (${p.pos.x.toFixed(1)}, ${p.pos.y.toFixed(1)}, ${p.pos.z.toFixed(1)})`
        : ''
    } · near=${worldSnap.nearby.length}`;
    runtime.worldBodyEl.textContent = renderWorldTab(worldSnap);
    return;
  }

  const snap = getProfilerSnapshot();
  runtime.statusEl.textContent = ` ${getProfilerMode()}${isProfilerFrozen() ? ' · frozen' : ''}`;
  runtime.freezeBtn.textContent = isProfilerFrozen() ? 'Unfreeze' : 'Freeze';
  runtime.summaryEl.textContent = renderSummary(snap);
  const rows = filteredRows(
    snap,
    runtime.filter,
    runtime.groupFilter,
    runtime.sortMode,
    runtime.minAvgMs
  ).slice(0, 45);
  runtime.systemNames = rows.map((s) => {
    const origin = shortOrigin(s.origin);
    return origin ? `${s.name} · ${origin}` : s.name;
  });
  runtime.scriptsEl.textContent = renderEntityScripts(
    snap,
    runtime.sortMode,
    runtime.minAvgMs
  );
  runtime.systemsEl.textContent = renderSystems(
    snap,
    runtime.filter,
    runtime.groupFilter,
    runtime.sortMode,
    runtime.minAvgMs
  );
  runtime.countersEl.textContent = collectCounters(state);
}

export function destroyProfilerPanel(runtime: ProfilerPanelRuntime): void {
  if (runtime.root.parentNode) {
    runtime.root.parentNode.removeChild(runtime.root);
  }
}
