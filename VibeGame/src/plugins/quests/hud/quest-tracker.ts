import { defineQuery } from '../../../core';
import type { State, XMLValue } from '../../../core';
import {
  type HudWidget,
  type WidgetHandle,
  registerHudWidgetFactory,
} from '../../hud/screen-layer';
import { injectWidgetCss, readAttr } from '../../hud/widgets/shared';
import {
  formatWaypointDistance,
  getWaypoints,
  waypointDistance,
} from '../../hud/waypoints';
import { t } from '../../i18n/utils';
import { PlayerController } from '../../player';
import { Transform } from '../../transforms';
import { getAllActiveQuestDefs, resolveTrackedQuestId } from '../beacon';
import { QuestState } from '../components';
import { getQuestIndex } from '../registry';

/**
 * Always-on quest log corner: what the player accepted, how far along it is
 * and how far away the next marker is.
 *
 * The pause-menu Quests tab already lists everything, but a list you have to
 * pause the game to read can't answer "am I getting closer?" mid-run — which
 * is the only question that matters while walking.
 */

const WIDGET_TYPE = 'quest-tracker';
const WIDGET_ID = 'vibe:quest-tracker';

const TRACKER_CSS = `
.hud-quest-tracker{position:absolute;display:none;flex-direction:column;gap:6px;
min-width:186px;max-width:280px;padding:9px 12px;border-radius:11px;z-index:11;
background:linear-gradient(160deg,rgba(16,14,20,0.82),rgba(9,11,16,0.74));
border:1px solid rgba(196,148,72,0.3);backdrop-filter:blur(10px);
box-shadow:0 6px 18px rgba(0,0,0,0.34);pointer-events:none;
font-family:system-ui,Segoe UI,sans-serif;color:#e9eefb;}
.hud-quest-tracker[data-visible="true"]{display:flex;}
.hud-quest-tracker-heading{font:800 9px system-ui,sans-serif;letter-spacing:1.1px;
text-transform:uppercase;color:#c8a35a;}
.hud-quest-tracker-row{display:flex;flex-direction:column;gap:2px;}
.hud-quest-tracker-title{font:700 12.5px system-ui,sans-serif;color:#f2e7cf;}
.hud-quest-tracker-title[data-tracked="true"]{color:#ffd88a;}
.hud-quest-tracker-meta{display:flex;gap:8px;align-items:baseline;
font:700 10.5px system-ui,sans-serif;}
.hud-quest-tracker-progress{color:#9fe6b6;}
.hud-quest-tracker-progress[data-done="false"]{color:#a9bcdd;}
.hud-quest-tracker-dist{color:#ffcf7a;}
.hud-quest-tracker-empty{font:600 11px system-ui,sans-serif;color:#7c8aa8;}
`;

/** How many quests the corner lists before it stops growing. */
export const QUEST_TRACKER_MAX_ROWS = 4;

export interface QuestTrackerEntry {
  readonly questId: string;
  readonly title: string;
  readonly progress: number;
  readonly goal: number;
  readonly tracked: boolean;
  /** Metres to the nearest marker of this quest, or `null` when it has none. */
  readonly distance: number | null;
}

const playerQuery = defineQuery([PlayerController, Transform]);

/**
 * Build the tracker rows: active quests, the pinned one first, each with the
 * distance to its nearest own marker. Pure — shared by the draw and the tests.
 */
export function collectQuestTrackerEntries(
  state: State,
  limit: number = QUEST_TRACKER_MAX_ROWS
): QuestTrackerEntry[] {
  const players = playerQuery(state.world);
  const player = players[0];
  const px = player === undefined ? 0 : Transform.posX[player];
  const pz = player === undefined ? 0 : Transform.posZ[player];
  // The highlight mirrors what the arrow follows: the explicit pin, or the
  // active quest the beacon picked on its own.
  const tracked = resolveTrackedQuestId(state);

  const entries: QuestTrackerEntry[] = [];
  for (const { def, index } of getAllActiveQuestDefs(state)) {
    let distance: number | null = null;
    for (const wp of getWaypoints(state).values()) {
      if (wp.questIndex !== index) continue;
      const d = waypointDistance(wp, px, pz);
      if (distance === null || d < distance) distance = d;
    }
    entries.push({
      questId: def.id,
      title: def.title,
      progress: Math.min(
        Math.max(1, def.objective.count),
        QuestState.progress[index] ?? 0
      ),
      goal: Math.max(1, def.objective.count),
      tracked: tracked === def.id,
      distance,
    });
  }

  entries.sort((a, b) => Number(b.tracked) - Number(a.tracked));
  return entries.slice(0, Math.max(0, limit));
}

