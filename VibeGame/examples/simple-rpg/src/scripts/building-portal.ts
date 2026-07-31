import { defineQuery } from 'vibegame';
import type { MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  Rigidbody,
  PlayerController,
  isKeyDown,
  registerInteractionTarget,
  unregisterInteractionTarget,
  getBodyForEntity,
  getTerrainHeightAt,
  getBodyYForFeetAt,
  terrainReady,
} from 'vibegame';
import { getInteriorSpawn, type InteriorId } from '../game/interiors.ts';

/**
 * Door portal: F → teleport when interior spawn exists, else toast stub.
 * Multiple instances share this module — state is keyed by entity id.
 */

const ENTER_RANGE = 2.8;
const ENTER_RANGE_SQ = ENTER_RANGE * ENTER_RANGE;
const GROUND_CONTACT_SKIN = 0.05;
const TOAST_MS = 2200;

/** Entity `name=` → interior id (mirrors portals.xml). */
const PORTAL_DEFS: Record<string, { interior: InteriorId; label: string }> = {
  'portal.house_a': { interior: 'house_a', label: 'Entrar na casa' },
  'portal.house_b': { interior: 'house_b', label: 'Entrar na casa' },
  'portal.house_c': { interior: 'house_c', label: 'Entrar na casa' },
  'portal.shepherd_cottage': {
    interior: 'shepherd_cottage',
    label: 'Entrar na cabana',
  },
  'portal.chapel': { interior: 'chapel', label: 'Entrar na capela' },
  'portal.village_forge': {
    interior: 'village_forge',
    label: 'Entrar na forja',
  },
  'portal.village_barn': {
    interior: 'village_barn',
    label: 'Entrar no celeiro',
  },
  'portal.longhouse': { interior: 'longhouse', label: 'Entrar no longhouse' },
  'portal.market_stall_a': {
    interior: 'market_stall_a',
    label: 'Entrar na banca',
  },
  'portal.market_stall_b': {
    interior: 'market_stall_b',
    label: 'Entrar na banca',
  },
  'portal.market_stall_c': {
    interior: 'market_stall_c',
    label: 'Entrar na banca',
  },
};

const playerQuery = defineQuery([PlayerController]);

type PortalRuntime = {
  fPressed: boolean;
};

const runtimeByEid = new Map<number, PortalRuntime>();
let cachedPlayer = 0;
let toastEl: HTMLDivElement | null = null;
let toastTimeout: ReturnType<typeof setTimeout> | null = null;

function findPlayer(state: State): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined) {
    return cachedPlayer;
  }
  cachedPlayer = playerQuery(state.world)[0] ?? 0;
  return cachedPlayer;
}

function resolveDef(ctx: MonoBehaviourContext): {
  interior: InteriorId;
  label: string;
} | null {
  const name = ctx.state.getEntityName(ctx.entity);
  if (!name) return null;
  return PORTAL_DEFS[name] ?? null;
}

function showToast(message: string): void {
  if (typeof document === 'undefined') return;
  if (!toastEl) {
    toastEl = document.createElement('div');
    toastEl.style.cssText =
      'position:fixed;top:18%;left:50%;transform:translateX(-50%);' +
      'background:rgba(18,14,28,0.94);border:2px solid #8b7cff;' +
      'border-radius:8px;padding:12px 22px;z-index:1000;' +
      'font:17px Georgia,serif;color:#e8e0ff;' +
      'box-shadow:0 0 22px rgba(120,90,255,0.35);opacity:0;transition:opacity 0.2s;';
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = message;
  toastEl.style.opacity = '1';
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    if (toastEl) toastEl.style.opacity = '0';
  }, TOAST_MS);
}

function teleportPlayer(
  state: State,
  player: number,
  x: number,
  y: number,
  z: number
): void {
  Transform.posX[player] = x;
  Transform.posY[player] = y;
  Transform.posZ[player] = z;
  Transform.dirty[player] = 1;
  Rigidbody.posX[player] = x;
  Rigidbody.posY[player] = y;
  Rigidbody.posZ[player] = z;
  Rigidbody.poseDirty[player] = 1;
  Rigidbody.velX[player] = 0;
  Rigidbody.velY[player] = 0;
  Rigidbody.velZ[player] = 0;
  const body = getBodyForEntity(state, player);
  if (body) {
    body.setTranslation({ x, y, z }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    body.wakeUp();
  }
}

function resolveFeetY(
  state: State,
  player: number,
  x: number,
  z: number,
  fallbackY: number
): number {
  if (!terrainReady(state)) return fallbackY;
  const terrainH = getTerrainHeightAt(state, x, z);
  if (!Number.isFinite(terrainH)) return fallbackY;
  return getBodyYForFeetAt(
    state,
    player,
    (terrainH as number) + GROUND_CONTACT_SKIN
  );
}

function tryEnter(
  ctx: MonoBehaviourContext,
  def: { interior: InteriorId; label: string }
): void {
  const spawn = getInteriorSpawn(def.interior);
  if (!spawn) {
    showToast('Interior em breve — porta ainda fechada');
    return;
  }
  const player = findPlayer(ctx.state);
  if (!player) return;
  const y = resolveFeetY(ctx.state, player, spawn.x, spawn.z, spawn.y);
  teleportPlayer(ctx.state, player, spawn.x, y, spawn.z);
  showToast('Entrou');
}

export function start(ctx: MonoBehaviourContext): void {
  const def = resolveDef(ctx);
  if (!def) return;
  runtimeByEid.set(ctx.entity, { fPressed: false });
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: def.label,
    key: 'F',
    range: ENTER_RANGE,
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  unregisterInteractionTarget(ctx.state, ctx.entity);
  runtimeByEid.delete(ctx.entity);
}

export function update(ctx: MonoBehaviourContext): void {
  const def = resolveDef(ctx);
  if (!def) return;
  let rt = runtimeByEid.get(ctx.entity);
  if (!rt) {
    rt = { fPressed: false };
    runtimeByEid.set(ctx.entity, rt);
  }

  const player = findPlayer(ctx.state);
  if (!player) {
    rt.fPressed = false;
    return;
  }

  const dx = Transform.posX[player] - Transform.posX[ctx.entity];
  const dz = Transform.posZ[player] - Transform.posZ[ctx.entity];
  if (dx * dx + dz * dz > ENTER_RANGE_SQ) {
    rt.fPressed = false;
    return;
  }

  const down = isKeyDown('KeyF');
  if (down && !rt.fPressed) {
    tryEnter(ctx, def);
  }
  rt.fPressed = down;
}
