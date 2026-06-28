import {
  defineQuery,
  getAllEntities,
  getTotalActiveCoroutineCount,
  type Component,
} from '../../core';
import type { Plugin, State, System } from '../../core';
import { getTerrainContext } from '../terrain/utils';
import { getRenderingContext, getScene } from '../rendering/utils';
import { getPhysicsContext } from '../physics/systems';
import { getActiveGltfLoadCount } from '../../extras/gltf-bridge';
import {
  PostFxToggleSystem,
  postFxToggleRecipe,
  parsePostFxBindings,
  setPostFxBindings,
} from './postfx-toggle';
import {
  getDebugRegistry,
  getDebugRegistryHandle,
  type DebugRegistryHandle,
} from './registry';

export interface VibeGameDebugBridge {
  state: State;
  snapshot(options?: Record<string, unknown>): string;
  entities(): Array<{
    eid: number;
    name: string | null;
    components: Record<string, Record<string, number>>;
  }>;
  entity(name: string): {
    eid: number;
    name: string;
    components: Record<string, Record<string, number>>;
  } | null;
  component(eid: number, name: string): Record<string, number> | null;
  query(...componentNames: string[]): number[];
  componentNames(): string[];
  namedEntities(): Array<{ name: string; eid: number }>;
  step(dt?: number): void;
  terrain(): Record<string, unknown>;
  rendering(): unknown;
  physics(): unknown;
  debug: DebugRegistryHandle;
}

type TypedArrayField =
  Float32Array | Int32Array | Uint8Array | Uint16Array | Uint32Array;

function isTypedArrayField(v: unknown): v is TypedArrayField {
  return (
    v instanceof Float32Array ||
    v instanceof Int32Array ||
    v instanceof Uint8Array ||
    v instanceof Uint16Array ||
    v instanceof Uint32Array
  );
}

function extractComponentFields(
  state: State,
  eid: number,
  compName: string
): Record<string, number> | null {
  const comp = state.getComponent(compName);
  if (!comp || !state.hasComponent(eid, comp)) return null;
  const fields: Record<string, number> = {};
  for (const key in comp) {
    if (key.startsWith('_')) continue;
    const field = (comp as Record<string, unknown>)[key];
    if (isTypedArrayField(field)) {
      fields[key] = field[eid];
    }
  }
  return fields;
}

function extractAllComponents(
  state: State,
  eid: number
): Record<string, Record<string, number>> {
  const result: Record<string, Record<string, number>> = {};
  for (const compName of state.getComponentNames()) {
    const fields = extractComponentFields(state, eid, compName);
    if (fields) result[compName] = fields;
  }
  return result;
}

function createBridge(state: State): VibeGameDebugBridge {
  return {
    state,
    snapshot(options) {
      return state
        .snapshot(options as Parameters<State['snapshot']>[0])
        .format();
    },
    entities() {
      const snap = state.snapshot();
      return snap.entities.map((e) => ({
        eid: e.eid,
        name: e.name ?? null,
        components: e.components,
      }));
    },
    entity(name) {
      const eid = state.getEntityByName(name);
      if (eid === null) return null;
      const entityName = state.getEntityName(eid) ?? name;
      const components = extractAllComponents(state, eid);
      return { eid, name: entityName, components };
    },
    component(eid, name) {
      return extractComponentFields(state, eid, name);
    },
    query(...componentNames) {
      const components = componentNames
        .map((n) => state.getComponent(n))
        .filter((c): c is Component => c != null);
      if (components.length === 0) return [];
      const q = defineQuery(components);
      return Array.from(q(state.world));
    },
    componentNames() {
      return state.getComponentNames();
    },
    namedEntities() {
      const entries = Array.from(state.getNamedEntities().entries());
      return entries.map(([name, eid]) => ({ name, eid }));
    },
    step(dt) {
      state.step(dt);
    },
    terrain() {
      const ctx = getTerrainContext(state);
      const out: Record<string, unknown> = {};
      for (const [eid, data] of ctx) {
        out[String(eid)] = data;
      }
      return out;
    },
    rendering() {
      return getRenderingContext(state);
    },
    physics() {
      return getPhysicsContext(state);
    },
    debug: getDebugRegistryHandle(state),
  };
}

const OVERLAY_ID = 'vibegame-debug-overlay';
const FRAME_RING_SIZE = 60;
const FPS_EWMA_ALPHA = 0.1;
const OVERLAY_REFRESH_FRAMES = 10;

