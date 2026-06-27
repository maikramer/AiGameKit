import {
  defineQuery,
  type ParsedElement,
  type State,
  type XMLValue,
} from '../../../core';
import { Transform } from '../../transforms';
import { PlayerController } from '../../player';
import { FACTION_TAG_NAMES, FactionComponent, Health } from '../../combat';
import { NavMeshAgent } from '../../navmesh';
import { ResourceNode } from '../../rpg-resource-node';
import { getResourceNodeKind } from '../../rpg-resource-node/utils';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidget,
  registerHudWidgetFactory,
} from '../screen-layer';

/**
 * Minimap categories. Each maps to a default color and a dot radius (px).
 * The player is always rendered as a directional arrow at the centre.
 */
export type MinimapCategory =
  | 'player'
  | 'enemy'
  | 'boss'
  | 'merchant'
  | 'wood'
  | 'stone'
  | 'neutral';

export const MINIMAP_CATEGORY_VALUES: readonly MinimapCategory[] = [
  'player',
  'enemy',
  'boss',
  'merchant',
  'wood',
  'stone',
  'neutral',
];

export const DEFAULT_MINIMAP_COLORS: Record<MinimapCategory, string> = {
  player: '#ffffff',
  enemy: '#ff4d4d',
  boss: '#c060ff',
  merchant: '#ffd24a',
  wood: '#6fdc6f',
  stone: '#b9b2a6',
  neutral: '#e8eef7',
};

/** Per-category dot radius in canvas pixels. */
export const DEFAULT_MINIMAP_RADII: Record<MinimapCategory, number> = {
  player: 7,
  enemy: 2.6,
  boss: 4.5,
  merchant: 3.5,
  wood: 1.8,
  stone: 1.8,
  neutral: 2.2,
};

export const DEFAULT_MINIMAP_RANGE = 60;
export const DEFAULT_MINIMAP_SIZE = 168;

export const MINIMAP_WIDGET_TYPE = 'Minimap';
const MINIMAP_COLOR_CHILD_TAG = 'MinimapColor';

export interface MinimapDot {
  readonly x: number;
  readonly z: number;
  readonly category: MinimapCategory;
}

export interface MinimapPlayerMarker {
  readonly x: number;
  readonly z: number;
  readonly heading: number;
}

/** Canvas-free result of {@link collectMinimapDots}: drives the runtime draw and unit tests. */
export interface MinimapCollection {
  readonly player: MinimapPlayerMarker | null;
  readonly dots: readonly MinimapDot[];
}

export interface MinimapOptions {
  readonly range: number;
  readonly size: number;
  readonly categories: ReadonlySet<MinimapCategory>;
  readonly colors: Record<MinimapCategory, string>;
  readonly radii: Record<MinimapCategory, number>;
  readonly anchor: MinimapAnchor;
}

export type MinimapAnchor =
  | 'top-right'
  | 'top-left'
  | 'bottom-right'
  | 'bottom-left';

interface ResolvedOptions extends MinimapOptions {
  readonly scale: number;
}

const creatureQuery = defineQuery([Transform, NavMeshAgent, Health]);
const factionQuery = defineQuery([Transform, FactionComponent]);
const resourceQuery = defineQuery([Transform, ResourceNode]);
const playerQuery = defineQuery([Transform, PlayerController]);

/** Resolve an entity to a minimap category, or `null` if it should be skipped. */
export function resolveMinimapCategory(
  state: State,
  eid: number
): MinimapCategory | null {
  if (state.hasComponent(eid, PlayerController)) return 'player';

  if (state.hasComponent(eid, ResourceNode)) {
    const kind = getResourceNodeKind(state, eid).toLowerCase();
    if (kind === 'wood') return 'wood';
    if (kind === 'stone') return 'stone';
    if (kind === 'ore') return 'stone';
    return 'neutral';
  }

  if (state.hasComponent(eid, FactionComponent)) {
    const tag = FACTION_TAG_NAMES[FactionComponent.tag[eid]] ?? '';
    if (tag === 'enemy') return 'enemy';
    if (tag === 'boss') return 'boss';
    if (tag === 'merchant') return 'merchant';
    if (tag === 'player') return null;
    return 'neutral';
  }

  if (
    state.hasComponent(eid, NavMeshAgent) &&
    state.hasComponent(eid, Health)
  ) {
    return 'enemy';
  }

  return null;
}

/**
 * Collect every blip visible on the minimap plus the player marker. Pure: no
 * canvas, no DOM — drives both the runtime draw and the unit tests.
 */
