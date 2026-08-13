// Plaza notice board: F opens a parchment of city-watch bounties. Kill/collect
// quests registered with npc "notice_board" show up here. Completed bounties
// can be taken again — the board is the repeatable loop between biome quests.
import {
  Transform,
  isKeyDown,
  registerInteractionTarget,
  unregisterInteractionTarget,
  getAllQuestDefs,
  getQuestIndex,
  QuestState,
  setTrackedQuest,
  setInputMovementSuppressed,
  playSound,
} from 'vibegame';
import type { MonoBehaviourContext, QuestDef, State } from 'vibegame';
import { isGamePaused, setGameModal } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { showToast } from '../../../shared/src/ui';

const BOARD_NPC = 'notice_board';
const READ_RANGE_SQ = 4.5 * 4.5;
const CLOSE_RANGE_SQ = 6 * 6;
const MODAL_ID = 'notice-board';

const TARGET_LABEL: Record<string, string> = {
  wolf: 'lobos',
  bandit: 'bandidos',
  goblin: 'goblins',
  wood: 'toros',
  stone: 'pedras',
};

const BUTTON_BASE =
  'display:block;width:100%;padding:10px 12px;margin:6px 0;box-sizing:border-box;' +
  'background:rgba(40,30,20,0.9);color:#e8d8b0;border:1px solid #5a4a30;' +
  'border-radius:4px;font:15px Georgia,serif;text-align:left;cursor:pointer;';
const BUTTON_FOCUS =
  'border:2px solid #ffd700;box-shadow:0 0 12px rgba(215,180,80,0.35);';
const BUTTON_DISABLED = 'opacity:0.45;cursor:not-allowed;';

let boardEid = 0;
let promptShown = false;
let panelOpen = false;
let panel: HTMLDivElement | null = null;
let listHost: HTMLDivElement | null = null;
let buttons: HTMLButtonElement[] = [];
let focusedIndex = 0;
let fPressed = false;
let navUpPressed = false;
let navDownPressed = false;
let enterPressed = false;
let closePressed = false;
let activeState: State | null = null;

function boardQuests(state: State): readonly QuestDef[] {
  return getAllQuestDefs(state).filter((def) => def.npc === BOARD_NPC);
}

function objectiveLine(def: QuestDef): string {
  const noun = TARGET_LABEL[def.objective.target] ?? def.objective.target;
  const verb = def.objective.type === 'collect' ? 'Recolher' : 'Abater';
  return `${verb} ${def.objective.count} ${noun}`;
}

function rewardLine(def: QuestDef): string {
  const gold = def.rewards?.gold ?? 0;
  const xp = def.rewards?.xp ?? 0;
  return `${gold}g · ${xp} XP`;
}

type BountyKind = 'available' | 'active' | 'done';

function bountyKind(state: State, def: QuestDef): BountyKind {
  const idx = getQuestIndex(state, def.id);
  if (idx < 0) return 'available';
  if (QuestState.active[idx] === 1) return 'active';
  if (QuestState.completed[idx] === 1) return 'done';
  return 'available';
}

function statusLine(state: State, def: QuestDef): string {
  const idx = getQuestIndex(state, def.id);
  const kind = bountyKind(state, def);
  if (kind === 'active' && idx >= 0) {
    const goal = Math.max(1, def.objective.count);
    return `Em curso (${QuestState.progress[idx]}/${goal})`;
  }
  if (kind === 'done') return 'Concluída — aceitar de novo';
  return 'Disponível';
}

function showPrompt(state: State): void {
  if (promptShown || !boardEid) return;
  registerInteractionTarget(state, boardEid, {
    label: 'Ler avisos',
    key: 'F',
  });
  promptShown = true;
}

function hidePrompt(state: State): void {
  if (!promptShown || !boardEid) return;
  unregisterInteractionTarget(state, boardEid);
  promptShown = false;
}

function takeBounty(state: State, def: QuestDef): void {
  const idx = getQuestIndex(state, def.id);
  if (idx < 0) return;
  if (QuestState.active[idx] === 1) return;
  QuestState.completed[idx] = 0;
  QuestState.active[idx] = 1;
  QuestState.progress[idx] = 0;
  setTrackedQuest(state, def.id);
  playSound('buy');
  showToast(`${def.title} aceite. ${objectiveLine(def)}.`, {
    color: '#e8d8b0',
    borderColor: '#c8a04a',
    background: 'rgba(20,15,10,0.95)',
    durationMs: 2600,
  });
}

function styleButton(btn: HTMLButtonElement, focused: boolean): void {
  let css = BUTTON_BASE;
  if (btn.disabled) css += BUTTON_DISABLED;
  else if (focused) css += BUTTON_FOCUS;
  btn.style.cssText = css;
}

function applyFocus(): void {
  for (let i = 0; i < buttons.length; i++) {
    styleButton(buttons[i], i === focusedIndex);
  }
}

