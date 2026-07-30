// A Nota — o registro do Anotador (GDD fase F1: "estado e anotar").
//
// O jogador chega a um marco de traçado, aperta [F] ("Medir e assinar") e o
// marco entra na Nota. Três marcos de um bioma fixam-no. Este módulo é a fonte
// única desse estado: as quests `*_survey` passam a ser creditadas por aqui
// (motor em modo `interact`) em vez de contarem sozinhas por proximidade —
// senão o gesto que dá nome ao jogo seria decorativo, já ticado antes de o
// jogador carregar na tecla.
//
// Fora de escopo na F1 (fica para F2): névoa, respawn em marcos, aba Registro,
// camadas musicais, gates de chefe. Ver docs/gdd/07-producao/fases/.
import type { State, System } from 'vibegame';
import {
  Transform,
  PlayerController,
  defineQuery,
  isKeyDown,
  isPaused,
  notifyLandmarkVisited,
  playSound,
  registerInteractionTarget,
  setQuestVisitMode,
  setVisitedTargets,
  spawnFloatingText,
  unregisterInteractionTarget,
} from 'vibegame';
import {
  BIOME_IDS,
  NOTA_LANDMARKS,
  NOTA_MARK_RADIUS,
  SURVEY_QUEST,
  biomeLabel,
  biomeOfLandmark,
  landmarkLabel,
  type BiomeId,
} from '../data/nota-landmarks';

export type { BiomeId };
export {
  BIOME_IDS,
  NOTA_LANDMARKS,
  NOTA_MARK_RADIUS,
  biomeLabel,
  biomeOfLandmark,
  landmarkLabel,
};

/** Contrato de save (GDD 05-tecnico/contratos-de-dados.md). */
export interface NotaSnapshot {
  /** Nomes de entidade dos marcos anotados, únicos e ordenados. */
  marked: string[];
  /** Biomas com os três marcos anotados. */
  fixed: BiomeId[];
  /** Decisão final do ato 3 — reservado, sempre `null` nesta fase. */
  signed: boolean | null;
}

const marked = new Set<string>();
const fixed = new Set<BiomeId>();

export function isMarked(name: string): boolean {
  return marked.has(name);
}

export function isBiomeFixed(biome: BiomeId): boolean {
  return fixed.has(biome);
}

/** Quantos dos três marcos do bioma já estão na Nota. */
export function biomeProgress(biome: BiomeId): number {
  let n = 0;
  for (const name of NOTA_LANDMARKS[biome]) if (marked.has(name)) n++;
  return n;
}

/** Recalcula `fixed` a partir de `marked`. Derivado, nunca escrito à mão. */
function recomputeFixed(): void {
  fixed.clear();
  for (const biome of BIOME_IDS) {
    if (biomeProgress(biome) === NOTA_LANDMARKS[biome].length) fixed.add(biome);
  }
}

/**
 * Anota um marco. Idempotente: a segunda interação no mesmo marco não duplica
 * a entrada nem volta a creditar a quest. Devolve `true` só quando a Nota
 * mudou de facto.
 */
export function markLandmark(state: State, name: string): boolean {
  if (biomeOfLandmark(name) === null) return false;
  if (marked.has(name)) return false;
  marked.add(name);
  recomputeFixed();
  // Fonte única: o motor conta a visita a partir daqui.
  notifyLandmarkVisited(state, name);
  return true;
}

export function notaSnapshot(): NotaSnapshot {
  return {
    marked: [...marked].sort(),
    fixed: [...fixed].sort(),
    signed: null,
  };
}

export function restoreNota(state: State, data: Partial<NotaSnapshot>): void {
  marked.clear();
  for (const name of data.marked ?? []) {
    if (biomeOfLandmark(name) !== null) marked.add(name);
  }
  recomputeFixed();
  syncVisitedToQuests(state);
}

export function clearNota(): void {
  marked.clear();
  fixed.clear();
}

/**
 * Reescreve o conjunto de marcos já contados de cada quest de traçado a partir
 * da Nota. O número de progresso volta com o `QuestState`, mas os nomes vivem
 * só no motor — sem isto, um save recarregado deixaria o jogador voltar a
 * creditar um marco que já tinha anotado.
 */
