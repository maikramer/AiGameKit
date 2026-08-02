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
/**
 * Landing assist: after a teleport, the destination's terrain colliders may
 * not exist yet — chunks far from the camera are destroyed, and recreation is
 * async (LOD select → mesh build → collider). During that window the hero
 * falls through the void; once below ground level the CCT never catches it
 * again (it only collides from above). Every tick, if the hero has fallen
 * more than LANDING_ASSIST_FALL_TOLERANCE below the target, re-teleport; when
 * the collider arrives the CCT snaps and holds the hero near the target, and
 * the assist stops.
 */
const LANDING_ASSIST_TICK_MS = 100;
const LANDING_ASSIST_FALL_TOLERANCE = 0.6;
const LANDING_ASSIST_MAX_TICKS = 60;
/**
 * Anti-bounce: after any portal teleport, ignore F for this long. Without it,
 * a single held F press chains through both portals of a pair — entering
 * lands the hero inside the exit portal's range (spawn-to-exit ~2 m < 2.8 m)
 * and the same held key instantly teleports back out; the exterior exit
 * lands exactly on the enter portal. Result: "Entrou" toast, no scene change.
 */
const TELEPORT_COOLDOWN_MS = 600;
let lastTeleportAt = 0;

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
  // Saídas dos interiores (entidades dentro das salas em interiors.xml) →
  // teleporta para a porta exterior (interiors.ts exit_* spawns).
  'portal.exit_chapel': { interior: 'exit_chapel', label: 'Sair da capela' },
  'portal.exit_village_forge': {
    interior: 'exit_village_forge',
    label: 'Sair da forja',
  },
  'portal.exit_house_a': { interior: 'exit_house_a', label: 'Sair da casa' },
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
  // Mirror the debug `tp` action: write Transform AND Rigidbody SOA pose plus
  // zero velocity, then push the Rapier body. `poseDirty` is deliberately NOT
  // set — the TeleportationSystem re-applies the SOA pose and rewrites
  // InterpolatedTransform mid-frame.
  Transform.posX[player] = x;
  Transform.posY[player] = y;
  Transform.posZ[player] = z;
  Transform.dirty[player] = 1;
  Rigidbody.posX[player] = x;
  Rigidbody.posY[player] = y;
  Rigidbody.posZ[player] = z;
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

function startLandingAssist(
  ctx: MonoBehaviourContext,
  player: number,
  x: number,
  y: number,
  z: number
): void {
  let ticks = 0;
  let stableTicks = 0;
  const iv = setInterval(() => {
    const body = getBodyForEntity(ctx.state, player);
    if (!body) {
      clearInterval(iv);
      return;
    }
    const bodyY = body.translation().y;
    if (y - bodyY <= LANDING_ASSIST_FALL_TOLERANCE) {
      // Perto do destino: o collider existe e o CCT segura o herói. Duas
      // amostras estáveis seguidas → acabou.
      if (++stableTicks >= 2) {
        clearInterval(iv);
        return;
      }
    } else {
      stableTicks = 0;
      // A cair através do vazio (collider ainda não criado): reset.
      if (++ticks > LANDING_ASSIST_MAX_TICKS) {
        clearInterval(iv);
        return;
      }
      teleportPlayer(ctx.state, player, x, y, z);
    }
  }, LANDING_ASSIST_TICK_MS);
}

function teleportWithAssist(
  ctx: MonoBehaviourContext,
  player: number,
  x: number,
  y: number,
  z: number
): void {
  teleportPlayer(ctx.state, player, x, y, z);
  startLandingAssist(ctx, player, x, y, z);
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
  const now = performance.now();
  if (now - lastTeleportAt < TELEPORT_COOLDOWN_MS) return;
  const spawn = getInteriorSpawn(def.interior);
  if (!spawn) {
    showToast('Interior em breve — porta ainda fechada');
    return;
  }
  const player = findPlayer(ctx.state);
  if (!player) return;
  const y = resolveFeetY(ctx.state, player, spawn.x, spawn.z, spawn.y);
  teleportWithAssist(ctx, player, spawn.x, y, spawn.z);
  lastTeleportAt = now;
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
