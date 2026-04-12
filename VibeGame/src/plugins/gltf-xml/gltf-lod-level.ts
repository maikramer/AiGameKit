/** Nível de LOD 0 = mais detalhe, 2 = mais barato. */
export function pickLodLevel(dist: number, near: number, mid: number): number {
  if (dist < near) return 0;
  if (dist < mid) return 1;
  return 2;
}
