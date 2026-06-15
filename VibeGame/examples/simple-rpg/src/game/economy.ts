let goldCount = 0;

let lastCollectX = 0;
let lastCollectY = 0;
let lastCollectZ = 0;
let lastCollectVersion = 0;

export function addGold(amount: number, x = 0, y = 0, z = 0): void {
  goldCount += amount;
  lastCollectX = x;
  lastCollectY = y;
  lastCollectZ = z;
  lastCollectVersion++;
}

export function spendGold(amount: number): boolean {
  if (goldCount < amount) return false;
  goldCount -= amount;
  return true;
}

export function getGold(): number {
  return goldCount;
}

export function getLastCollectPosition(): {
  x: number;
  y: number;
  z: number;
  version: number;
} {
  return {
    x: lastCollectX,
    y: lastCollectY,
    z: lastCollectZ,
    version: lastCollectVersion,
  };
}

export function resetEconomy(): void {
  goldCount = 0;
}