export function collectMinimapDots(
  state: State,
  options: MinimapOptions
): MinimapCollection {
  const enabled = options.categories;

  let player: MinimapPlayerMarker | null = null;
  for (const eid of playerQuery(state.world)) {
    player = {
      x: Transform.posX[eid],
      z: Transform.posZ[eid],
      heading: Transform.eulerY[eid] || 0,
    };
    break;
  }

  const originX = player ? player.x : 0;
  const originZ = player ? player.z : 0;
  const range2 = options.range * options.range;
  const dots: MinimapDot[] = [];

  const consider = (eid: number): void => {
    if (state.hasComponent(eid, PlayerController)) return;
    const category = resolveMinimapCategory(state, eid);
    if (!category) return;
    if (!enabled.has(category)) return;
    if (
      category === 'enemy' ||
      category === 'boss' ||
      category === 'merchant'
    ) {
      if (state.hasComponent(eid, Health) && Health.current[eid] <= 0) return;
    }
    const x = Transform.posX[eid];
    const z = Transform.posZ[eid];
    const dx = x - originX;
    const dz = z - originZ;
    if (dx * dx + dz * dz > range2) return;
    dots.push({ x, z, category });
  };

  for (const eid of creatureQuery(state.world)) consider(eid);
  for (const eid of factionQuery(state.world)) consider(eid);
  for (const eid of resourceQuery(state.world)) consider(eid);

  return { player, dots };
}

/**
 * Paint the minimap onto a 2D context. Range-ring background, dots clamped to
 * the disc edge, and the player arrow at the centre. `north` (-Z world) is up.
 */
