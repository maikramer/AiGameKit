// Ambiência de água: loops espaciais (water-flow / water-lake) que ligam
// quando o jogador se aproxima de um rio/lago e desligam com histerese —
// mesmo contrato do crepitar da fogueira (campfire.ts). Um script só serve
// N entidades: cada uma adota a âncora cujo ponto está mais perto do seu
// spawn (sem atributos XML, sem estado por ficheiro).
import { Transform, playSoundAt } from 'aigamekit-vibegame';
import type { MonoBehaviourContext, SoundHandle } from 'aigamekit-vibegame';
import { isGamePaused } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';

interface WaterAnchor {
  x: number;
  z: number;
  sound: string;
  /** Raio de ligar (m). O desligar é start + histerese. */
  start: number;
}

const HYSTERESIS = 4;

const ANCHORS: WaterAnchor[] = [
  // Rio norte, junto à ponte (a travessia principal).
  { x: 4, z: 214, sound: 'water-flow', start: 16 },
  // Lagoa Grande do pântano, sob a ponte oeste.
  { x: -190, z: -6, sound: 'water-lake', start: 20 },
  // Lago do vale.
  { x: -80, z: 104, sound: 'water-lake', start: 20 },
  // Lagoa leste do pântano (ruína submersa).
  { x: -236, z: -84, sound: 'water-lake', start: 18 },
  // Oásis do deserto.
  { x: 140, z: 92, sound: 'water-lake', start: 16 },
  // Lago gelado dos picos.
  { x: 68, z: -184, sound: 'water-lake', start: 20 },
];

interface Ambience {
  anchor: WaterAnchor;
  handle: SoundHandle | null;
}

const ambiences = new Map<number, Ambience>();

function pickAnchor(x: number, z: number): WaterAnchor {
  let best = ANCHORS[0];
  let bestDist = Infinity;
  for (const a of ANCHORS) {
    const d = (a.x - x) * (a.x - x) + (a.z - z) * (a.z - z);
    if (d < bestDist) {
      bestDist = d;
      best = a;
    }
  }
  return best;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  const anchor = pickAnchor(ctx.transform.positionX, ctx.transform.positionZ);
  ambiences.set(ctx.entity, { anchor, handle: null });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  const amb = ambiences.get(ctx.entity);
  amb?.handle?.stop();
  ambiences.delete(ctx.entity);
}

export function update(ctx: MonoBehaviourContext): void {
  const amb = ambiences.get(ctx.entity);
  if (!amb || isGamePaused()) return;

  const eid = ctx.entity;
  const player = findPlayer(ctx.state);
  const dx = player ? Transform.posX[player] - Transform.posX[eid] : 0;
  const dz = player ? Transform.posZ[player] - Transform.posZ[eid] : 0;
  const distSq = dx * dx + dz * dz;

  const startSq = amb.anchor.start * amb.anchor.start;
  const stopSq = (amb.anchor.start + HYSTERESIS) ** 2;

  if (amb.handle) {
    if (!player || distSq > stopSq) {
      amb.handle.fadeOut(1.5);
      amb.handle = null;
    }
  } else if (player && distSq < startSq) {
    amb.handle = playSoundAt(
      amb.anchor.sound,
      Transform.posX[eid],
      Transform.posY[eid],
      Transform.posZ[eid],
      { originEid: eid }
    );
  }
}
