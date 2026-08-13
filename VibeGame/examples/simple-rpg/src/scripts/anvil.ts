// Forge anvil: [K] craft a bomb from scrap. K so [F] on the blacksmith
// quest never collides (anvil sits ~2 m from Bram).
import {
  Transform,
  isKeyDown,
  addItem,
  registerInteractionTarget,
  playSound,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import { isGamePaused } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { getStoneCount, removeStone } from './inventory.ts';
import { getWoodCount, removeWood } from './wood.ts';
import { showToast } from '../../../shared/src/ui';
import {
  BOMB_CRAFT_STONE,
  BOMB_CRAFT_WOOD,
  canCraftBomb,
} from '../game/city-amenities.ts';

const RANGE_SQ = 3.4 * 3.4;

let kPressed = false;

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: `Forjar bomba (${BOMB_CRAFT_STONE} pedra + ${BOMB_CRAFT_WOOD} madeira)`,
    key: 'K',
  });
}

export function update(ctx: MonoBehaviourContext): void {
  if (isGamePaused()) return;
  const player = findPlayer(ctx.state);
  if (!player) return;
  const eid = ctx.entity;
  const dx = Transform.posX[player] - Transform.posX[eid];
  const dz = Transform.posZ[player] - Transform.posZ[eid];
  if (dx * dx + dz * dz >= RANGE_SQ) {
    kPressed = isKeyDown('KeyK');
    return;
  }

  const k = isKeyDown('KeyK');
  if (k && !kPressed) {
    const stone = getStoneCount();
    const wood = getWoodCount();
    if (!canCraftBomb(stone, wood)) {
      showToast(
        `Preciso de ${BOMB_CRAFT_STONE} pedras e ${BOMB_CRAFT_WOOD} madeira.`,
        {
          color: '#e8c090',
          borderColor: '#8a6a40',
          background: 'rgba(20,14,8,0.95)',
        }
      );
      playSound('error');
    } else {
      removeStone(BOMB_CRAFT_STONE);
      removeWood(BOMB_CRAFT_WOOD);
      addItem(ctx.state, player, 'bomb', 1);
      playSound('buy');
      showToast('Uma bomba sai da bigorna.', {
        color: '#e8d8b0',
        borderColor: '#c8a04a',
        background: 'rgba(20,14,8,0.95)',
      });
    }
  }
  kPressed = k;
}
