import * as THREE from 'three';
import { defineQuery, loadGltfToSceneWithAnimator, playSound } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'vibegame';
import { Transform, PlayerController } from 'vibegame';
import {
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  isKeyDown,
  setInputMovementSuppressed,
  Health,
  addItem,
  getItemQty,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'vibegame';
import { getGold, spendGold, addGold } from '../game/economy.ts';
import { isGamePaused, setShopOpen } from '../game/pause.ts';
import { getStoneCount, removeStone } from './inventory.ts';
import { getWoodCount, removeWood } from './wood.ts';
import { heroStats, RING_SPEED_MULT } from '../game/skills';

const TURN_SPEED = 6;
const TERRAIN_LAYER = 0x0001;
const HUT_FLOOR_TOP = 0.2;
const MODEL_URL = '/assets/meshes/npc_merchant_rigged_animated.glb';
const IDLE_CLIP = 'Animator3D_BreatheIdle';

// Compared squared against dx*dx + dz*dz to avoid sqrt per frame.
const TALK_RANGE_SQ = 4.5 * 4.5;
const CLOSE_RANGE_SQ = 6 * 6;
const FACE_RANGE_SQ = 5 * 5;

const POTION_PRICE = 30;
const POTION_HEAL = 50;
const SWORD_PRICE = 100;
const STONE1_PRICE = 5;
const STONE5_PRICE = 25;
const WOOD1_PRICE = 8;

// Commerce-only items (2D shop icons via text2d, no 3D models).
const ANTIDOTE_PRICE = 25;
const ANTIDOTE_HEAL = 35;
const RING_PRICE = 80; // one-time permanent +15% move speed (applied by HeroStatsSystem)
const BOMB_PRICE = 20;

const ICON_BASE = '/assets/images/';
/** Shop button → 2D icon file (in public/assets/images). */
const ICONS: Record<string, string> = {
  potion: 'potion_health.png',
  sword: 'sword_hero.png',
  antidote: 'potion_antidote.png',
  ring: 'ring_magic.png',
  bomb: 'bomb.png',
  stone1: 'rock_mossy.png',
  stone5: 'rock_mossy.png',
  wood1: 'tree_oak.png',
};

const playerQuery = defineQuery([PlayerController]);
let cachedPlayer = 0;
let shopState: MonoBehaviourContext['state'] | null = null;

let group: THREE.Group | null = null;
let animator: GltfAnimator | null = null;
let footOffset = 0;
let yaw = 0;
let loadStarted = false;
const _box = new THREE.Box3();

let shopOpen = false;
let activePlayer = 0;
let shopPanel: HTMLDivElement | null = null;
let statsLabel: HTMLDivElement | null = null;
let errorLabel: HTMLDivElement | null = null;
let shopButtons: HTMLButtonElement[] = [];
let focusedIndex = 0;
let shopErrorTimeout: ReturnType<typeof setTimeout> | null = null;

// Edge-trigger debounce flags: isKeyDown stays true while held, so these
// convert it to a single-fire per keypress to prevent repeat triggers.
let kPressed = false;
let lPressed = false;
// Merchant entity + whether its "[K] Trade" interaction prompt is registered.
// The prompt is hidden while the shop is open (player is in range, but the
// panel already covers the screen).
let merchantEid = 0;
let promptShown = false;
let navUpPressed = false;
let navDownPressed = false;
let enterPressed = false;

const BUTTON_BASE_STYLE =
  'display:flex;align-items:center;gap:10px;width:100%;padding:8px 12px;margin:4px 0;box-sizing:border-box;' +
  'background:rgba(40,30,20,0.9);color:#e8d8b0;border:1px solid #5a4a30;' +
  'border-radius:4px;font:15px Georgia,serif;text-align:left;cursor:pointer;transition:background 0.12s;';
const ICON_STYLE =
  'width:34px;height:34px;flex:0 0 auto;object-fit:contain;' +
  'border-radius:4px;background:rgba(0,0,0,0.25);';
const BUTTON_FOCUS_STYLE =
  'border:2px solid #ffd700;box-shadow:0 0 12px rgba(255,215,0,0.4);';
const BUTTON_DISABLED_STYLE = 'opacity:0.4;cursor:not-allowed;';

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined)
    return cachedPlayer;
  cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
  return cachedPlayer;
}

