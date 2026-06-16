// Shared modal/pause state so independent scripts (main HUD, merchant) can
// coordinate: the pause menu must not open over an active shop dialog, and
// scripts must not act on input while the game is paused.

let paused = false;
let shopOpen = false;

export function isGamePaused(): boolean {
  return paused;
}

export function setGamePaused(value: boolean): void {
  paused = value;
}

export function isShopOpen(): boolean {
  return shopOpen;
}

export function setShopOpen(value: boolean): void {
  shopOpen = value;
}
