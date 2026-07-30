import type { State } from '../../core';
import { getRapierWorld } from '../physics';
import { invalidateTerrainBvh } from '../bvh';
import { TerrainChunk } from './components';
import type { HeightSampler } from './height-sampler';
import type { TerrainEntityData } from './utils';
import { fireGroundMutationCallbacks } from './utils';

/**
 * Núcleo reutilizável de carving/flatten do terreno. Todos os "pincéis" que
 * mutam o HeightSampler (TerrainPad, corredor de <Road>, taças de <Lake>,
 * canais de <River>) partilham três problemas resolvidos aqui uma única vez:
 *
 *  1. **Iteração texel↔mundo**: conversões AABB→índices com clamps ± meio
 *     texel — historicamente cada carver tinha a sua cópia e os "cantos sem
 *     carve" vinham de off-by-ones nessas conversões. O loop partilhado
 *     expande o AABB em 1 texel para nunca perder a orla.
 *  2. **Amplitude mínima**: um pincel mais estreito que o passo do texel
 *     alias-a até desaparecer (nenhum centro de texel cai dentro do reach).
 *     {@link minEffectiveFalloff} devolve o falloff clamped à resolução real
 *     do sampler; todos os carvers devem passar por ele.
 *  3. **Rebuild dos derivados**: depois de mutar o sampler é OBRIGATÓRIO
 *     invalidar chunk meshes + heightfields Rapier + BVH — três cópias desse
 *     bloco viviam em pads/água/estrada; {@link rebuildTerrainDerivatives}
 *     é agora a única.
 */

/** Passo do texel em metros (assume grelha quadrada; usa o eixo X). */
export function samplerTexelStep(sampler: HeightSampler): number {
  if (sampler.width < 2) return sampler.worldSize;
  return sampler.worldSize / (sampler.width - 1);
}

/**
 * Falloff efetivo ≥ `minTexels` passos de texel. Sem isto, um pincel com
 * falloff menor que o texel produz transições em degrau — ou, com reach
 * total < 1 texel, não escreve NADA (a "amplitude mínima" em falta).
 */
export function minEffectiveFalloff(
  sampler: HeightSampler,
  falloff: number,
  minTexels = 1.5
): number {
  return Math.max(falloff, samplerTexelStep(sampler) * minTexels);
}

/**
 * Full-weight brush width ≥ `minTexels` sampler steps. A `<Road>` bed of ~9 m
 * on a 2000/64 heightmap (texel ≈ 32 m) never covers a texel centre — carve
 * "runs" but the terrain does not move. Expand the **prep** corridor to the
 * sampler lattice; the painted ribbon stays at authored width.
 */
export function minEffectiveWidth(
  sampler: HeightSampler,
  width: number,
  minTexels = 1.5
): number {
  return Math.max(width, samplerTexelStep(sampler) * minTexels);
}

/** Resultado do pincel num ponto: alvo em METROS + peso 0..1. */
export interface BrushSample {
  targetY: number;
  weight: number;
}

export type BrushMode = 'blend' | 'lower' | 'raise';

export interface HeightBrush {
  /** AABB do pincel em coordenadas field-local (m). */
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  /**
   * Avalia o pincel no centro do texel (field-local). null = fora do pincel.
   * `weight` faz lerp para `targetY` em modo 'blend'; nos modos 'lower' e
   * 'raise' o alvo efetivo é lerp(atual→targetY, weight) mas só escreve se
   * baixar/subir, respetivamente (perfis min/max como a água usa).
   */
  evalAt(x: number, z: number): BrushSample | null;
  /** Default 'blend'. */
  mode?: BrushMode;
}

export interface TexelAabb {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

/**
 * Inclusive texel index range for an AABB, expanded by 1 texel so brush
 * corners never fall outside by floor/ceil rounding. Shared by
 * {@link applyHeightBrush} and segmented corridor stamps (rivers).
 */
export function texelIndexRange(
  sampler: HeightSampler,
  aabb: TexelAabb
): { x0: number; x1: number; z0: number; z1: number } | null {
  const { width, height, worldSize } = sampler;
  if (width < 2 || height < 2) return null;
  const half = worldSize / 2;
  const stepX = worldSize / (width - 1);
  const stepZ = worldSize / (height - 1);
  return {
    x0: Math.max(0, Math.floor((aabb.minX + half) / stepX) - 1),
    x1: Math.min(width - 1, Math.ceil((aabb.maxX + half) / stepX) + 1),
    z0: Math.max(0, Math.floor((aabb.minZ + half) / stepZ) - 1),
    z1: Math.min(height - 1, Math.ceil((aabb.maxZ + half) / stepZ) + 1),
  };
}

/**
 * Visit every texel centre inside `aabb` (+1 texel margin). Returns false
 * when the sampler has no writable data. Used by long corridor multi-pass
 * stamps that cannot go through a single {@link HeightBrush}.
 */
export function forEachTexelInAabb(
  sampler: HeightSampler,
  aabb: TexelAabb,
  visit: (idx: number, wx: number, wz: number) => void
): boolean {
  const { data, width, height, worldSize } = sampler;
  if (!data || width < 2 || height < 2) return false;
  const range = texelIndexRange(sampler, aabb);
  if (!range) return false;
  const half = worldSize / 2;
  const stepX = worldSize / (width - 1);
  const stepZ = worldSize / (height - 1);
  for (let zi = range.z0; zi <= range.z1; zi++) {
    const wz = zi * stepZ - half;
    for (let xi = range.x0; xi <= range.x1; xi++) {
      visit(zi * width + xi, xi * stepX - half, wz);
    }
  }
  return true;
}

/**
 * Aplica um pincel de altura ao sampler (in place). Devolve true se alterou
 * pelo menos um texel. O AABB é expandido em 1 texel para os cantos/orlas
 * nunca ficarem de fora por arredondamento.
 */
export function applyHeightBrush(
  sampler: HeightSampler,
  brush: HeightBrush
): boolean {
  const { data, maxHeight } = sampler;
  if (!data || maxHeight <= 0) return false;

  const mode = brush.mode ?? 'blend';
  let changed = false;
  const ok = forEachTexelInAabb(sampler, brush, (idx, wx, wz) => {
    const s = brush.evalAt(wx, wz);
    if (!s || s.weight <= 0) return;
    const w = Math.min(1, s.weight);
    const target = Math.min(1, Math.max(0, s.targetY / maxHeight));
    const cur = data[idx]!;
    const next = cur + (target - cur) * w;
    if (mode === 'lower' && next >= cur) return;
    if (mode === 'raise' && next <= cur) return;
    if (next !== cur) {
      data[idx] = next;
      changed = true;
    }
  });
  return ok && changed;
}

/**
 * Invalida todos os derivados do terreno depois de uma mutação do sampler:
 * chunk meshes (meshDirty), heightfields Rapier por chunk e o BVH. Mesma
 * sequência que TerrainPad/água/estrada duplicavam — usar SEMPRE esta.
 */
export function rebuildTerrainDerivatives(
  state: State,
  fieldEntity: number,
  data: TerrainEntityData
): void {
  for (const chunk of data.chunks) TerrainChunk.meshDirty[chunk] = 1;
  const world = getRapierWorld(state);
  if (world) {
    for (const body of data.chunkColliders.values()) {
      world.removeRigidBody(body);
    }
    data.chunkColliders.clear();
  }
  invalidateTerrainBvh(state, fieldEntity);
  fireGroundMutationCallbacks(state);
}