function showTradePrompt(state: typeof shopState): void {
  if (promptShown || !merchantEid || !state) return;
  registerInteractionTarget(state, merchantEid, { label: 'Trade', key: 'K' });
  promptShown = true;
}

function hideTradePrompt(state: typeof shopState): void {
  if (!promptShown || !merchantEid || !state) return;
  unregisterInteractionTarget(state, merchantEid);
  promptShown = false;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx);
  merchantEid = ctx.entity;
  // Show the "[K] Trade" prompt; the HUD InteractionPrompt widget only renders
  // it while the player is within range, so K-to-open is discoverable.
  showTradePrompt(ctx.state);
  if (!loadStarted) {
    loadStarted = true;
    void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL, {
      crossfadeDuration: 0.3,
    }).then((result) => {
      group = result.group;
      animator = result.animator;
      _box.setFromObject(group);
      footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
      animator?.play(IDLE_CLIP);
    });
  }
}

function styleButton(btn: HTMLButtonElement, focused: boolean): void {
  let css = BUTTON_BASE_STYLE;
  if (btn.disabled) {
    css += BUTTON_DISABLED_STYLE;
  } else if (focused) {
    css += BUTTON_FOCUS_STYLE;
  }
  btn.style.cssText = css;
}

function applyFocus(): void {
  for (let i = 0; i < shopButtons.length; i++) {
    styleButton(shopButtons[i], i === focusedIndex);
  }
}

/** Update a button's text without clobbering its icon (label lives in a span). */
function setButtonLabel(btn: HTMLButtonElement, label: string): void {
  const span = btn.querySelector<HTMLSpanElement>('[data-role="label"]');
  if (span) span.textContent = label;
  else btn.textContent = label;
}

function makeButton(
  label: string,
  action: string,
  onClick: () => void
): HTMLButtonElement {
  const btn = document.createElement('button');
  const iconFile = ICONS[action];
  if (iconFile) {
    const img = document.createElement('img');
    img.src = ICON_BASE + iconFile;
    img.alt = '';
    img.style.cssText = ICON_STYLE;
    btn.appendChild(img);
  }
  const span = document.createElement('span');
  span.dataset.role = 'label';
  span.textContent = label;
  span.style.cssText = 'flex:1 1 auto;';
  btn.appendChild(span);
  btn.dataset.action = action;
  btn.addEventListener('click', onClick);
  btn.addEventListener('mouseenter', () => {
    const idx = shopButtons.indexOf(btn);
    if (idx >= 0 && !btn.disabled) {
      focusedIndex = idx;
      applyFocus();
    }
  });
  styleButton(btn, false);
  return btn;
}

function sectionHead(text: string): HTMLDivElement {
  const head = document.createElement('div');
  head.textContent = text;
  head.style.cssText =
    'color:#c8a04a;font-size:13px;letter-spacing:2px;margin:10px 0 2px;';
  return head;
}