function syncVisitedToQuests(state: State): void {
  for (const biome of BIOME_IDS) {
    const names = NOTA_LANDMARKS[biome].filter((n) => marked.has(n));
    setVisitedTargets(state, SURVEY_QUEST[biome], names);
  }
}

// ── Interação: [F] no marco ────────────────────────────────────────────────
const INTERACT_KEY = 'KeyF';

const playerQuery = defineQuery([PlayerController, Transform]);

let fHeld = false;
/** Marcos cujo alvo de interação já foi registado (eid → nome). */
const registered = new Map<number, string>();

interface CatalogueEntry {
  readonly name: string;
  readonly biome: BiomeId;
  readonly radiusSq: number;
}

/** Catálogo achatado, com o raio já ao quadrado. Construído uma vez. */
const CATALOGUE: readonly CatalogueEntry[] = BIOME_IDS.flatMap((biome) =>
  NOTA_LANDMARKS[biome].map((name) => ({
    name,
    biome,
    radiusSq: NOTA_MARK_RADIUS[biome] ** 2,
  }))
);

/**
 * Mantém o prompt "Medir e assinar" nos marcos por anotar e trata o [F].
 *
 * Um sistema em vez de um script por marco: o catálogo já lista os nomes, e os
 * doze marcos estão espalhados por quatro XML como `<GameObject>` e
 * `<Composition>` — pôr `script=` em cada um duplicaria a lista e daria doze
 * sítios para ela divergir.
 */
export const NotaSystem: System = {
  name: 'NotaSystem',
  group: 'simulation',
  update(state: State) {
    const pressed = isKeyDown(INTERACT_KEY);
    const justPressed = pressed && !fHeld;
    fHeld = pressed;

    const players = playerQuery(state.world);
    const player = players[0];
    if (player === undefined) return;
    const px = Transform.posX[player];
    const pz = Transform.posZ[player];

    let nearest = 0;
    let nearestName = '';
    let nearestDist = Infinity;

    for (const entry of CATALOGUE) {
      const name = entry.name;
      const eid = state.getEntityByName(name);
      if (eid === null) continue;

      if (marked.has(name)) {
        // Já anotado: sai do prompt, mas continua no mundo.
        if (registered.has(eid)) {
          unregisterInteractionTarget(state, eid);
          registered.delete(eid);
        }
        continue;
      }

      if (!registered.has(eid)) {
        registerInteractionTarget(state, eid, {
          label: 'Medir e assinar',
          key: 'F',
          kind: 'landmark',
          // O prompt tem de acender no mesmo alcance em que o [F] funciona —
          // o default do widget (4,5 m) é menos de metade do raio do bioma.
          range: NOTA_MARK_RADIUS[entry.biome],
        });
        registered.set(eid, name);
      }

      const dx = Transform.posX[eid] - px;
      const dz = Transform.posZ[eid] - pz;
      const d = dx * dx + dz * dz;
      if (d <= entry.radiusSq && d < nearestDist) {
        nearestDist = d;
        nearest = eid;
        nearestName = name;
      }
    }

    if (!justPressed || isPaused(state) || nearest === 0) return;

    const biome = biomeOfLandmark(nearestName);
    if (!markLandmark(state, nearestName)) return;

    unregisterInteractionTarget(state, nearest);
    registered.delete(nearest);

    spawnFloatingText(
      state,
      `Marco registrado — ${landmarkLabel(nearestName)}`,
      {
        x: Transform.posX[nearest],
        y: Transform.posY[nearest] + 2.4,
        z: Transform.posZ[nearest],
        duration: 2.6,
        color: '#ffe3a0',
        stackKey: `nota@${nearest}`,
      }
    );
    playSound('save');

    if (biome && isBiomeFixed(biome)) {
      spawnFloatingText(state, `${biomeLabel(biome)} — traçado completo`, {
        x: Transform.posX[nearest],
        y: Transform.posY[nearest] + 3.1,
        z: Transform.posZ[nearest],
        duration: 3.2,
        color: '#9fe6b6',
        stackKey: `nota@${nearest}`,
      });
    }
  },
};

/** Liga o modo `interact` das quests de traçado. Chamar no bootstrap. */
export function initNota(state: State): void {
  setQuestVisitMode(state, 'interact');
}
