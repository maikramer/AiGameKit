import { getAllEntities, hasComponent as bitecsHas } from 'bitecs';
import { defineSystem } from '../profiler';
import { logger } from '../utils/logger';
import { Parent } from './components';
import { cleanupEntityCoroutines } from './coroutines';
import {
  createSnapshot,
  type EntitySnapshot,
  type SnapshotOptions,
  type WorldSnapshot,
} from './snapshot';
import type { State } from './state';
import type { System } from './types';
import { clearComponentFields, setComponentFields } from './utils';
import { dispatchWindowEvent } from '../utils/window-event';

export interface ChronoOptions {
  /** Seconds of history to keep (default 30). */
  seconds?: number;
  /** Snapshots per second (default 10). */
  hz?: number;
  /** Restrict recording to specific entity/component names (default: all). */
  filter?: SnapshotOptions;
}

export interface ChronoMark {
  label: string;
  frameIndex: number;
  elapsed: number;
}

export interface ChronoStatus {
  enabled: boolean;
  frames: number;
  capacity: number;
  hz: number;
  seconds: number;
  oldestElapsed: number | null;
  newestElapsed: number | null;
  marks: ChronoMark[];
}

const DEFAULT_SECONDS = 30;
const DEFAULT_HZ = 10;

interface ChronoState {
  enabled: boolean;
  interval: number;
  capacity: number;
  filter?: SnapshotOptions;
  frames: WorldSnapshot[];
  marks: ChronoMark[];
  lastRecordElapsed: number;
  restoring: boolean;
  seekListeners: Set<(state: State, snapshot: WorldSnapshot) => void>;
}

const chronoByState = new WeakMap<State, ChronoState>();

function getChrono(state: State): ChronoState {
  let chrono = chronoByState.get(state);
  if (!chrono) {
    chrono = {
      enabled: false,
      interval: 1 / DEFAULT_HZ,
      capacity: DEFAULT_SECONDS * DEFAULT_HZ,
      frames: [],
      marks: [],
      lastRecordElapsed: -Infinity,
      restoring: false,
      seekListeners: new Set(),
    };
    chronoByState.set(state, chrono);
  }
  return chrono;
}

/**
 * Records world snapshots into a ring buffer at a fixed rate.
 * Runs last in `late` so each frame is captured after all systems settle.
 */
export const ChronoRecorderSystem: System = defineSystem({
  name: 'ChronoRecorderSystem',
  group: 'late',
  last: true,
  update: (state) => {
    recordChronoFrame(state);
  },
});

/** Enable time-travel recording. Idempotent; re-calling replaces the config. */
export function enableChrono(state: State, options?: ChronoOptions): void {
  const chrono = getChrono(state);
  const seconds = options?.seconds ?? DEFAULT_SECONDS;
  const hz = options?.hz ?? DEFAULT_HZ;
  chrono.interval = 1 / hz;
  chrono.capacity = Math.max(1, Math.ceil(seconds * hz));
  chrono.filter = options?.filter;
  chrono.frames = [];
  chrono.marks = [];
  chrono.lastRecordElapsed = -Infinity;
  chrono.enabled = true;
  if (!state.systems.has(ChronoRecorderSystem)) {
    state.registerSystem(ChronoRecorderSystem);
  }
}

export function disableChrono(state: State): void {
  const chrono = getChrono(state);
  chrono.enabled = false;
  chrono.frames = [];
  chrono.marks = [];
}

export function isChronoEnabled(state: State): boolean {
  return chronoByState.get(state)?.enabled ?? false;
}

/** Record one frame if the interval has elapsed. Called by the recorder system. */
export function recordChronoFrame(state: State): void {
  const chrono = chronoByState.get(state);
  if (!chrono?.enabled || chrono.restoring) return;

  const elapsed = state.time.elapsed;
  if (elapsed - chrono.lastRecordElapsed < chrono.interval) return;

  chrono.frames.push(createSnapshot(state, chrono.filter));
  if (chrono.frames.length > chrono.capacity) {
    chrono.frames.shift();
  }
  chrono.lastRecordElapsed = elapsed;
}

