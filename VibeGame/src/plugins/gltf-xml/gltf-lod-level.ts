/**
 * Nível de LOD 0 = mais detalhe, 2 = mais barato.
 *
 * Histerese assimétrica: downgrade (afastar) usa limiares crus para responsividade;
 * upgrade (aproximar) exige cruzar ``threshold * 0.85`` para evitar flickering na fronteira.
 */
export function pickLodLevel(
  dist: number,
  near: number,
  mid: number,
  prevLevel?: number
): number {
  if (prevLevel === undefined) {
    if (dist < near) return 0;
    if (dist < mid) return 1;
    return 2;
  }

  const UPGRADE_MARGIN = 0.85;

  switch (prevLevel) {
    case 0:
      return dist > near ? 1 : 0;
    case 1:
      if (dist < near * UPGRADE_MARGIN) return 0;
      return dist > mid ? 2 : 1;
    case 2:
      return dist < mid * UPGRADE_MARGIN ? 1 : 2;
    default:
      if (dist < near) return 0;
      if (dist < mid) return 1;
      return 2;
  }
}
