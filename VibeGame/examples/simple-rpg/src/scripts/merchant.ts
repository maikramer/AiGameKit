import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playSound } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'vibegame';
import { Transform } from 'vibegame';
import {
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
import { findPlayer } from '../game/player-query.ts';
import { NpcIdleAnimator } from '../game/npc-anims.ts';
import { getStoneCount, removeStone } from './inventory.ts';
import { getWoodCount, removeWood } from './wood.ts';
import { playerStats, RING_SPEED_MULT } from '../game/skills';

const TURN_SPEED = 6;
const MODEL_URL = '/assets/meshes/characters/npc_merchant_lod2.glb';
const IDLE_CLIP = 'idle';
// Mercador vivo: pregões (call), conversa, braços cruzados entre idles.
const idleVariety = new NpcIdleAnimator({
  idle: IDLE_CLIP,
  gestures: ['call', 'talk', 'foldarms'],
});

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
const RING_PRICE = 80; // one-time permanent +15% move speed (applied by PlayerStatsSystem)
const BOMB_PRICE = 20;

const ICON_BASE = '/assets/icons/';
/** Shop button → 2D icon file (in public/assets/icons). */
const ICONS: Record<string, string> = {
  potion: 'potion_health.png',
  sword: 'sword.png',
  antidote: 'item_antidote.png',
  ring: 'ring_magic.png',
  bomb: 'item_bomb.png',
  stone1: 'hud_stone.png',
  stone5: 'hud_stone.png',
  wood1: 'hud_wood.png',
};

let shopState: MonoBehaviourContext['state'] | null = null;

let group: THREE.Group | null = null;
let animator: GltfAnimator | null = null;
let yaw = 0;
let loadStarted = false;

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

function showTradePrompt(state: typeof shopState): void {
  if (promptShown || !merchantEid || !state) return;
  registerInteractionTarget(state, merchantEid, {
    label: 'Comerciar',
    key: 'K',
  });
  promptShown = true;
}

function hideTradePrompt(state: typeof shopState): void {
  if (!promptShown || !merchantEid || !state) return;
  unregisterInteractionTarget(state, merchantEid);
  promptShown = false;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
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
      animator?.play(IDLE_CLIP);
      idleVariety.start(animator);
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
  title.textContent = '\u2694 Loja do Osric';
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

  panel.appendChild(sectionHead('\u2014 Comprar \u2014'));
  shopButtons = [];
  shopButtons.push(
    makeButton(
      `Comprar poção (${POTION_PRICE}g) \u2014 +${POTION_HEAL} HP`,
      'potion',
      buyHealthPotion
    )
  );
  shopButtons.push(
    makeButton(
      `Melhorar espada (${SWORD_PRICE}g) \u2014 Nv.${playerStats.swordLevel + 1}`,
      'sword',
      buySwordUpgrade
    )
  );
  shopButtons.push(
    makeButton(
      `Comprar antídoto (${ANTIDOTE_PRICE}g) \u2014 cura +${ANTIDOTE_HEAL} HP`,
      'antidote',
      buyAntidote
    )
  );
  shopButtons.push(
    makeButton(
      `Comprar anel mágico (${RING_PRICE}g) \u2014 +15% velocidade`,
      'ring',
      buyRing
    )
  );
  shopButtons.push(
    makeButton(
      `Comprar bomba (${BOMB_PRICE}g) \u2014 arremessar com [B]`,
      'bomb',
      buyBomb
    )
  );

  panel.appendChild(sectionHead('\u2014 Vender \u2014'));
  shopButtons.push(
    makeButton(`Vender 1 pedra (${STONE1_PRICE}g)`, 'stone1', () =>
      sellStones(1, STONE1_PRICE)
    )
  );
  shopButtons.push(
    makeButton(`Vender 5 pedras (${STONE5_PRICE}g)`, 'stone5', () =>
      sellStones(5, STONE5_PRICE)
    )
  );
  shopButtons.push(
    makeButton(`Vender 1 madeira (${WOOD1_PRICE}g)`, 'wood1', () =>
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
  footer.textContent =
    'W/S navegar \u00b7 Enter selecionar \u00b7 L/ESC fechar';
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
    statsLabel.textContent = `Ouro: ${gold}   |   HP: ${hp}/${hpMax}   |   Pedras: ${stones}   |   Madeira: ${wood}`;
  }

  for (const btn of shopButtons) {
    switch (btn.dataset.action) {
      case 'potion':
        btn.disabled = gold < POTION_PRICE;
        setButtonLabel(btn, `Poção — ${POTION_PRICE}g  (tem ${ownedPotion})`);
        break;
      case 'sword':
        btn.disabled = gold < SWORD_PRICE;
        setButtonLabel(
          btn,
          `Melhorar espada (${SWORD_PRICE}g) \u2014 Nv.${playerStats.swordLevel + 1}`
        );
        break;
      case 'antidote':
        btn.disabled = gold < ANTIDOTE_PRICE;
        setButtonLabel(
          btn,
          `Antídoto — ${ANTIDOTE_PRICE}g  (tem ${ownedAntidote})`
        );
        break;
      case 'ring':
        btn.disabled = playerStats.ringOwned || gold < RING_PRICE;
        setButtonLabel(
          btn,
          playerStats.ringOwned
            ? 'Anel mágico \u2014 já tem (+15% velocidade)'
            : `Comprar anel mágico (${RING_PRICE}g) \u2014 +15% velocidade`
        );
        break;
      case 'bomb':
        btn.disabled = gold < BOMB_PRICE;
        setButtonLabel(btn, `Bomba — ${BOMB_PRICE}g  (tem ${ownedBomb})`);
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
    showShopError('Ouro insuficiente!');
    return;
  }
  // Potions go into the bag; use later with [1] (see game/consumables.ts).
  if (shopState) addItem(shopState, activePlayer, 'potion', 1);
  playSound('buy');
  refreshShopDisplay();
}

function buySwordUpgrade(): void {
  if (!spendGold(SWORD_PRICE)) {
    showShopError('Ouro insuficiente!');
    return;
  }
  // Sword upgrades raise the player's attack damage: PlayerStatsSystem folds
  // swordLevel into playerStats.attackBonus, which bombs.ts adds to blast damage.
  playerStats.swordLevel++;
  playSound('buy');
  refreshShopDisplay();
}

function buyAntidote(): void {
  if (!spendGold(ANTIDOTE_PRICE)) {
    showShopError('Ouro insuficiente!');
    return;
  }
  // Antidote goes into the bag; use later with [2] (see game/consumables.ts).
  if (shopState) addItem(shopState, activePlayer, 'antidote', 1);
  playSound('buy');
  refreshShopDisplay();
}

function buyRing(): void {
  if (playerStats.ringOwned) {
    showShopError('Você já tem este item!');
    return;
  }
  if (!spendGold(RING_PRICE)) {
    showShopError('Ouro insuficiente!');
    return;
  }
  // Flag only — PlayerStatsSystem applies RING_SPEED_MULT to
  // PlayerController.speed each frame. This avoids the old compounding bug
  // where save/load reset ringOwned=false and let the player re-buy and
  // re-multiply the speed.
  playerStats.ringOwned = true;
  playSound('buy');
  refreshShopDisplay();
}

function buyBomb(): void {
  if (!spendGold(BOMB_PRICE)) {
    showShopError('Ouro insuficiente!');
    return;
  }
  // Bomb goes into the bag; throw it later with [B] (see BombSystem).
  if (shopState) addItem(shopState, activePlayer, 'bomb', 1);
  playSound('buy');
  refreshShopDisplay();
}

function sellStones(amount: number, goldGain: number): void {
  if (!removeStone(amount)) {
    showShopError('Pedras insuficientes!');
    return;
  }
  addGold(goldGain);
  refreshShopDisplay();
}

function sellWood(amount: number, goldGain: number): void {
  if (!removeWood(amount)) {
    showShopError('Madeira insuficiente!');
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
  idleVariety.update(ctx.deltaTime, animator);

  const x = Transform.posX[eid];
  const y = Transform.posY[eid];
  const z = Transform.posZ[eid];

  const player = findPlayer(ctx.state);
  const dx = player ? Transform.posX[player] - x : 0;
  const dz = player ? Transform.posZ[player] - z : 0;
  const distSq = dx * dx + dz * dz;

  const near = player !== 0 && distSq < FACE_RANGE_SQ;
  const targetYaw = near ? Math.atan2(dx, dz) : 0;
  const err = Math.atan2(Math.sin(targetYaw - yaw), Math.cos(targetYaw - yaw));
  const maxTurn = TURN_SPEED * ctx.deltaTime;
  yaw += Math.min(maxTurn, Math.max(-maxTurn, err));

  // Entity place owns world Y (incl. hut floor y-offset).
  group.position.set(x, y, z);
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
