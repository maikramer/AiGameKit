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

/**
 * Aplica um pincel de altura ao sampler (in place). Devolve true se alterou
 * pelo menos um texel. O AABB é expandido em 1 texel para os cantos/orlas
 * nunca ficarem de fora por arredondamento.
 */
export function applyHeightBrush(
  sampler: HeightSampler,
  brush: HeightBrush
): boolean {
  const { data, width, height, worldSize, maxHeight } = sampler;
  if (!data || width < 2 || height < 2 || maxHeight <= 0) return false;

  const half = worldSize / 2;
  const stepX = worldSize / (width - 1);
  const stepZ = worldSize / (height - 1);

  // +1 texel de margem: cantos do pincel nunca caem fora por floor/ceil.
  const x0 = Math.max(0, Math.floor((brush.minX + half) / stepX) - 1);
  const x1 = Math.min(width - 1, Math.ceil((brush.maxX + half) / stepX) + 1);
  const z0 = Math.max(0, Math.floor((brush.minZ + half) / stepZ) - 1);
  const z1 = Math.min(height - 1, Math.ceil((brush.maxZ + half) / stepZ) + 1);

  const mode = brush.mode ?? 'blend';
  let changed = false;
  for (let zi = z0; zi <= z1; zi++) {
    const wz = zi * stepZ - half;
    for (let xi = x0; xi <= x1; xi++) {
      const wx = xi * stepX - half;
      const s = brush.evalAt(wx, wz);
      if (!s || s.weight <= 0) continue;
      const w = Math.min(1, s.weight);
      const target = Math.min(1, Math.max(0, s.targetY / maxHeight));
      const i = zi * width + xi;
      const cur = data[i]!;
      const next = cur + (target - cur) * w;
      if (mode === 'lower' && next >= cur) continue;
      if (mode === 'raise' && next <= cur) continue;
      if (next !== cur) {
        data[i] = next;
        changed = true;
      }
    }
  }
  return changed;
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