function createShopPanel(): void {
  const panel = document.createElement('div');
  panel.id = 'merchant-shop';
  panel.style.cssText =
    'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);' +
    'width:380px;max-height:82vh;overflow-y:auto;box-sizing:border-box;' +
    'background:rgba(20,15,10,0.96);border:2px solid #c8a04a;border-radius:8px;' +
    'padding:18px 20px;z-index:1000;font-family:Georgia,serif;color:#e8d8b0;' +
    'box-shadow:0 0 40px rgba(0,0,0,0.85),0 0 0 1px rgba(200,160,74,0.25);' +
    'display:none;';

  const header = document.createElement('div');
  header.style.cssText =
    'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
  const title = document.createElement('div');
  title.textContent = '\u2694 Merchant Shop';
  title.style.cssText =
    'font-size:20px;font-weight:bold;color:#c8a04a;letter-spacing:1px;';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '\u2715';
  closeBtn.style.cssText =
    'background:none;border:none;color:#c8a04a;font-size:18px;cursor:pointer;padding:0 4px;';
  closeBtn.addEventListener('click', () => closeShop());
  header.appendChild(title);
  header.appendChild(closeBtn);
  panel.appendChild(header);

  statsLabel = document.createElement('div');
  statsLabel.style.cssText =
    'font-size:14px;color:#b8a888;margin-bottom:8px;padding:8px 10px;' +
    'background:rgba(0,0,0,0.35);border-radius:4px;border-left:3px solid #c8a04a;';
  panel.appendChild(statsLabel);

  panel.appendChild(sectionHead('\u2014 Buy \u2014'));
  shopButtons = [];
  shopButtons.push(
    makeButton(
      `Buy Health Potion (${POTION_PRICE}g) \u2014 +${POTION_HEAL} HP`,
      'potion',
      buyHealthPotion
    )
  );
  shopButtons.push(
    makeButton(
      `Buy Sword Upgrade (${SWORD_PRICE}g) \u2014 Lv.${heroStats.swordLevel + 1}`,
      'sword',
      buySwordUpgrade
    )
  );
  shopButtons.push(
    makeButton(
      `Buy Antidote (${ANTIDOTE_PRICE}g) \u2014 cure +${ANTIDOTE_HEAL} HP`,
      'antidote',
      buyAntidote
    )
  );
  shopButtons.push(
    makeButton(
      `Buy Magic Ring (${RING_PRICE}g) \u2014 +15% speed`,
      'ring',
      buyRing
    )
  );
  shopButtons.push(
    makeButton(
      `Buy Bomb (${BOMB_PRICE}g) \u2014 throw with [B]`,
      'bomb',
      buyBomb
    )
  );

  panel.appendChild(sectionHead('\u2014 Sell \u2014'));
  shopButtons.push(
    makeButton(`Sell 1 Stone (${STONE1_PRICE}g)`, 'stone1', () =>
      sellStones(1, STONE1_PRICE)
    )
  );
  shopButtons.push(
    makeButton(`Sell 5 Stones (${STONE5_PRICE}g)`, 'stone5', () =>
      sellStones(5, STONE5_PRICE)
    )
  );
  shopButtons.push(
    makeButton(`Sell 1 Wood (${WOOD1_PRICE}g)`, 'wood1', () =>
      sellWood(1, WOOD1_PRICE)
    )
  );

  for (const btn of shopButtons) panel.appendChild(btn);

  errorLabel = document.createElement('div');
  errorLabel.style.cssText =
    'min-height:20px;margin-top:10px;text-align:center;font-size:14px;color:#ff6b5a;' +
    'opacity:0;transition:opacity 0.2s;';
  panel.appendChild(errorLabel);

  const footer = document.createElement('div');
  footer.textContent = 'W/S navigate \u00b7 Enter select \u00b7 L/ESC close';
  footer.style.cssText =
    'margin-top:12px;padding-top:10px;border-top:1px solid rgba(200,160,74,0.3);' +
    'font-size:12px;color:#8a7a5a;text-align:center;';
  panel.appendChild(footer);

  document.body.appendChild(panel);
  shopPanel = panel;
}

function showShopError(message: string): void {
  if (!errorLabel) return;
  errorLabel.textContent = message;
  errorLabel.style.opacity = '1';
  if (shopErrorTimeout) clearTimeout(shopErrorTimeout);
  shopErrorTimeout = setTimeout(() => {
    if (errorLabel) errorLabel.style.opacity = '0';
  }, 1500);
  playSound('error');
}

