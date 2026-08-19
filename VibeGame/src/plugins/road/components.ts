import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';
import type { State } from '../../core';

/**
 * `<Road>` — estrada pintada sobre o terreno ao longo de uma polyline, estilo
 * city-builder: a faixa é um ribbon que segue a spline suavizada do path, a
 * textura orienta-se e curva com a estrada (UV por arc-length) e as bordas
 * desvanecem para o terreno com feather + ruído orgânico (vertex alpha).
 * Decal puro: sem colisor. `flatten` = preparação do leito (corte+aterro
 * mínimo) antes do ribbon assentar no terreno já planado — ordem real.
 */
export const Road = defineComponent({
  /** Largura total da faixa / leito (m). */
  width: F32,
  /** Metros de mundo por tile de textura (u e v). */
  textureScale: F32,
  /** Fade lateral de alpha, borda→núcleo (m). */
  edgeFeather: F32,
  /** Amplitude do ruído que corrói a borda para dentro (m). */
  edgeNoise: F32,
  /** Fade longitudinal no início do path (m; 0 = ponta sólida/enterrada). */
  endFeatherStart: F32,
  /** Fade longitudinal no fim do path (m; 0 = ponta sólida). */
  endFeatherEnd: F32,
  /** Elevação da faixa acima do terreno (m). */
  yOffset: F32,
  /** Espaçamento entre estações do ribbon (m; menor = curvas mais suaves). */
  stationSpacing: F32,
  /** 1 = prepara o leito no sampler (corte+aterro) antes de pavimentar. */
  flatten: U8,
  /**
   * 1 = pinta o ribbon. `paint="0"` = usar `<Road>` só como terraplanagem
   * (o jogo desenha a superfície: pista de corrida, ponte, plataforma).
   */
  paint: U8,
  /** Ombro: blend lateral leito→relevo natural (m). Curto = carve mínimo. */
  flattenFalloff: F32,
  /** Suavização leve do perfil longitudinal (m) — não é corte de autoestrada. */
  flattenWindow: F32,
  /** Max |Δh/Δs| do perfil de projecto (0 = sem limite de pendente). */
  flattenMaxGrade: F32,
  /** 1 = path é um circuito fechado (perfil suaviza através da junta). */
  flattenClosed: U8,
  /** Run-off plano de cada lado do leito (m) antes do ombro. */
  flattenShoulder: F32,
  /** Altura do berm no bordo do run-off (m; negativo = vala). */
  flattenBerm: F32,
  /** Banda lateral em que o berm sobe (m). */
  flattenBermWidth: F32,
  /** 1 = leito inclina com {@link RoadData.banks} (cross-slope). */
  flattenBank: U8,
  /**
   * Viaduto: acima desta folga (m) entre a cota de projecto e o terreno
   * natural, o carve NÃO toca no chão — o vale/floresta/prédios por baixo
   * ficam intactos. `0` = desligado (estrada sempre assente).
   */
  flattenViaductClearance: F32,
  /**
   * Declive máximo (m/m) do talude de corte: cortes profundos alargam o
   * `flatten-falloff` para o talude ler como encosta natural em vez de vala
   * artificial. `0` = falloff fixo autorado (aproxes de ponta usam sempre 0).
   */
  flattenMaxCutSlope: F32,
  /**
   * 1 = quando o corredor passa duas vezes pelo mesmo texel, ganha a passagem
   * cuja cota de projecto está mais perto do terreno (viadutos / braços de
   * circuito lado a lado); 0 = ganha a estação mais próxima.
   */
  flattenOverlapElevation: U8,
  /** Iterações de suavização Chaikin do path (0 = cantos vivos). */
  smoothing: U8,
  /** Opacidade global 0..1. */
  opacity: F32,
  /** Roughness do material. */
  roughness: F32,
  /** Metalness do material. */
  metalness: F32,
  /**
   * 1 = Segment bridge: ribbon at {@link Road.deckY}, approach-only flatten,
   * GLB deck spawn (see RoadData.bridgeUrl).
   */
  bridge: U8,
  /** Mean bank Y (spawn / fallback); ribbon may lerp {@link Road.deckY0}→{@link Road.deckY1}. */
  deckY: F32,
  /** Bank height at path start (world Y), resolved at apply. */
  deckY0: F32,
  /** Bank height at path end (world Y), resolved at apply. */
  deckY1: F32,
  /** 1 quando o ribbon foi construído e adicionado à cena. */
  applied: U8,
});

/** Dados não-SOA da estrada (bitecs não guarda arrays/strings). */
export interface RoadData {
  /** Polyline `[x0,z0,x1,z1,...]` em coordenadas de mundo. */
  path: number[];
  /**
   * Optional per-vertex widths (m), one float per path point.
   * When set, ribbon/carve use `makeWidthAtFromVertexWidths` instead of scalar
   * `Road.width`. Length must match `path.length / 2`.
   */
  widths?: number[];
  /**
   * Optional authored design elevation (world Y) per path point. When set the
   * bed is carved exactly there instead of being surveyed from the terrain —
   * the right way round for a circuit, where the driving surface is authored
   * in 3D and the ground has to follow it. Length must match `path.length / 2`.
   */
  heights?: number[];
  /**
   * Optional per-point cross-slope (degrees, `+` raises the right side, same
   * sign as `TrackSpline`). Requires `Road.flattenBank`.
   */
  banks?: number[];
  textureUrl: string | null;
  normalMapUrl: string | null;
  roughnessMapUrl: string | null;
  /** Visual GLB for bridge deck (null = not a bridge). */
  bridgeUrl?: string | null;
  bridgeCollisionUrl?: string | null;
  bridgeLod1Url?: string | null;
  bridgeLod2Url?: string | null;
  /** Mesh local +X length before scale (m); default 18. */
  bridgeNativeSpan?: number;
}

const ROAD_DATA = new WeakMap<State, Map<number, RoadData>>();

export function getRoadData(state: State, entity: number): RoadData | null {
  return ROAD_DATA.get(state)?.get(entity) ?? null;
}

export function setRoadData(
  state: State,
  entity: number,
  data: RoadData
): void {
  let m = ROAD_DATA.get(state);
  if (!m) {
    m = new Map();
    ROAD_DATA.set(state, m);
  }
  m.set(entity, data);
}

export function deleteRoadData(state: State, entity: number): void {
  ROAD_DATA.get(state)?.delete(entity);
}
