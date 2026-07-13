import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';

/**
 * Corredor de estrada: suaviza o terreno ao longo do path (perfil longitudinal
 * = média móvel das alturas originais) e assenta a faixa nesse perfil, com
 * falloff lateral de volta ao relevo natural — corte e aterro, como estradas
 * reais. Mutar o sampler mantém TODOS os consumidores coerentes (chunks de
 * qualquer LOD, física, BVH, spawners) — é isto que elimina o "morrinho" a
 * cortar a estrada: sem o carve, os chunks LOD grosseiros fazem corda por
 * cima das depressões e nenhum offset fixo do ribbon cobre todos os LODs.
 */
export interface RoadCorridorOpts {
  /** Polyline `[x0,z0,...]` em coordenadas FIELD-LOCAL, já suavizada. */
  path: number[];
  /** Largura da faixa nivelada (m). */
  width: number;
  /** Blend lateral de volta ao terreno original (m). */
  falloff: number;
  /** Janela da média móvel do perfil longitudinal (m). */
  window: number;
}

/** Alturas por estação + arco acumulado (perfil longitudinal cru). */
function stationProfile(
  sampler: HeightSampler,
  path: number[]
): { arcs: number[]; heights: number[] } {
  const n = path.length / 2;
  const arcs: number[] = [0];
  const heights: number[] = [];
  for (let i = 0; i < n; i++) {
    heights.push(sampleHeightAt(sampler, path[i * 2]!, path[i * 2 + 1]!));
    if (i > 0) {
      arcs.push(
        arcs[i - 1]! +
          Math.hypot(
            path[i * 2]! - path[(i - 1) * 2]!,
            path[i * 2 + 1]! - path[(i - 1) * 2 + 1]!
          )
      );
    }
  }
  return { arcs, heights };
}

/** Média móvel triangular do perfil, janela em metros de arco. */
function smoothProfile(
  arcs: number[],
  heights: number[],
  window: number
): number[] {
  const half = Math.max(window, 0.01) / 2;
  const out: number[] = [];
  for (let i = 0; i < heights.length; i++) {
    let acc = 0;
    let wsum = 0;
    for (let j = 0; j < heights.length; j++) {
      const d = Math.abs(arcs[j]! - arcs[i]!);
      if (d > half) continue;
      const w = 1 - d / half;
      acc += heights[j]! * w;
      wsum += w;
    }
    out.push(wsum > 0 ? acc / wsum : heights[i]!);
  }
  return out;
}

/**
 * Escreve o corredor no sampler (in place). Devolve true se alterou texels.
 * Ambas as direções (corta morros, aterra vales), como o flattenRect dos
 * TerrainPads.
 */
export function carveRoadCorridor(
  sampler: HeightSampler,
  opts: RoadCorridorOpts
): boolean {
  const { data, width: texW, height: texH, worldSize, maxHeight } = sampler;
  if (!data || texW < 2 || texH < 2 || maxHeight <= 0) return false;
  const path = opts.path;
  if (path.length < 4) return false;

  const halfWidth = Math.max(opts.width, 0.1) / 2;
  const fall = Math.max(opts.falloff, 0.01);
  const reach = halfWidth + fall;

  const { arcs, heights } = stationProfile(sampler, path);
  const profile = smoothProfile(arcs, heights, opts.window);

  // AABB do path (+reach) em texels.
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < path.length; i += 2) {
    minX = Math.min(minX, path[i]!);
    maxX = Math.max(maxX, path[i]!);
    minZ = Math.min(minZ, path[i + 1]!);
    maxZ = Math.max(maxZ, path[i + 1]!);
  }
  const half = worldSize / 2;
  const stepX = worldSize / (texW - 1);
  const stepZ = worldSize / (texH - 1);
  const x0 = Math.max(0, Math.floor((minX - reach + half) / stepX));
  const x1 = Math.min(texW - 1, Math.ceil((maxX + reach + half) / stepX));
  const z0 = Math.max(0, Math.floor((minZ - reach + half) / stepZ));
  const z1 = Math.min(texH - 1, Math.ceil((maxZ + reach + half) / stepZ));

  const segCount = path.length / 2 - 1;
  let changed = false;
  for (let zi = z0; zi <= z1; zi++) {
    const wz = zi * stepZ - half;
    for (let xi = x0; xi <= x1; xi++) {
      const wx = xi * stepX - half;

      // Ponto mais próximo no path: distância lateral + segmento interpolado.
      let bestD = Infinity;
      let bestSeg = 0;
      let bestT = 0;
      for (let s = 0; s < segCount; s++) {
        const ax = path[s * 2]!;
        const az = path[s * 2 + 1]!;
        const bx = path[(s + 1) * 2]!;
        const bz = path[(s + 1) * 2 + 1]!;
        const dx = bx - ax;
        const dz = bz - az;
        const lenSq = dx * dx + dz * dz;
        let t = lenSq > 0 ? ((wx - ax) * dx + (wz - az) * dz) / lenSq : 0;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        const cx = ax + t * dx;
        const cz = az + t * dz;
        const d = Math.hypot(wx - cx, wz - cz);
        if (d < bestD) {
          bestD = d;
          bestSeg = s;
          bestT = t;
        }
      }
      if (bestD >= reach) continue;

      // Alvo: perfil suavizado interpolado no arco do ponto mais próximo.
      const p0 = profile[bestSeg]!;
      const p1 = profile[bestSeg + 1]!;
      const targetY = p0 + (p1 - p0) * bestT;
      const target = Math.min(1, Math.max(0, targetY / maxHeight));

      // Peso: 1 dentro da faixa, smoothstep até 0 no fim do falloff.
      let w = 1;
      if (bestD > halfWidth) {
        const t = (bestD - halfWidth) / fall;
        w = 1 - t * t * (3 - 2 * t);
      }
      const i = zi * texW + xi;
      const next = data[i]! + (target - data[i]!) * w;
      if (next !== data[i]!) {
        data[i] = next;
        changed = true;
      }
    }
  }
  return changed;
}
