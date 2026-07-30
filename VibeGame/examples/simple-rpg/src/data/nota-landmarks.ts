// Catálogo de marcos de traçado de A Nota (GDD F1).
//
// Módulo de dados puro — sem importar o motor — para que o contrato possa ser
// verificado por testes contra `src/data/quests/*.json` sem arrastar o runtime.

export type BiomeId = 'dark-forest' | 'desert' | 'swamp' | 'frozen-peaks';

/**
 * Três marcos por bioma, por `name=` de entidade da cena. Espelha
 * `objective.target` da quest `*_survey` do bioma; um marco fora desta lista
 * (baú, santuário, POI de recompensa) nunca toca na Nota.
 */
export const NOTA_LANDMARKS: Readonly<Record<BiomeId, readonly string[]>> = {
  'dark-forest': [
    'forest-outpost-tower',
    'forest-crossroads-well',
    'forest-stone-circle',
  ],
  desert: ['desert-arch', 'desert-caravan-wreck', 'desert-sun-obelisk'],
  swamp: ['swamp-wrecked-boat', 'swamp-sunken-graves', 'swamp-bone-altar'],
  'frozen-peaks': ['peaks-cairn-1', 'peaks-cairn-2', 'peaks-cairn-3'],
};

export const BIOME_IDS = Object.keys(NOTA_LANDMARKS) as BiomeId[];

/**
 * Raio de anotação por bioma, em metros. **Tem de coincidir** com o `radius` do
 * `objective` da quest `*_survey` correspondente — é o mesmo alcance visto de
 * dois lados (o GDD fala de 9–12 m conforme o marco). Divergir daria um prompt
 * "Medir e assinar" fora do alcance que a quest reconhece, ou o contrário.
 */
export const NOTA_MARK_RADIUS: Readonly<Record<BiomeId, number>> = {
  'dark-forest': 10,
  desert: 12,
  swamp: 11,
  'frozen-peaks': 9,
};

/** Quest de traçado de cada bioma — o `id` no JSON. */
export const SURVEY_QUEST: Readonly<Record<BiomeId, string>> = {
  'dark-forest': 'forest_survey',
  desert: 'desert_survey',
  swamp: 'swamp_survey',
  'frozen-peaks': 'peaks_survey',
};

/** Nome de exibição do bioma, para as mensagens da Nota. */
export const BIOME_LABEL: Readonly<Record<BiomeId, string>> = {
  'dark-forest': 'Floresta Sombria',
  desert: 'Deserto',
  swamp: 'Pântano',
  'frozen-peaks': 'Picos Gelados',
};

/** Nome legível de cada marco — é o que entra na Nota e no toast. */
export const LANDMARK_LABEL: Readonly<Record<string, string>> = {
  'forest-outpost-tower': 'Torre do Posto Avançado',
  'forest-crossroads-well': 'Poço da Encruzilhada',
  'forest-stone-circle': 'Círculo de Menires',
  'desert-arch': 'Arco do Deserto',
  'desert-caravan-wreck': 'Caravana Encalhada',
  'desert-sun-obelisk': 'Obelisco do Sol',
  'swamp-wrecked-boat': 'Barco Naufragado',
  'swamp-sunken-graves': 'Covas Submersas',
  'swamp-bone-altar': 'Altar de Ossos',
  'peaks-cairn-1': 'Primeiro Mojão',
  'peaks-cairn-2': 'Segundo Mojão',
  'peaks-cairn-3': 'Terceiro Mojão',
};

export function landmarkLabel(name: string): string {
  return LANDMARK_LABEL[name] ?? name;
}

export function biomeLabel(biome: BiomeId): string {
  return BIOME_LABEL[biome];
}

/** Bioma a que um marco pertence, ou `null` se não estiver no catálogo. */
export function biomeOfLandmark(name: string): BiomeId | null {
  for (const biome of BIOME_IDS) {
    if (NOTA_LANDMARKS[biome].includes(name)) return biome;
  }
  return null;
}
