// Pause/modal adapter → engine PauseCoordinator. Opening the shop pushes a
// 'shop' modal (which pauses the sim via the coordinator); the engine pause
// menu (TabbedModal) pushes its own modal, so `isGamePaused` is true for both.
import { isPaused, pushModal, popModal, getActiveModal } from 'vibegame';
import { engineState } from './engine-bridge';

export function isGamePaused(): boolean {
  const s = engineState();
  return s ? isPaused(s) : false;
}

export function isShopOpen(): boolean {
  const s = engineState();
  return s ? getActiveModal(s) === 'shop' : false;
}

export function setShopOpen(value: boolean): void {
  const s = engineState();
  if (!s) return;
  if (value) pushModal(s, 'shop');
  else popModal(s, 'shop');
}
