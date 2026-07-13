import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';

/**
 * `<Road>` — estrada pintada sobre o terreno ao longo de uma polyline, estilo
 * city-builder: a faixa é um ribbon que segue a spline suavizada do path, a
 * textura orienta-se e curva com a estrada (UV por arc-length) e as bordas
 * desvanecem para o terreno com feather + ruído orgânico (vertex alpha).
 * Decal puro: sem colisor; carve opcional (`flatten`) aplaina um corredor no
 * terreno (corte+aterro) antes do ribbon assentar — evita "morrinhos" do LOD.
 */
export const Road = {
  /** Largura total da faixa (m). */
  width: new Float32Array(MAX_ENTITIES),
  /** Metros de mundo por tile de textura (u e v). */
  textureScale: new Float32Array(MAX_ENTITIES),
  /** Fade lateral de alpha, borda→núcleo (m). */
  edgeFeather: new Float32Array(MAX_ENTITIES),
  /** Amplitude do ruído que corrói a borda para dentro (m). */
  edgeNoise: new Float32Array(MAX_ENTITIES),
  /** Fade longitudinal no início do path (m; 0 = ponta sólida/enterrada). */
  endFeatherStart: new Float32Array(MAX_ENTITIES),
  /** Fade longitudinal no fim do path (m; 0 = ponta sólida). */
  endFeatherEnd: new Float32Array(MAX_ENTITIES),
  /** Elevação da faixa acima do terreno (m). */
  yOffset: new Float32Array(MAX_ENTITIES),
  /** Espaçamento entre estações do ribbon (m; menor = curvas mais suaves). */
  stationSpacing: new Float32Array(MAX_ENTITIES),
  /** 1 = aplaina um corredor no terreno ao longo do path (corte e aterro). */
  flatten: new Uint8Array(MAX_ENTITIES),
  /** Blend lateral do corredor de volta ao relevo natural (m). */
  flattenFalloff: new Float32Array(MAX_ENTITIES),
  /** Janela da média móvel do perfil longitudinal do corredor (m). */
  flattenWindow: new Float32Array(MAX_ENTITIES),
  /** Iterações de suavização Chaikin do path (0 = cantos vivos). */
  smoothing: new Uint8Array(MAX_ENTITIES),
  /** Opacidade global 0..1. */
  opacity: new Float32Array(MAX_ENTITIES),
  /** Roughness do material. */
  roughness: new Float32Array(MAX_ENTITIES),
  /** Metalness do material. */
  metalness: new Float32Array(MAX_ENTITIES),
  /** 1 quando o ribbon foi construído e adicionado à cena. */
  applied: new Uint8Array(MAX_ENTITIES),
} as const;

/** Dados não-SOA da estrada (bitecs não guarda arrays/strings). */
export interface RoadData {
  /** Polyline `[x0,z0,x1,z1,...]` em coordenadas de mundo. */
  path: number[];
  textureUrl: string | null;
  normalMapUrl: string | null;
  roughnessMapUrl: string | null;
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
