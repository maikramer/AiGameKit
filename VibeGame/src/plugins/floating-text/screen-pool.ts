import type { State } from '../../core';
import { getHudScreenLayer } from '../hud/screen-layer';
import { FloatingText } from './components';
import { getFloatingTextString } from './utils';

const FLOAT_SCREEN_CLASS = 'vibe-float-screen';
const POOL_SPAN_STYLE =
  'position:absolute;left:0;top:0;pointer-events:none;' +
  'font-family:system-ui,Segoe UI,sans-serif;font-weight:800;white-space:nowrap;' +
  'text-shadow:0 0 4px rgba(0,0,0,0.9),0 2px 3px rgba(0,0,0,0.85);' +
  '-webkit-text-stroke:0.6px rgba(0,0,0,0.5);' +
  'will-change:transform,opacity;opacity:0;transform:translate(-9999px,-9999px);';

export interface ScreenPoolOptions {
  size?: number;
}

interface PoolEntry {
  el: HTMLSpanElement;
  entity: number;
  active: boolean;
}

const stateToPool = new WeakMap<State, ScreenFloatPool>();

function hexByte(n: number): string {
  return Math.max(0, Math.min(255, Math.round(n * 255)))
    .toString(16)
    .padStart(2, '0');
}

function rgbToHex(r: number, g: number, b: number): string {
  return `#${hexByte(r)}${hexByte(g)}${hexByte(b)}`;
}

export class ScreenFloatPool {
  private readonly entries: PoolEntry[] = [];
  private readonly entityToEntry = new Map<number, PoolEntry>();
  private cursor = 0;
  readonly size: number;

  constructor(state: State, options: ScreenPoolOptions = {}) {
    this.size = Math.max(1, options.size ?? 32);
    const layer = getHudScreenLayer(state);
    for (let i = 0; i < this.size; i++) {
      const el = document.createElement('span');
      el.className = FLOAT_SCREEN_CLASS;
      el.style.cssText = POOL_SPAN_STYLE;
      layer.appendChild(el);
      this.entries.push({ el, entity: -1, active: false });
    }
  }

  acquire(state: State, entity: number): PoolEntry {
    const startIdx = this.cursor;
    do {
      const entry = this.entries[this.cursor];
      this.cursor = (this.cursor + 1) % this.size;
      if (!entry.active) {
        return this.assign(entry, entity);
      }
    } while (this.cursor !== startIdx);

    const evicted = this.entries[this.cursor];
    if (evicted.entity >= 0) {
      this.entityToEntry.delete(evicted.entity);
      if (state.exists(evicted.entity)) {
        state.destroyEntity(evicted.entity);
      }
    }
    return this.assign(evicted, entity);
  }

  private assign(entry: PoolEntry, entity: number): PoolEntry {
    if (entry.entity >= 0) {
      this.entityToEntry.delete(entry.entity);
    }
    entry.entity = entity;
    entry.active = true;
    this.entityToEntry.set(entity, entry);
    return entry;
  }

  release(entity: number): void {
    const entry = this.entityToEntry.get(entity);
    if (!entry) return;
    entry.active = false;
    entry.el.style.opacity = '0';
    entry.el.style.transform = 'translate(-9999px,-9999px)';
    this.entityToEntry.delete(entity);
  }

  getEntry(entity: number): PoolEntry | undefined {
    return this.entityToEntry.get(entity);
  }

  updateEntity(entity: number, t: number): void {
    const entry = this.entityToEntry.get(entity);
    if (!entry) return;
    const el = entry.el;
    const duration = FloatingText.duration[entity] || 1.4;
    if (t >= duration) {
      this.release(entity);
      return;
    }
    const rel = t / duration;
    const rise = FloatingText.riseSpeed[entity] * t;
    const drift = FloatingText.driftX[entity] * rel;
    const scale =
      rel < 0.14
        ? 0.55 + (rel / 0.14) * 0.55
        : 1.1 - Math.min(0.12, (rel - 0.14) * 0.2);
    const alpha = rel > 0.62 ? 1 - (rel - 0.62) / 0.38 : 1;
    const x = FloatingText.screenX[entity] + drift;
    const y = FloatingText.screenY[entity] - rise;
    el.style.transform = `translate(-50%,-50%) translate(${x}px,${y}px) scale(${scale})`;
    el.style.opacity = String(alpha);
  }

  applySpawn(state: State, entity: number): void {
    const entry = this.acquire(state, entity);
    const el = entry.el;
    const text = getFloatingTextString(state, entity) ?? '';
    const crit = FloatingText.crit[entity] === 1;
    const baseSize = FloatingText.fontSizePx[entity] || (crit ? 26 : 20);
    const r = FloatingText.colorR[entity];
    const g = FloatingText.colorG[entity];
    const b = FloatingText.colorB[entity];
    el.textContent = text;
    el.style.fontSize = `${baseSize}px`;
    el.style.color = crit ? '#ff6b3d' : rgbToHex(r, g, b);
    el.style.opacity = '0';
    el.style.transform = 'translate(-50%,-50%) translate(-9999px,-9999px)';
  }

  dispose(): void {
    for (const entry of this.entries) {
      entry.el.remove();
    }
    this.entries.length = 0;
    this.entityToEntry.clear();
  }
}

export function getScreenFloatPool(state: State, options?: ScreenPoolOptions): ScreenFloatPool {
  let pool = stateToPool.get(state);
  if (!pool) {
    pool = new ScreenFloatPool(state, options);
    stateToPool.set(state, pool);
  }
  return pool;
}

export function disposeScreenFloatPool(state: State): void {
  const pool = stateToPool.get(state);
  if (!pool) return;
  pool.dispose();
  stateToPool.delete(state);
}

export function getFloatingScreenPoolSize(state: State): number {
  return stateToPool.get(state)?.size ?? 0;
}
