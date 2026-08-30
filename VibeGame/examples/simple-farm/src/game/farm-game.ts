// The farm game loop: equips the player, routes [J] (interact near the bed or
// the stall, else use the active tool on the facing tile), regenerates stamina
// and keeps the <Hotbar> in sync with the bag. Everything above the engine
// plugins lives in this folder; main.ts only calls withSystem/initFarmGame.

import {
  GOLD_KIND,
  GameClock,
  InventoryComponent,
  Transform,
  VaultComponent,
  addResource,
  findNearestInteractionTarget,
  getClockEntity,
  getResource,
  isKeyDown,
  isModalOpen,
  onHotbarActivate,
  registerDebugVar,
  registerGlobalSaveSerializer,
  registerHudStatSource,
  t,
} from 'aigamekit-vibegame';
import type { State, System } from 'aigamekit-vibegame';
import { showToast } from '../../../shared/src/ui';
import { regenStamina, staminaValue } from './stamina';
import {
  activeTool,
  activeToolIndex,
  applyToolToFacingTile,
  loadToolDefs,
  setActiveToolIndex,
  setTools,
  syncHotbar,
} from './tools';
import { sleepAtBed } from './sleep';
import { closeShop, isShopOpen, openShop } from './shop';
import { setStamina } from './stamina';

const USE_KEY = 'KeyJ';
const STARTING_GOLD = 500;

let playerInit = false;
let useHeld = false;
let toolsBound = false;

/**
 * [J] near a registered target wins over tool use; the shop swallows the
 * press that closes it.
 */
function handleUse(state: State, player: number): void {
  if (isShopOpen()) {
    closeShop();
    return;
  }
  const nearest = findNearestInteractionTarget(
    state,
    Transform.posX[player],
    Transform.posZ[player],
    { key: USE_KEY }
  );
  if (nearest) {
    if (nearest.info.kind === 'sleep') sleepAtBed(state);
    else if (nearest.info.kind === 'shop') openShop(state, player);
    return;
  }
  const tool = activeTool();
  if (!tool) return;
  const outcome = applyToolToFacingTile(state, player, tool);
  if (outcome.message === 'stamina') {
    showToast(t(state, 'farm.toast.tired'), { durationMs: 1600 });
  } else if (outcome.message === 'no-seeds') {
    showToast(t(state, 'farm.toast.no_seeds'), { durationMs: 1600 });
  }
}

export const FarmGameSystem: System = {
  name: 'FarmGameSystem',
  group: 'simulation',
  update(state: State): void {
    const player = state.getEntityByName('player');
    if (player === null) return;

    if (!playerInit) {
      playerInit = true;
      if (!state.hasComponent(player, InventoryComponent)) {
        state.addComponent(player, InventoryComponent);
      }
      InventoryComponent.capacity[player] = 24;
      if (!state.hasComponent(player, VaultComponent)) {
        state.addComponent(player, VaultComponent);
      }
      addResource(state, player, 'gold', STARTING_GOLD);
    }

    if (!toolsBound) {
      toolsBound = true;
      setTools(loadToolDefs(state));
      onHotbarActivate(state, (index) => {
        setActiveToolIndex(index);
        syncHotbar(state, player);
      });
    }

    regenStamina(state.time.deltaTime);
    if (activeTool() !== null) syncHotbar(state, player);

    if (isModalOpen(state, 'pause')) {
      useHeld = isKeyDown(USE_KEY);
      return;
    }
    const pressed = isKeyDown(USE_KEY);
    if (pressed && !useHeld) handleUse(state, player);
    useHeld = pressed;
  },
};

/** Post-build wiring: stat source, farm save, smoke/QA debug surface. */
export function initFarmGame(state: State): void {
  registerHudStatSource(state, 'stamina', () => staminaValue());

  registerGlobalSaveSerializer(state, 'farm-player', {
    serialize(): unknown {
      return { v: 1, stamina: staminaValue().cur, tool: activeToolIndex() };
    },
    deserialize(_state: State, data: unknown): void {
      const save = data as { v: number; stamina: number; tool: number };
      if (save?.v !== 1) return;
      setStamina(save.stamina);
      setActiveToolIndex(save.tool);
    },
  });

  // Smoke/QA surface: __VIBEGAME__.debug.getVar('farm').
  registerDebugVar(state, 'farm', () => ({
    day: GameClock.day[getClockEntity(state) ?? 0],
    minute: Math.round(GameClock.minuteOfDay[getClockEntity(state) ?? 0]),
    gold: getResource(state, state.getEntityByName('player') ?? 0, GOLD_KIND),
    stamina: staminaValue(),
    activeTool: activeTool()?.id ?? null,
    tools: loadToolDefs(state).map((tool) => tool.id),
  }));
}