interface OverlayRuntime {
  el: HTMLDivElement;
  keyHandler: (event: KeyboardEvent) => void;
  fps: number;
  fpsReady: boolean;
  ring: Float32Array;
  ringIndex: number;
  ringFull: boolean;
  wireframeOn: boolean;
}

const overlayByState = new WeakMap<State, OverlayRuntime>();

function createOverlayEl(): HTMLDivElement {
  const el = document.createElement('div');
  el.id = OVERLAY_ID;
  el.setAttribute('aria-hidden', 'true');
  const s = el.style;
  s.position = 'fixed';
  s.top = '8px';
  s.left = '8px';
  s.zIndex = '9999';
  s.margin = '0';
  s.padding = '8px 10px';
  s.fontFamily = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  s.fontSize = '12px';
  s.lineHeight = '1.45';
  s.color = '#eaeaea';
  s.background = 'rgba(0, 0, 0, 0.66)';
  s.borderRadius = '6px';
  s.whiteSpace = 'pre';
  s.pointerEvents = 'none';
  s.userSelect = 'none';
  s.display = 'none';
  return el;
}

function toggleWireframe(state: State, runtime: OverlayRuntime): void {
  const scene = getScene(state);
  if (!scene) return;
  runtime.wireframeOn = !runtime.wireframeOn;
  const on = runtime.wireframeOn;
  scene.traverse((obj) => {
    const mesh = obj as unknown as {
      isMesh?: boolean;
      material?: unknown;
    };
    if (mesh.isMesh !== true || !mesh.material) return;
    const materials = Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material];
    for (const mat of materials) {
      const m = mat as { wireframe?: boolean } | null;
      if (m && typeof m.wireframe === 'boolean') m.wireframe = on;
    }
  });
}

function isTextInput(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA'
  );
}

function buildKeyHandler(state: State): (event: KeyboardEvent) => void {
  return (event: KeyboardEvent) => {
    if (isTextInput(event.target)) return;
    const runtime = overlayByState.get(state);
    if (!runtime) return;
    if (event.key === '?') {
      runtime.el.style.display =
        runtime.el.style.display === 'none' ? 'block' : 'none';
    } else if (event.key === '*') {
      toggleWireframe(state, runtime);
    }
  };
}

function ensureOverlayRuntime(state: State): OverlayRuntime | null {
  const existing = overlayByState.get(state);
  if (existing) return existing;
  if (typeof document === 'undefined' || typeof window === 'undefined') {
    return null;
  }
  const el = createOverlayEl();
  if (document.body) {
    document.body.appendChild(el);
  } else {
    document.documentElement.appendChild(el);
  }
  const runtime: OverlayRuntime = {
    el,
    keyHandler: buildKeyHandler(state),
    fps: 0,
    fpsReady: false,
    ring: new Float32Array(FRAME_RING_SIZE),
    ringIndex: 0,
    ringFull: false,
    wireframeOn: false,
  };
  window.addEventListener('keydown', runtime.keyHandler);
  overlayByState.set(state, runtime);
  return runtime;
}

function ringStats(runtime: OverlayRuntime): {
  min: number;
  avg: number;
  max: number;
} {
  const count = runtime.ringFull ? FRAME_RING_SIZE : runtime.ringIndex;
  if (count === 0) return { min: 0, avg: 0, max: 0 };
  let min = Infinity;
  let max = 0;
  let sum = 0;
  for (let i = 0; i < count; i++) {
    const v = runtime.ring[i];
    if (v < min) min = v;
    if (v > max) max = v;
    sum += v;
  }
  return { min, avg: sum / count, max };
}

function truncate(value: string, max: number): string {
  return value.length > max ? value.slice(0, max - 3) + '...' : value;
}

