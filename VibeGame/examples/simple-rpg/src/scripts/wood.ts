let woodCount = 0;

let lastCollectX = 0;
let lastCollectY = 0;
let lastCollectZ = 0;
let lastCollectVersion = 0;

export function addWood(amount: number, x = 0, y = 0, z = 0): void {
  woodCount += amount;
  lastCollectX = x;
  lastCollectY = y;
  lastCollectZ = z;
  lastCollectVersion++;
}

export function getWoodCount(): number {
  return woodCount;
}

export function removeWood(amount: number): boolean {
  if (woodCount < amount) return false;
  woodCount -= amount;
  return true;
}

export function getLastWoodCollectPosition(): {
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