interface TrackerRow {
  root: HTMLDivElement;
  title: HTMLDivElement;
  progress: HTMLSpanElement;
  dist: HTMLSpanElement;
}

function makeRow(): TrackerRow {
  const root = document.createElement('div');
  root.className = 'hud-quest-tracker-row';
  const title = document.createElement('div');
  title.className = 'hud-quest-tracker-title';
  const meta = document.createElement('div');
  meta.className = 'hud-quest-tracker-meta';
  const progress = document.createElement('span');
  progress.className = 'hud-quest-tracker-progress';
  const dist = document.createElement('span');
  dist.className = 'hud-quest-tracker-dist';
  meta.append(progress, dist);
  root.append(title, meta);
  return { root, title, progress, dist };
}

function anchorCss(anchor: string): string {
  switch (anchor) {
    case 'top-left':
      return 'top:56px;left:18px;';
    case 'bottom-left':
      return 'bottom:120px;left:18px;';
    case 'bottom-right':
      return 'bottom:120px;right:18px;';
    case 'top-right':
    default:
      // Clears the 168 px minimap disc plus its 18 px gap.
      return 'top:206px;right:18px;';
  }
}

export function questTrackerFactory(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const anchor = readAttr(attributes, 'anchor') ?? 'top-right';
  const rawLimit = Number(readAttr(attributes, 'max-rows'));
  const limit =
    Number.isFinite(rawLimit) && rawLimit > 0
      ? Math.floor(rawLimit)
      : QUEST_TRACKER_MAX_ROWS;

  return {
    id: WIDGET_ID,
    mount(layer: HTMLDivElement): WidgetHandle {
      injectWidgetCss(TRACKER_CSS);

      const root = document.createElement('div');
      root.className = 'hud-quest-tracker';
      root.style.cssText = anchorCss(anchor);
      root.dataset.visible = 'false';

      const heading = document.createElement('div');
      heading.className = 'hud-quest-tracker-heading';
      root.appendChild(heading);

      const rows: TrackerRow[] = [];
      for (let i = 0; i < limit; i++) {
        const row = makeRow();
        row.root.style.display = 'none';
        root.appendChild(row.root);
        rows.push(row);
      }

      layer.appendChild(root);

      return {
        root,
        update(state: State): void {
          const entries = collectQuestTrackerEntries(state, limit);
          if (entries.length === 0) {
            root.dataset.visible = 'false';
            return;
          }
          heading.textContent = t(state, 'quests.tracker.title');
          for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            const entry = entries[i];
            if (!entry) {
              row.root.style.display = 'none';
              continue;
            }
            row.root.style.display = '';
            row.title.textContent = entry.tracked
              ? `▸ ${entry.title}`
              : entry.title;
            row.title.dataset.tracked = String(entry.tracked);
            row.progress.textContent = `${entry.progress}/${entry.goal}`;
            row.progress.dataset.done = String(entry.progress >= entry.goal);
            row.dist.textContent =
              entry.distance === null
                ? ''
                : formatWaypointDistance(entry.distance);
          }
          root.dataset.visible = 'true';
        },
        unmount(): void {
          root.remove();
        },
      };
    },
  };
}

registerHudWidgetFactory(WIDGET_TYPE, questTrackerFactory);

/** Quest ids are stable, so pinning survives save/load without extra state. */
export function questIsRegistered(state: State, questId: string): boolean {
  return getQuestIndex(state, questId) >= 0;
}