export function drawMinimap(
  ctx: CanvasRenderingContext2D,
  collection: MinimapCollection,
  options: MinimapOptions
): void {
  const size = options.size;
  const cx = size / 2;
  const cy = size / 2;
  const maxPix = size / 2 - 6;
  const scale = maxPix / options.range;
  const originX = collection.player ? collection.player.x : 0;
  const originZ = collection.player ? collection.player.z : 0;

  ctx.save();
  ctx.clearRect(0, 0, size, size);
  ctx.beginPath();
  ctx.arc(cx, cy, size / 2 - 2, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = 'rgba(18,26,40,0.82)';
  ctx.fillRect(0, 0, size, size);

  ctx.strokeStyle = 'rgba(120,150,210,0.12)';
  ctx.lineWidth = 1;
  for (let i = 1; i <= 2; i++) {
    ctx.beginPath();
    ctx.arc(cx, cy, (size / 2 - 2) * (i / 2.4), 0, Math.PI * 2);
    ctx.stroke();
  }

  for (const dot of collection.dots) {
    const rx = (dot.x - originX) * scale;
    const rz = -(dot.z - originZ) * scale;
    const dist = Math.hypot(rx, rz);
    let dx = rx;
    let dz = rz;
    let edge = false;
    if (dist > maxPix) {
      dx = (rx / dist) * maxPix;
      dz = (rz / dist) * maxPix;
      edge = true;
    }
    const radius = options.radii[dot.category] ?? 2;
    ctx.beginPath();
    ctx.arc(cx + dx, cy + dz, edge ? radius * 0.7 : radius, 0, Math.PI * 2);
    ctx.fillStyle =
      options.colors[dot.category] ?? DEFAULT_MINIMAP_COLORS[dot.category];
    ctx.globalAlpha = edge ? 0.5 : 1;
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  if (collection.player) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-collection.player.heading);
    ctx.beginPath();
    ctx.moveTo(0, -7);
    ctx.lineTo(5, 6);
    ctx.lineTo(0, 3);
    ctx.lineTo(-5, 6);
    ctx.closePath();
    ctx.fillStyle = options.colors.player ?? DEFAULT_MINIMAP_COLORS.player;
    ctx.strokeStyle = 'rgba(0,0,0,0.55)';
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  ctx.restore();

  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.font = '700 9px system-ui,sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('N', cx, 11);
}

/** Parse `<Minimap range size categories anchor color-enemy .../>` + `<MinimapColor/>` children. */
export function parseMinimapOptions(
  attributes: Record<string, XMLValue>,
  children: readonly ParsedElement[]
): MinimapOptions {
  const range = parseNumber(attributes.range, DEFAULT_MINIMAP_RANGE);
  const size = parseNumber(attributes.size, DEFAULT_MINIMAP_SIZE);
  const anchor = parseAnchor(attributes.anchor, 'top-right');
  const categories = parseCategories(attributes.categories);

  const colors: Record<MinimapCategory, string> = { ...DEFAULT_MINIMAP_COLORS };
  for (const cat of MINIMAP_CATEGORY_VALUES) {
    const inline = attributes[`color-${cat}`];
    if (typeof inline === 'string' && inline.length > 0) colors[cat] = inline;
  }
  for (const child of children) {
    if (child.tagName !== MINIMAP_COLOR_CHILD_TAG) continue;
    const cat = String(child.attributes.category ?? '')
      .trim()
      .toLowerCase();
    const color = child.attributes.color;
    if (!isMinimapCategory(cat)) continue;
    if (typeof color !== 'string' || color.length === 0) continue;
    colors[cat] = color;
  }

  const radii: Record<MinimapCategory, number> = { ...DEFAULT_MINIMAP_RADII };

  return { range, size, categories, colors, radii, anchor };
}

/** Build a {@link MinimapOptions} from `<Minimap>` XML element. */
export function minimapParser(params: {
  element: ParsedElement;
  state: State;
}): void {
  const options = parseMinimapOptions(
    params.element.attributes,
    params.element.children
  );
  const widget = new MinimapWidget(options);
  registerHudWidget(params.state, widget);
}

/** Register the minimap as a `<HudWidget type="Minimap" …/>` factory. */
export function registerMinimapWidgetFactory(): void {
  const factory = (
    attributes: Record<string, XMLValue>,
    state: State
  ): HudWidget => {
    void state;
    const options = parseMinimapOptions(attributes, []);
    return new MinimapWidget(options);
  };
  registerHudWidgetFactory(MINIMAP_WIDGET_TYPE, factory);
}

export class MinimapWidget implements HudWidget {
  readonly id = MINIMAP_WIDGET_TYPE;
  private readonly resolved: ResolvedOptions;

  constructor(options: MinimapOptions) {
    this.resolved = {
      ...options,
      scale: (options.size / 2 - 6) / options.range,
    };
  }

  mount(layer: HTMLDivElement): WidgetHandle {
    const wrapper = document.createElement('div');
    wrapper.className = 'vibe-hud-minimap';
    wrapper.dataset.minimapId = MINIMAP_WIDGET_TYPE;
    wrapper.style.cssText = wrapperStyle(this.resolved.anchor);

    const canvas = document.createElement('canvas');
    canvas.className = 'vibe-hud-minimap-canvas';
    canvas.width = this.resolved.size;
    canvas.height = this.resolved.size;
    canvas.style.cssText = `width:${this.resolved.size}px;height:${this.resolved.size}px;border-radius:50%;display:block;`;
    wrapper.appendChild(canvas);
    layer.appendChild(wrapper);

    const ctx = canvas.getContext('2d');

    return {
      root: wrapper,
      update: (state: State): void => {
        if (state.headless) return;
        if (!ctx) return;
        const collection = collectMinimapDots(state, this.resolved);
        drawMinimap(ctx, collection, this.resolved);
      },
      unmount: (): void => {
        if (wrapper.parentNode) wrapper.parentNode.removeChild(wrapper);
      },
    };
  }
}

function wrapperStyle(anchor: MinimapAnchor): string {
  const base =
    'position:absolute;z-index:11;pointer-events:none;' +
    'box-shadow:0 6px 20px rgba(0,0,0,0.35);' +
    'border:1px solid rgba(120,150,220,0.22);';
  const gap = '18px';
  switch (anchor) {
    case 'top-left':
      return `${base}top:${gap};left:${gap};`;
    case 'bottom-right':
      return `${base}bottom:${gap};right:${gap};`;
    case 'bottom-left':
      return `${base}bottom:${gap};left:${gap};`;
    case 'top-right':
    default:
      return `${base}top:${gap};right:${gap};`;
  }
}

function parseNumber(value: XMLValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  const n = typeof value === 'number' ? value : Number(String(value));
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function parseAnchor(
  value: XMLValue | undefined,
  fallback: MinimapAnchor
): MinimapAnchor {
  if (typeof value !== 'string') return fallback;
  switch (value.trim().toLowerCase()) {
    case 'top-left':
      return 'top-left';
    case 'bottom-right':
      return 'bottom-right';
    case 'bottom-left':
      return 'bottom-left';
    case 'top-right':
      return 'top-right';
    default:
      return fallback;
  }
}

function parseCategories(raw: XMLValue | undefined): Set<MinimapCategory> {
  const result = new Set<MinimapCategory>(['player']);
  if (raw === undefined || raw === null) {
    for (const c of MINIMAP_CATEGORY_VALUES) if (c !== 'player') result.add(c);
    return result;
  }
  const list = String(raw)
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);
  for (const entry of list) {
    if (isMinimapCategory(entry)) result.add(entry);
  }
  return result;
}

function isMinimapCategory(value: string): value is MinimapCategory {
  return (MINIMAP_CATEGORY_VALUES as readonly string[]).includes(value);
}