function formatDebugValue(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return truncate(value, 40);
  if (
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    typeof value === 'bigint'
  ) {
    return String(value);
  }
  if (typeof value === 'function') return 'ƒ';
  if (isTypedArrayField(value)) {
    return `${value.constructor.name}(${value.length})`;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    return value.length > 8
      ? `Array(${value.length})`
      : truncate(safeStringify(value), 40);
  }
  const ctor = (value as { constructor?: { name?: string } }).constructor;
  return ctor && ctor.name && ctor.name !== 'Object' ? ctor.name : '{obj}';
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function renderRegistrySection(state: State, visible: boolean): string {
  if (!visible) return '';
  const reg = getDebugRegistry(state);
  const actionNames = Array.from(reg.actions.keys()).sort();
  const varNames = Array.from(reg.vars.keys()).sort();
  if (actionNames.length === 0 && varNames.length === 0) return '';
  let out = '\n';
  if (varNames.length > 0) {
    out += 'Vars:\n';
    for (const name of varNames) {
      const entry = reg.vars.get(name);
      const value = entry ? formatDebugValue(entry.get()) : '';
      out += `  ${name} = ${value}\n`;
    }
  }
  if (actionNames.length > 0) {
    out += 'Actions:\n';
    for (const name of actionNames) {
      const entry = reg.actions.get(name);
      const desc = entry?.description ? ` — ${entry.description}` : '';
      out += `  ${name}${desc}\n`;
    }
    out += 'invoke: __VIBEGAME__.debug.callAction(name, ...args)';
  }
  return out;
}

export const DebugOverlaySystem: System = {
  group: 'draw',
  last: true,
  update(state: State): void {
    if (state.headless) return;
    const runtime = ensureOverlayRuntime(state);
    if (!runtime) return;

    const dt = state.time.unscaledDeltaTime;
    if (dt > 0) {
      const frameMs = dt * 1000;
      const instantFps = 1000 / frameMs;
      runtime.fps = runtime.fpsReady
        ? runtime.fps * (1 - FPS_EWMA_ALPHA) + instantFps * FPS_EWMA_ALPHA
        : instantFps;
      runtime.fpsReady = true;
      runtime.ring[runtime.ringIndex] = frameMs;
      runtime.ringIndex += 1;
      if (runtime.ringIndex >= FRAME_RING_SIZE) {
        runtime.ringIndex = 0;
        runtime.ringFull = true;
      }
    }

    if (state.time.frameCount % OVERLAY_REFRESH_FRAMES !== 0) return;

    const { min, avg, max } = ringStats(runtime);
    const entityCount = Array.from(getAllEntities(state.world)).length;
    const coroutineCount = getTotalActiveCoroutineCount(state);
    const gltfLoads = getActiveGltfLoadCount();
    const visible = runtime.el.style.display !== 'none';
    const registrySection = renderRegistrySection(state, visible);

    runtime.el.textContent =
      'VibeGame Debug\n' +
      `FPS:        ${runtime.fps.toFixed(1)}\n` +
      `Frame:      ${avg.toFixed(1)} ms  (min ${min.toFixed(1)} / max ${max.toFixed(1)})\n` +
      `Entities:   ${entityCount}\n` +
      `Systems:    ${state.systems.size}\n` +
      `Coroutines: ${coroutineCount}\n` +
      `GLTF loads: ${gltfLoads}\n` +
      `[?] toggle   [*] wireframe${runtime.wireframeOn ? ' ON' : ''}` +
      registrySection;
  },
  dispose(state: State): void {
    const runtime = overlayByState.get(state);
    if (!runtime) return;
    if (typeof window !== 'undefined') {
      window.removeEventListener('keydown', runtime.keyHandler);
    }
    if (runtime.el.parentNode) {
      runtime.el.parentNode.removeChild(runtime.el);
    }
    overlayByState.delete(state);
  },
};

/**
 * Installs `window.__VIBEGAME__`, a read-only introspection bridge over the live
 * ECS State for AI-driven / Playwright QA tooling, plus a visual debug overlay
 * (FPS, frame time, entity/system/coroutine counts, GLTF loads in flight). The
 * bridge also exposes a `debug` namespace for invoking actions/vars registered
 * via {@link registerDebugAction} / {@link registerDebugVar}.
 *
 * In-browser keys (active only when this plugin is registered and not headless):
 *   `?` (Shift+/) — toggle the stats overlay (hidden by default)
 *   `*` (Shift+8) — toggle wireframe rendering on every scene mesh
 *
 * Not part of DefaultPlugins — register it explicitly (e.g. in an example) so
 * it never ships in production.
 */
export const DebugPlugin: Plugin = {
  systems: [PostFxToggleSystem, DebugOverlaySystem],
  recipes: [postFxToggleRecipe],
  config: {
    parsers: {
      PostFxDebugToggle({ element, state }) {
        const raw = element.attributes['bindings'];
        if (typeof raw === 'string' && raw.trim() !== '') {
          setPostFxBindings(state, parsePostFxBindings(raw));
        }
      },
    },
  },
  initialize(state: State): void {
    if (typeof window === 'undefined') return;

    const w = window as unknown as Record<string, unknown>;
    if (w.__VIBEGAME__) return;

    w.__VIBEGAME__ = createBridge(state);
  },
};