/** Recorded frames, oldest first. Treat as read-only. */
export function getChronoFrames(state: State): readonly WorldSnapshot[] {
  return chronoByState.get(state)?.frames ?? [];
}

/** Bookmark the current newest frame for later seeking. */
export function chronoMark(state: State, label?: string): ChronoMark | null {
  const chrono = chronoByState.get(state);
  if (!chrono?.enabled || chrono.frames.length === 0) return null;
  const index = chrono.frames.length - 1;
  const mark: ChronoMark = {
    label: label ?? `mark-${chrono.marks.length}`,
    frameIndex: index,
    elapsed: chrono.frames[index]!.elapsed,
  };
  chrono.marks.push(mark);
  return mark;
}

export function getChronoMarks(state: State): ChronoMark[] {
  return [...(chronoByState.get(state)?.marks ?? [])];
}

export function getChronoStatus(state: State): ChronoStatus {
  const chrono = chronoByState.get(state);
  const frames = chrono?.frames ?? [];
  return {
    enabled: chrono?.enabled ?? false,
    frames: frames.length,
    capacity: chrono?.capacity ?? 0,
    hz: chrono ? 1 / chrono.interval : 0,
    seconds: chrono ? chrono.capacity * chrono.interval : 0,
    oldestElapsed: frames.length > 0 ? frames[0]!.elapsed : null,
    newestElapsed:
      frames.length > 0 ? frames[frames.length - 1]!.elapsed : null,
    marks: [...(chrono?.marks ?? [])],
  };
}

/** Restore the frame at `frameIndex` (0 = oldest recorded). */
export function chronoSeek(state: State, frameIndex: number): boolean {
  const chrono = chronoByState.get(state);
  if (!chrono?.enabled) return false;
  const snapshot = chrono.frames[frameIndex];
  if (!snapshot) return false;

  chrono.restoring = true;
  try {
    restoreChronoFrame(state, snapshot);
  } finally {
    chrono.restoring = false;
  }

  // Drop frames recorded after the seek target — the timeline now continues
  // from here, so stale future frames would be misleading.
  chrono.frames.length = Math.min(chrono.frames.length, frameIndex + 1);
  chrono.lastRecordElapsed = snapshot.elapsed;

  for (const listener of chrono.seekListeners) {
    try {
      listener(state, snapshot);
    } catch (err) {
      logger.error('[VibeGame] chrono seek listener threw:', err);
    }
  }
  if (typeof window !== 'undefined') {
    dispatchWindowEvent('vibegame:chrono-seek', {
      elapsed: snapshot.elapsed,
      frame: frameIndex,
    });
  }
  return true;
}

/** Rewind `seconds` back from the newest frame (clamped to available history). */
export function chronoRewind(state: State, seconds: number): boolean {
  const chrono = chronoByState.get(state);
  if (!chrono?.enabled || chrono.frames.length === 0) return false;

  const newest = chrono.frames[chrono.frames.length - 1]!;
  const target = newest.elapsed - Math.max(0, seconds);

  let index = chrono.frames.length - 1;
  while (index > 0 && chrono.frames[index]!.elapsed > target) index--;
  return chronoSeek(state, index);
}

/**
 * Register a callback fired after every successful seek. Systems that mirror
 * ECS state in external runtimes (physics bodies, audio, network) use this to
 * resync with the restored world.
 */
export function onChronoSeek(
  state: State,
  callback: (state: State, snapshot: WorldSnapshot) => void
): void {
  getChrono(state).seekListeners.add(callback);
}

export function offChronoSeek(
  state: State,
  callback: (state: State, snapshot: WorldSnapshot) => void
): void {
  chronoByState.get(state)?.seekListeners.delete(callback);
}

function componentNamesOn(state: State, eid: number): Set<string> {
  const names = new Set<string>();
  for (const name of state.getComponentNames()) {
    const component = state.getComponent(name);
    if (component && bitecsHas(state.world, eid, component)) {
      names.add(name);
    }
  }
  return names;
}