function refreshShopDisplay(): void {
  const player = activePlayer;
  const gold = getGold();
  const hp = Math.round(Health.current[player] ?? 0);
  const hpMax = Math.round(Health.max[player] ?? 0);
  const stones = getStoneCount();
  const wood = getWoodCount();
  const ownedPotion = shopState ? getItemQty(shopState, player, 'potion') : 0;
  const ownedAntidote = shopState
    ? getItemQty(shopState, player, 'antidote')
    : 0;
  const ownedBomb = shopState ? getItemQty(shopState, player, 'bomb') : 0;

  if (statsLabel) {
    statsLabel.textContent = `Gold: ${gold}   |   HP: ${hp}/${hpMax}   |   Stones: ${stones}   |   Wood: ${wood}`;
  }

  for (const btn of shopButtons) {
    switch (btn.dataset.action) {
      case 'potion':
        btn.disabled = gold < POTION_PRICE;
        setButtonLabel(
          btn,
          `Health Potion — ${POTION_PRICE}g  (have ${ownedPotion})`
        );
        break;
      case 'sword':
        btn.disabled = gold < SWORD_PRICE;
        setButtonLabel(
          btn,
          `Buy Sword Upgrade (${SWORD_PRICE}g) \u2014 Lv.${heroStats.swordLevel + 1}`
        );
        break;
      case 'antidote':
        btn.disabled = gold < ANTIDOTE_PRICE;
        setButtonLabel(
          btn,
          `Antidote — ${ANTIDOTE_PRICE}g  (have ${ownedAntidote})`
        );
        break;
      case 'ring':
        btn.disabled = heroStats.ringOwned || gold < RING_PRICE;
        setButtonLabel(
          btn,
          heroStats.ringOwned
            ? 'Magic Ring \u2014 owned (+15% speed)'
            : `Buy Magic Ring (${RING_PRICE}g) \u2014 +15% speed`
        );
        break;
      case 'bomb':
        btn.disabled = gold < BOMB_PRICE;
        setButtonLabel(btn, `Bomb — ${BOMB_PRICE}g  (have ${ownedBomb})`);
        break;
      case 'stone1':
        btn.disabled = stones < 1;
        break;
      case 'stone5':
        btn.disabled = stones < 5;
        break;
      case 'wood1':
        btn.disabled = wood < 1;
        break;
    }
  }

  applyFocus();
}

function buyHealthPotion(): void {
  if (!spendGold(POTION_PRICE)) {
    showShopError('Not enough gold!');
    return;
  }
  // Potions go into the bag; use later with [1] (see game/consumables.ts).
  if (shopState) addItem(shopState, activePlayer, 'potion', 1);
  playSound('buy');
  refreshShopDisplay();
}

function buySwordUpgrade(): void {
  if (!spendGold(SWORD_PRICE)) {
    showShopError('Not enough gold!');
    return;
  }
  // Sword upgrades raise the hero's attack damage: HeroStatsSystem folds
  // swordLevel into heroStats.attackBonus, which bombs.ts adds to blast damage.
  heroStats.swordLevel++;
  playSound('buy');
  refreshShopDisplay();
}

function buyAntidote(): void {
  if (!spendGold(ANTIDOTE_PRICE)) {
    showShopError('Not enough gold!');
    return;
  }
  // Antidote goes into the bag; use later with [2] (see game/consumables.ts).
  if (shopState) addItem(shopState, activePlayer, 'antidote', 1);
  playSound('buy');
  refreshShopDisplay();
}

function buyRing(): void {
  if (heroStats.ringOwned) {
    showShopError('Already owned!');
    return;
  }
  if (!spendGold(RING_PRICE)) {
    showShopError('Not enough gold!');
    return;
  }
  // Flag only — HeroStatsSystem applies RING_SPEED_MULT to
  // PlayerController.speed each frame. This avoids the old compounding bug
  // where save/load reset ringOwned=false and let the player re-buy and
  // re-multiply the speed.
  heroStats.ringOwned = true;
  playSound('buy');
  refreshShopDisplay();
}

function buyBomb(): void {
  if (!spendGold(BOMB_PRICE)) {
    showShopError('Not enough gold!');
    return;
  }
  // Bomb goes into the bag; throw it later with [B] (see BombSystem).
  if (shopState) addItem(shopState, activePlayer, 'bomb', 1);
  playSound('buy');
  refreshShopDisplay();
}

