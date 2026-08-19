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
 * Quanto o valor de um texel alcança em direção ao interior do pincel, em
 * múltiplos do passo do texel: a diagonal completa do stencil bilinear 2×2
 * (√2 · step). O stamp primário descreve a superfície no **centro** do
 * texel, mas mesh/collider/ribbon reconstruem por interpolação bilinear
 * entre centros — o valor de um texel influencia cada ponto até um texel de
 * distância em cada eixo, logo um texel vizinho cujo centro ficou fora da
 * banda de peso total ainda levanta a reconstrução dentro do leito carvado.
 */
export const TEXEL_INFLUENCE_REACH = Math.SQRT2;

/** Distância world-space que o stencil bilinear de um texel invade (m). */
export function texelInfluenceReach(sampler: HeightSampler): number {
  return samplerTexelStep(sampler) * TEXEL_INFLUENCE_REACH;
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
  /**
   * Clamp geométrico lower-only, avaliado no **centro** do texel como
   * `evalAt`, mas descrevendo o stencil bilinear: o valor que o texel não
   * pode exceder para a reconstrução não subir acima da superfície de
   * projeto dentro da banda de peso total do pincel.
   *
   * Um texel vizinho cujo centro cai na banda de falloff fica em
   * `atual + (projeto − atual)·w` — num corte profundo isso é metros acima
   * da cama, e o stencil dele (± {@link texelInfluenceReach}) levanta o
   * leito: terreno a furar a estrada/pista exatamente na borda. O clamp
   * avalia o perfil de projeto na borda do stencil voltada ao corredor
   * (`dist − texelInfluenceReach`) e **só baixa** — nunca aterra aterros
   * (vale), não mexe em flats (min já satisfeito) e respeita o journal do
   * owner como qualquer outra escrita.
   *
   * null = sem clamp (centro já full-weight — o primário é exato — ou o
   * stencil não alcança a banda full-weight).
   */
  guardAt?: (x: number, z: number) => BrushSample | null;
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
 * Undo journal for one carver (`owner`): the texels it wrote and what was
 * there before. Re-carving the same feature is otherwise **not idempotent** —
 * a terrace survey reads the terrain it flattened last time and the bed
 * creeps down by `platformSink` on every regrade (a road that gets regraded
 * whenever a neighbouring feature carves sinks a little each pass).
 */
interface BrushJournal {
  idx: Int32Array;
  prev: Float32Array;
}

const BRUSH_JOURNALS = new WeakMap<HeightSampler, Map<string, BrushJournal>>();

export interface ApplyBrushOpts {
  /**
   * Stable id for the carver (e.g. `road:12`). When set, the previous stamp
   * from the same owner is journalled so {@link revertHeightBrush} can undo it
   * before a re-carve. Owners are independent: reverting a road does not touch
   * a lake's writes — but a revert **does** discard whatever another carver
   * wrote on top of those texels afterwards, so revert then re-stamp in the
   * same pass (that is what {@link revertHeightBrush} callers do).
   */
  owner?: string;
}

/**
 * Restore the texels an owner last wrote. Returns true when anything changed.
 * Call immediately **before** re-surveying + re-stamping the same feature.
 */
export function revertHeightBrush(
  sampler: HeightSampler,
  owner: string
): boolean {
  const journals = BRUSH_JOURNALS.get(sampler);
  const journal = journals?.get(owner);
  const data = sampler.data;
  if (!journal || !data) return false;
  let changed = false;
  // Reverse order: a texel written by two stamps of the same owner must end up
  // with the value it had before the FIRST stamp.
  for (let i = journal.idx.length - 1; i >= 0; i--) {
    const idx = journal.idx[i]!;
    if (idx < 0 || idx >= data.length) continue;
    if (data[idx] !== journal.prev[i]!) changed = true;
    data[idx] = journal.prev[i]!;
  }
  journals!.delete(owner);
  return changed;
}

/** Drop every journal for a sampler (terrain re-decode / field disposed). */
export function clearHeightBrushJournals(sampler: HeightSampler): void {
  BRUSH_JOURNALS.delete(sampler);
}

/**
 * Aplica um pincel de altura ao sampler (in place). Devolve true se alterou
 * pelo menos um texel. O AABB é expandido em 1 texel para os cantos/orlas
 * nunca ficarem de fora por arredondamento.
 */
export function applyHeightBrush(
  sampler: HeightSampler,
  brush: HeightBrush,
  opts?: ApplyBrushOpts
): boolean {
  const { data, maxHeight } = sampler;
  if (!data || maxHeight <= 0) return false;

  const mode = brush.mode ?? 'blend';
  const owner = opts?.owner;
  const journalIdx: number[] | null = owner ? [] : null;
  const journalPrev: number[] | null = owner ? [] : null;
  let changed = false;
  const ok = forEachTexelInAabb(sampler, brush, (idx, wx, wz) => {
    const s = brush.evalAt(wx, wz);
    const guard = brush.guardAt?.(wx, wz) ?? null;
    if (!s && !guard) return;
    const cur = data[idx]!;

    // Primary stamp (blend + mode filters). A mode rejection skips the
    // primary write but never the guard below — the guard has its own
    // lower-only direction.
    let next: number | null = null;
    if (s && s.weight > 0) {
      const w = Math.min(1, s.weight);
      const target = Math.min(1, Math.max(0, s.targetY / maxHeight));
      const cand = cur + (target - cur) * w;
      if (mode !== 'lower' || cand < cur) {
        if (mode !== 'raise' || cand > cur) next = cand;
      }
    }

    // Cell-aware clamp: only ever lowers, and only when the guard target is
    // below what the primary (or the natural terrain) left there.
    if (guard && guard.weight > 0) {
      const w = Math.min(1, guard.weight);
      const target = Math.min(1, Math.max(0, guard.targetY / maxHeight));
      const cand = cur + (target - cur) * w;
      if (cand < cur && (next === null || cand < next)) next = cand;
    }

    if (next === null || next === cur) return;
    if (journalIdx) {
      journalIdx.push(idx);
      journalPrev!.push(cur);
    }
    data[idx] = next;
    changed = true;
  });
  if (owner && journalIdx && journalIdx.length > 0) {
    let journals = BRUSH_JOURNALS.get(sampler);
    if (!journals) {
      journals = new Map();
      BRUSH_JOURNALS.set(sampler, journals);
    }
    const existing = journals.get(owner);
    // Multi-stamp carvers (bridge stubs, banked passes) call apply more than
    // once per carve: append instead of dropping the earlier stamp's undo.
    journals.set(owner, {
      idx: concatInt32(existing?.idx, journalIdx),
      prev: concatFloat32(existing?.prev, journalPrev!),
    });
  }
  return ok && changed;
}

function concatInt32(head: Int32Array | undefined, tail: number[]): Int32Array {
  if (!head || head.length === 0) return Int32Array.from(tail);
  const out = new Int32Array(head.length + tail.length);
  out.set(head, 0);
  out.set(tail, head.length);
  return out;
}

function concatFloat32(
  head: Float32Array | undefined,
  tail: number[]
): Float32Array {
  if (!head || head.length === 0) return Float32Array.from(tail);
  const out = new Float32Array(head.length + tail.length);
  out.set(head, 0);
  out.set(tail, head.length);
  return out;
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
  }
  data.chunkColliders.clear();
  // The collider set is empty until TerrainChunkColliderSystem rebuilds it
  // (next simulation tick). Readiness must say so: gates and spawners that
  // poll it would otherwise release entities into a collider-less world.
  data.collisionReady = false;
  invalidateTerrainBvh(state, fieldEntity);
  fireGroundMutationCallbacks(state);
}