function signatureMatches(state: State, eid: number, entity: EntitySnapshot) {
  const current = componentNamesOn(state, eid);
  const recorded = new Set(Object.keys(entity.components));
  if (current.size !== recorded.size) return false;
  for (const name of recorded) {
    if (!current.has(name)) return false;
  }
  return true;
}

/**
 * Restore a snapshot in place: matched entities keep their eid (so external
 * maps like three.js cameras and physics handles stay valid), divergent
 * entities are destroyed, missing ones are recreated.
 */
function restoreChronoFrame(state: State, snapshot: WorldSnapshot): void {
  // recorded eid -> eid in the restored world
  const eidMap = new Map<number, number>();
  const matched = new Set<EntitySnapshot>();

  for (const entity of snapshot.entities) {
    if (!entity.name) continue;
    const current = state.getEntityByName(entity.name);
    if (current !== null && state.exists(current)) {
      eidMap.set(entity.eid, current);
      matched.add(entity);
    }
  }
  for (const entity of snapshot.entities) {
    if (matched.has(entity)) continue;
    if (
      state.exists(entity.eid) &&
      signatureMatches(state, entity.eid, entity)
    ) {
      eidMap.set(entity.eid, entity.eid);
      matched.add(entity);
    }
  }

  const targets = new Set(eidMap.values());
  const destroySet = new Set<number>();
  for (const eid of Array.from(getAllEntities(state.world))) {
    if (!targets.has(eid)) destroySet.add(eid);
  }

  // Detach targeted children from doomed parents first: destroyEntity cascades
  // to descendants, and targets must survive the sweep.
  for (const eid of targets) {
    if (destroySet.has(eid)) continue;
    if (!bitecsHas(state.world, eid, Parent)) continue;
    if (destroySet.has(Parent.entity[eid])) {
      state.removeComponent(eid, Parent);
    }
  }

  for (const eid of destroySet) {
    cleanupEntityCoroutines(state, eid);
  }
  for (const eid of destroySet) {
    if (state.exists(eid)) state.destroyEntity(eid);
  }

  for (const entity of matched) {
    const eid = eidMap.get(entity.eid)!;
    if (!state.exists(eid)) continue;
    const currentNames = componentNamesOn(state, eid);
    for (const [name, fields] of Object.entries(entity.components)) {
      if (name === 'parent') continue;
      const component = state.getComponent(name);
      if (!component) continue;
      if (currentNames.has(name)) {
        clearComponentFields(component, eid);
        setComponentFields(component, eid, fields);
      } else {
        state.addComponent(eid, component, fields);
      }
    }
    for (const name of currentNames) {
      if (name === 'parent') continue;
      if (!(name in entity.components)) {
        const component = state.getComponent(name);
        if (component) state.removeComponent(eid, component);
      }
    }
    if ('parent' in entity.components) continue;
    if (currentNames.has('parent')) {
      state.removeComponent(eid, Parent);
    }
  }

  for (const entity of snapshot.entities) {
    if (matched.has(entity)) continue;
    const eid = state.createEntity();
    eidMap.set(entity.eid, eid);
    if (entity.name) state.setEntityName(entity.name, eid);
    for (const [name, fields] of Object.entries(entity.components)) {
      if (name === 'parent') continue;
      const component = state.getComponent(name);
      if (component) state.addComponent(eid, component, fields);
    }
  }

  // Parent links are rewritten after all entities exist so references resolve
  // through the recorded-eid mapping instead of stale ids.
  for (const entity of snapshot.entities) {
    const parentFields = entity.components['parent'];
    if (!parentFields || typeof parentFields.entity !== 'number') continue;
    const childEid = eidMap.get(entity.eid);
    const parentEid = eidMap.get(parentFields.entity);
    if (childEid === undefined || parentEid === undefined) continue;
    if (!state.exists(childEid) || !state.exists(parentEid)) continue;
    state.addComponent(childEid, Parent, { entity: parentEid });
  }

  const clock = snapshot.realtimeSinceStartup ?? snapshot.elapsed;
  state.time.realtimeSinceStartup = clock;
  state.time.elapsed = clock;
  state.time.fixedTime = snapshot.fixedTime ?? 0;
}