function rebuildList(state: State): void {
  if (!listHost) return;
  listHost.replaceChildren();
  buttons = [];
  const quests = boardQuests(state);
  for (const def of quests) {
    const kind = bountyKind(state, def);
    const btn = document.createElement('button');
    btn.disabled = kind === 'active';
    btn.textContent = `${def.title}\n${objectiveLine(def)} · ${rewardLine(def)}\n${statusLine(state, def)}`;
    btn.style.whiteSpace = 'pre-line';
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      takeBounty(state, def);
      closeBoard();
    });
    btn.addEventListener('mouseenter', () => {
      const idx = buttons.indexOf(btn);
      if (idx >= 0 && !btn.disabled) {
        focusedIndex = idx;
        applyFocus();
      }
    });
    buttons.push(btn);
    listHost.appendChild(btn);
  }
  focusedIndex = Math.max(
    0,
    buttons.findIndex((b) => !b.disabled)
  );
  if (focusedIndex < 0) focusedIndex = 0;
  applyFocus();
}

function createPanel(): void {
  const root = document.createElement('div');
  root.id = 'notice-board-panel';
  root.style.cssText =
    'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);' +
    'width:420px;max-height:82vh;overflow-y:auto;box-sizing:border-box;' +
    'background:rgba(20,15,10,0.96);border:2px solid #c8a04a;border-radius:8px;' +
    'padding:18px 20px;z-index:1000;font-family:Georgia,serif;color:#e8d8b0;' +
    'box-shadow:0 0 40px rgba(0,0,0,0.85);display:none;';

  const header = document.createElement('div');
  header.style.cssText =
    'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';
  const title = document.createElement('div');
  title.textContent = 'Quadro de avisos';
  title.style.cssText =
    'font-size:20px;font-weight:bold;color:#c8a04a;letter-spacing:1px;';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '\u2715';
  closeBtn.style.cssText =
    'background:none;border:none;color:#c8a04a;font-size:18px;cursor:pointer;padding:0 4px;';
  closeBtn.addEventListener('click', () => closeBoard());
  header.appendChild(title);
  header.appendChild(closeBtn);
  root.appendChild(header);

  const blurb = document.createElement('div');
  blurb.textContent =
    'A guarda cola recompensas. Cumpre, cobra, e o aviso volta ao prego.';
  blurb.style.cssText =
    'font-size:13px;color:#b8a888;margin-bottom:10px;font-style:italic;';
  root.appendChild(blurb);

  listHost = document.createElement('div');
  root.appendChild(listHost);

  const hint = document.createElement('div');
  hint.textContent = 'W/S navegar · Enter aceitar · F/ESC fechar';
  hint.style.cssText =
    'margin-top:12px;padding-top:10px;border-top:1px solid rgba(200,160,74,0.3);' +
    'font-size:12px;color:#8a7a5a;text-align:center;';
  root.appendChild(hint);

  document.body.appendChild(root);
  panel = root;
}

function openBoard(state: State): void {
  if (panelOpen) return;
  panelOpen = true;
  setGameModal(MODAL_ID, true);
  setInputMovementSuppressed(true);
  hidePrompt(state);
  if (!panel) createPanel();
  rebuildList(state);
  if (panel) panel.style.display = 'block';
  playSound('shop-open');
  closePressed = true;
  fPressed = true;
}

function closeBoard(): void {
  if (!panelOpen) return;
  panelOpen = false;
  setGameModal(MODAL_ID, false);
  setInputMovementSuppressed(false);
  if (activeState) showPrompt(activeState);
  if (panel) panel.style.display = 'none';
}

function navigate(direction: number): void {
  const n = buttons.length;
  if (n === 0) return;
  let idx = focusedIndex;
  for (let step = 0; step < n; step++) {
    idx = (idx + direction + n) % n;
    if (!buttons[idx].disabled) {
      focusedIndex = idx;
      applyFocus();
      return;
    }
  }
}

function handleKeys(): void {
  const up = isKeyDown('KeyW') || isKeyDown('ArrowUp');
  if (up && !navUpPressed) navigate(-1);
  navUpPressed = up;

  const down = isKeyDown('KeyS') || isKeyDown('ArrowDown');
  if (down && !navDownPressed) navigate(1);
  navDownPressed = down;

  const confirm = isKeyDown('KeyJ') || isKeyDown('Enter');
  if (confirm && !enterPressed) {
    const btn = buttons[focusedIndex];
    if (btn && !btn.disabled) btn.click();
  }
  enterPressed = confirm;

  const close = isKeyDown('Escape') || isKeyDown('KeyL') || isKeyDown('KeyF');
  if (close && !closePressed) closeBoard();
  closePressed = close;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  boardEid = ctx.entity;
  activeState = ctx.state;
  showPrompt(ctx.state);
}

export function update(ctx: MonoBehaviourContext): void {
  activeState = ctx.state;
  if (isGamePaused() && !panelOpen) return;

  const player = findPlayer(ctx.state);
  const eid = ctx.entity;
  const dx = player ? Transform.posX[player] - Transform.posX[eid] : 0;
  const dz = player ? Transform.posZ[player] - Transform.posZ[eid] : 0;
  const distSq = dx * dx + dz * dz;

  if (panelOpen) {
    handleKeys();
    if (distSq > CLOSE_RANGE_SQ) closeBoard();
    return;
  }

  const f = isKeyDown('KeyF');
  if (f && !fPressed && player && distSq < READ_RANGE_SQ) {
    openBoard(ctx.state);
  }
  fPressed = f;
}
