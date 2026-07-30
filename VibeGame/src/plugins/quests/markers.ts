import * as THREE from 'three';
import { defineQuery, defineSystem } from '../../core';
import type { State, System } from '../../core';
import { PlayerController } from '../player';
import { getScene } from '../rendering';
import { Transform } from '../transforms';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_TAKEN,
} from './components';
import { getQuestDefByIndex } from './registry';

/**
 * World-space "there is something here" markers floating over quest givers —
 * the `!` / `?` / `✓` badge every RPG uses to tell a decorative villager apart
 * from one worth walking up to.
 *
 * Drawn as depth-test-free sprites so a wall or a tree between the player and
 * the NPC can't swallow the badge; a marker you only see once you already have
 * line of sight is a marker you find by accident.
 */

export type QuestMarkerKind = 'available' | 'progress' | 'turnin' | 'none';

export interface QuestMarkerStyle {
  readonly glyph: string;
  readonly color: string;
  /** Relative sprite size — the "come back here" badges read louder. */
  readonly scale: number;
}

export const QUEST_MARKER_STYLES: Record<
  Exclude<QuestMarkerKind, 'none'>,
  QuestMarkerStyle
> = {
  available: { glyph: '!', color: '#ffd24a', scale: 1 },
  progress: { glyph: '?', color: '#8fa4c8', scale: 0.78 },
  turnin: { glyph: '✓', color: '#7fe0a0', scale: 1 },
};

/** Markers stop drawing past this distance (metres). */
export const QUEST_MARKER_MAX_DISTANCE = 70;
const MARKER_BASE_SCALE = 0.62;
const MARKER_BOB_AMPLITUDE = 0.09;
const MARKER_BOB_SPEED = 2.2;
export const DEFAULT_MARKER_HEIGHT = 2.6;

/**
 * Which badge (if any) an NPC should show.
 *
 * `turnin` is the state a player would otherwise never learn about: the
 * objective completes out in the field, and without a badge the quest giver
 * they should walk back to looks identical to one they already exhausted.
 */
export function resolveQuestMarkerKind(
  giverState: number,
  progress: number,
  goal: number,
  acknowledged: boolean
): QuestMarkerKind {
  if (giverState === QUEST_STATE_AVAILABLE) return 'available';
  if (giverState === QUEST_STATE_COMPLETED) {
    return acknowledged ? 'none' : 'turnin';
  }
  if (giverState === QUEST_STATE_TAKEN) {
    return progress >= goal ? 'turnin' : 'progress';
  }
  return 'none';
}

const textureCache = new Map<string, THREE.CanvasTexture>();

function markerTexture(glyph: string, color: string): THREE.CanvasTexture {
  const key = `${glyph}|${color}`;
  const cached = textureCache.get(key);
  if (cached) return cached;

  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const cx = size / 2;
    const cy = size / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, size * 0.38, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(12,14,22,0.78)';
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = color;
    ctx.stroke();

    ctx.fillStyle = color;
    ctx.font = "800 62px 'Trebuchet MS', system-ui, sans-serif";
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(glyph, cx, cy + 3);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  textureCache.set(key, texture);
  return texture;
}

interface MountedMarker {
  sprite: THREE.Sprite;
  kind: QuestMarkerKind;
}

const markersByState = new WeakMap<State, Map<number, MountedMarker>>();

function markerMap(state: State): Map<number, MountedMarker> {
  let m = markersByState.get(state);
  if (!m) {
    m = new Map();
    markersByState.set(state, m);
  }
  return m;
}

function disposeMarker(state: State, entry: MountedMarker): void {
  const scene = getScene(state);
  if (scene) scene.remove(entry.sprite);
  entry.sprite.material.dispose();
}

function makeSprite(kind: Exclude<QuestMarkerKind, 'none'>): THREE.Sprite {
  const style = QUEST_MARKER_STYLES[kind];
  const material = new THREE.SpriteMaterial({
    map: markerTexture(style.glyph, style.color),
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.renderOrder = 998;
  return sprite;
}

const giverQuery = defineQuery([QuestGiver, Transform]);
const playerQuery = defineQuery([PlayerController, Transform]);

/**
 * Creates / updates / removes the head badge for every quest giver. Runs in
 * `draw` so it reads the same transforms the renderer is about to use.
 */
export const QuestMarkerSystem: System = defineSystem({
  name: 'QuestMarkerSystem',
  group: 'draw',
  update(state: State): void {
    if (state.headless) return;
    if (typeof document === 'undefined') return;
    const scene = getScene(state);
    if (!scene) return;

    const markers = markerMap(state);
    const players = playerQuery(state.world);
    const player = players[0];
    const px = player === undefined ? 0 : Transform.posX[player];
    const pz = player === undefined ? 0 : Transform.posZ[player];
    const t = state.time.elapsed;

    for (const eid of giverQuery(state.world)) {
      const questIdx = QuestGiver.questId[eid];
      const def = getQuestDefByIndex(state, questIdx);
      const goal = Math.max(1, def?.objective.count ?? 1);
      const kind = resolveQuestMarkerKind(
        QuestGiver.state[eid],
        QuestState.progress[questIdx] ?? 0,
        goal,
        QuestGiver.acknowledged[eid] === 1
      );

      const dx = Transform.posX[eid] - px;
      const dz = Transform.posZ[eid] - pz;
      const dist = Math.hypot(dx, dz);
      const wanted =
        kind !== 'none' && dist <= QUEST_MARKER_MAX_DISTANCE ? kind : 'none';

      const existing = markers.get(eid);
      if (wanted === 'none') {
        if (existing) {
          disposeMarker(state, existing);
          markers.delete(eid);
        }
        continue;
      }

      let entry = existing;
      if (!entry || entry.kind !== wanted) {
        if (entry) disposeMarker(state, entry);
        entry = { sprite: makeSprite(wanted), kind: wanted };
        scene.add(entry.sprite);
        markers.set(eid, entry);
      }

      const height =
        QuestGiver.markerHeight[eid] > 0
          ? QuestGiver.markerHeight[eid]
          : DEFAULT_MARKER_HEIGHT;
      // Bob is offset per entity so a row of NPCs doesn't pulse in lockstep.
      const bob = Math.sin(t * MARKER_BOB_SPEED + eid) * MARKER_BOB_AMPLITUDE;
      entry.sprite.position.set(
        Transform.posX[eid],
        Transform.posY[eid] + height + bob,
        Transform.posZ[eid]
      );
      // Sprites shrink with distance; grow the far ones back so a badge across
      // the valley stays a readable glyph instead of one dim pixel.
      const growth = Math.min(3, Math.max(1, dist / 14));
      const scale =
        MARKER_BASE_SCALE * QUEST_MARKER_STYLES[wanted].scale * growth;
      entry.sprite.scale.set(scale, scale, scale);
    }

    for (const [eid, entry] of markers) {
      if (state.exists(eid)) continue;
      disposeMarker(state, entry);
      markers.delete(eid);
    }
  },

  dispose(state: State): void {
    const markers = markersByState.get(state);
    if (markers) {
      for (const entry of markers.values()) disposeMarker(state, entry);
      markers.clear();
    }
    markersByState.delete(state);
  },
});

/** Test/teardown helper: drop the shared glyph textures. */
export function disposeQuestMarkerTextures(): void {
  for (const texture of textureCache.values()) texture.dispose();
  textureCache.clear();
}
