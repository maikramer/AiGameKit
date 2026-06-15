let stoneCount = 0;

let lastCollectX = 0;
let lastCollectY = 0;
let lastCollectZ = 0;
let lastCollectVersion = 0;

export function addStone(amount: number, x = 0, y = 0, z = 0): void {
  stoneCount += amount;
  lastCollectX = x;
  lastCollectY = y;
  lastCollectZ = z;
  lastCollectVersion++;
}

export function getStoneCount(): number {
  return stoneCount;
}

export function removeStone(amount: number): boolean {
  if (stoneCount < amount) return false;
  stoneCount -= amount;
  return true;
}

export function removeStones(amount: number): boolean {
  return removeStone(amount);
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
