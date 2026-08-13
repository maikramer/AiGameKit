// Plaza well: [F] drink — small free heal, short cooldown.
import {
  Transform,
  isKeyDown,
  Health,
  healHealth,
  registerInteractionTarget,
  playSound,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import { isGamePaused } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { showToast } from '../../../shared/src/ui';
import { WELL_COOLDOWN, WELL_HEAL } from '../game/city-amenities.ts';

const RANGE_SQ = 3.6 * 3.6;

let readyAt = 0;
let fPressed = false;

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: 'Beber',
    key: 'F',
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
    fPressed = isKeyDown('KeyF');
    return;
  }

  const now = ctx.state.time.elapsed;
  const wait = Math.ceil(readyAt - now);
  registerInteractionTarget(ctx.state, eid, {
    label: wait > 0 ? `Beber (${wait}s)` : 'Beber',
    key: 'F',
  });

  const f = isKeyDown('KeyF');
  if (f && !fPressed) {
    if (now < readyAt) {
      showToast('O balde ainda desce.', {
        color: '#a8d0e8',
        borderColor: '#4a7a98',
        background: 'rgba(8,16,22,0.95)',
      });
      playSound('error');
    } else {
      const max = Health.max[player] ?? 0;
      const cur = Health.current[player] ?? 0;
      if (cur >= max) {
        showToast('Já estás são.', {
          color: '#a8d0e8',
          borderColor: '#4a7a98',
          background: 'rgba(8,16,22,0.95)',
        });
        playSound('error');
      } else {
        const heal = Math.min(WELL_HEAL, max - cur);
        healHealth(player, heal);
        readyAt = now + WELL_COOLDOWN;
        playSound('heal');
        showToast(`Água fria. (+${heal} HP)`, {
          color: '#b8e0f8',
          borderColor: '#5a9ab8',
          background: 'rgba(8,16,22,0.95)',
        });
      }
    }
  }
  fPressed = f;
}