function sellStones(amount: number, goldGain: number): void {
  if (!removeStone(amount)) {
    showShopError('Not enough stones!');
    return;
  }
  addGold(goldGain);
  refreshShopDisplay();
}

function sellWood(amount: number, goldGain: number): void {
  if (!removeWood(amount)) {
    showShopError('Not enough wood!');
    return;
  }
  addGold(goldGain);
  refreshShopDisplay();
}

function openShop(player: number): void {
  if (shopOpen) return;
  activePlayer = player;
  shopOpen = true;
  setShopOpen(true);
  setInputMovementSuppressed(true);
  hideTradePrompt(shopState);
  if (!shopPanel) createShopPanel();
  if (shopPanel) shopPanel.style.display = 'block';
  playSound('shop-open');

  focusedIndex = 0;
  for (let i = 0; i < shopButtons.length; i++) {
    if (!shopButtons[i].disabled) {
      focusedIndex = i;
      break;
    }
  }
  refreshShopDisplay();
}

function closeShop(): void {
  shopOpen = false;
  setShopOpen(false);
  setInputMovementSuppressed(false);
  showTradePrompt(shopState);
  if (shopPanel) shopPanel.style.display = 'none';
  if (shopErrorTimeout) {
    clearTimeout(shopErrorTimeout);
    shopErrorTimeout = null;
  }
  if (errorLabel) errorLabel.style.opacity = '0';
}

function navigateShop(direction: number): void {
  const n = shopButtons.length;
  if (n === 0) return;
  let idx = focusedIndex;
  for (let step = 0; step < n; step++) {
    idx = (idx + direction + n) % n;
    if (!shopButtons[idx].disabled) {
      focusedIndex = idx;
      applyFocus();
      return;
    }
  }
}

function handleShopKeys(): void {
  const up = isKeyDown('KeyW') || isKeyDown('ArrowUp');
  if (up && !navUpPressed) navigateShop(-1);
  navUpPressed = up;

  const down = isKeyDown('KeyS') || isKeyDown('ArrowDown');
  if (down && !navDownPressed) navigateShop(1);
  navDownPressed = down;

  // Buy/confirm with J (matches the world "[J]" interaction key); Enter also
  // works for keyboard users. Space is intentionally not bound.
  const confirm = isKeyDown('KeyJ') || isKeyDown('Enter');
  if (confirm && !enterPressed) {
    const btn = shopButtons[focusedIndex];
    if (btn && !btn.disabled) btn.click();
  }
  enterPressed = confirm;

  const close = isKeyDown('KeyL') || isKeyDown('Escape');
  if (close && !lPressed) closeShop();
  lPressed = close;
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  shopState = ctx.state;
  if (!group) return;
  // Frozen while the pause menu is open (don't open the shop on K, etc.).
  if (isGamePaused() && !shopOpen) return;
  animator?.update(ctx.deltaTime);

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const gy =
    getBvhSurfaceHeight(ctx.state, x, 500, z, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(ctx.state, x, z);

  const player = findPlayer(ctx);
  const dx = player ? Transform.posX[player] - x : 0;
  const dz = player ? Transform.posZ[player] - z : 0;
  const distSq = dx * dx + dz * dz;

  const near = player !== 0 && distSq < FACE_RANGE_SQ;
  const targetYaw = near ? Math.atan2(dx, dz) : 0;
  const err = Math.atan2(Math.sin(targetYaw - yaw), Math.cos(targetYaw - yaw));
  const maxTurn = TURN_SPEED * ctx.deltaTime;
  yaw += Math.min(maxTurn, Math.max(-maxTurn, err));

  group.position.set(x, gy + HUT_FLOOR_TOP + footOffset, z);
  group.rotation.set(0, yaw, 0);

  if (shopOpen) {
    handleShopKeys();
    refreshShopDisplay();
    if (distSq > CLOSE_RANGE_SQ) closeShop();
  } else {
    const k = isKeyDown('KeyK');
    if (k && !kPressed && distSq < TALK_RANGE_SQ) {
      openShop(player);
    }
    kPressed = k;
  }
}
