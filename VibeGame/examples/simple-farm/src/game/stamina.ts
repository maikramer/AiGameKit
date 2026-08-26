// Player stamina: the budget that gates field work. The value lives here (one
// player), the <StatBar> reads it through registerHudStatSource, and tools.ts
// pays into it per action. Sleeping restores it fully.

export const STAMINA_MAX = 100;

/** Passive recovery per second while wandering the valley. */
const REGEN_PER_SECOND = 0.9;

let current = STAMINA_MAX;

export function staminaValue(): { cur: number; max: number } {
  return { cur: current, max: STAMINA_MAX };
}

/** Try to pay a cost; false (and no change) when the tank is empty. */
export function trySpendStamina(cost: number): boolean {
  if (cost <= 0) return true;
  if (current < cost) return false;
  current -= cost;
  return true;
}

export function regenStamina(dt: number): void {
  current = Math.min(STAMINA_MAX, current + REGEN_PER_SECOND * dt);
}

export function restoreStamina(): void {
  current = STAMINA_MAX;
}

/** Debug/tests: reset to a known value. */
export function setStamina(value: number): void {
  current = Math.max(0, Math.min(STAMINA_MAX, value));
}
