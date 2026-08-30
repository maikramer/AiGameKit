// Market stall: [J] opens a small DOM shop. Buying/selling goes through the
// engine economy (buyItem/sellItem) against a real merchant entity — gold and
// goods move atomically between vaults, prices live in /data/items.yaml.

import {
  GOLD_KIND,
  InventoryComponent,
  VaultComponent,
  addResource,
  addItem,
  buyItem,
  getDataRegistry,
  getInventory,
  getPrice,
  getResource,
  sellItem,
  registerInteractionTarget,
  t,
} from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';
import { showToast } from '../../../shared/src/ui';

const STALL_ENTITY_NAME = 'farm_market';
const STALL_RANGE = 4;
const MERCHANT_FLOAT_GOLD = 5000;
const MERCHANT_SEED_STOCK = 99;

export interface ShopEntry {
  itemId: string;
  name: string;
  icon: string;
  buy: number;
  sell: number;
}

/** Everything priced in /data/items.yaml, seeds first (they open the loop). */
export function shopCatalog(state: State): ShopEntry[] {
  const registry = getDataRegistry(state);
  return registry
    .all<{ id: string; name?: string; icon?: string; tags?: string[] }>('item')
    .filter((item) => item.tags?.includes('seed'))
    .map((item) => ({
      itemId: item.id,
      name: item.name ?? item.id,
      icon: item.icon ?? '🌱',
      buy: getPrice(state, item.id, 'buy'),
      sell: getPrice(state, item.id, 'sell'),
    }));
}

/** Produce ids the merchant buys (everything with a sell price > 0). */
function produceIds(state: State): string[] {
  const registry = getDataRegistry(state);
  return registry
    .all<{ id: string; tags?: string[] }>('item')
    .filter((item) => item.tags?.includes('produce'))
    .map((item) => item.id)
    .filter((id) => getPrice(state, id, 'sell') > 0);
}

let merchant = 0;

/**
 * The merchant is invisible economy state behind the stall mesh: a vault with
 * float gold and a stocked inventory, so buyItem/sellItem have a real
 * counterparty to trade against.
 */
export function ensureMerchant(state: State): number {
  if (merchant && state.exists(merchant)) return merchant;
  merchant = state.createEntity();
  state.addComponent(merchant, VaultComponent);
  state.addComponent(merchant, InventoryComponent);
  InventoryComponent.capacity[merchant] = 24;
  for (const entry of shopCatalog(state)) {
    addItem(state, merchant, entry.itemId, MERCHANT_SEED_STOCK);
  }
  // Vault resources are interned lazily; seeding gold through the same API
  // the transactions use keeps one source of truth.
  const startingGold = getResource(state, merchant, GOLD_KIND);
  if (startingGold < MERCHANT_FLOAT_GOLD) {
    addResource(state, merchant, GOLD_KIND, MERCHANT_FLOAT_GOLD - startingGold);
  }
  return merchant;
}

// ── DOM panel ────────────────────────────────────────────────────────────────

let panel: HTMLDivElement | null = null;

export function isShopOpen(): boolean {
  return panel !== null;
}

export function closeShop(): void {
  panel?.remove();
  panel = null;
}

function buyRow(
  state: State,
  player: number,
  entry: ShopEntry,
  onChange: () => void
): HTMLDivElement {
  const row = document.createElement('div');
  row.className = 'farm-shop-row';
  row.innerHTML = `<span class="farm-shop-item">${entry.icon} ${entry.name}</span>
    <span class="farm-shop-price">${entry.buy}g</span>`;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Buy';
  btn.disabled = getResource(state, player, GOLD_KIND) < entry.buy;
  btn.addEventListener('click', () => {
    const ok = buyItem(
      state,
      player,
      ensureMerchant(state),
      entry.itemId,
      1,
      entry.buy
    );
    if (ok) {
      showToast(t(state, 'farm.toast.bought').replace('{item}', entry.name), {
        durationMs: 1200,
      });
      onChange();
    }
  });
  row.appendChild(btn);
  return row;
}

function sellAll(
  state: State,
  player: number,
  onChange: () => void
): HTMLDivElement {
  const row = document.createElement('div');
  row.className = 'farm-shop-row';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Sell all produce';
  btn.addEventListener('click', () => {
    const merchantEid = ensureMerchant(state);
    let earned = 0;
    for (const itemId of produceIds(state)) {
      const qty =
        getInventory(state, player).find((s) => s.itemId === itemId)?.qty ?? 0;
      if (qty > 0) {
        const price = getPrice(state, itemId, 'sell');
        if (sellItem(state, player, merchantEid, itemId, qty, price)) {
          earned += price * qty;
        }
      }
    }
    if (earned > 0) {
      showToast(t(state, 'farm.toast.sold').replace('{gold}', String(earned)), {
        durationMs: 1600,
      });
    }
    onChange();
  });
  row.appendChild(btn);
  return row;
}

/** (Re)build the shop panel against the player's live gold and bag. */
export function openShop(state: State, player: number): void {
  closeShop();
  const layer =
    (document.querySelector(
      '.vibe-hud-screen-layer'
    ) as HTMLDivElement | null) ?? document.body;

  const root = document.createElement('div');
  root.className = 'farm-shop';
  root.innerHTML = `
    <style>
      .farm-shop {
        position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
        min-width:320px; padding:16px 18px; border-radius:14px; z-index:40;
        background:rgba(10,22,14,0.92); color:#e8f4dc;
        font:600 13px/1.5 system-ui,sans-serif; pointer-events:auto;
        border:1px solid rgba(180,230,160,0.25); box-shadow:0 12px 40px rgba(0,0,0,0.5);
      }
      .farm-shop h3 { margin:0 0 10px; font-size:15px; letter-spacing:0.4px; }
      .farm-shop-gold { color:#ffd166; margin-bottom:10px; }
      .farm-shop-row { display:flex; align-items:center; gap:10px; padding:4px 0; }
      .farm-shop-item { flex:1; }
      .farm-shop-price { color:#ffd166; min-width:48px; text-align:right; }
      .farm-shop button {
        padding:4px 12px; border-radius:8px; cursor:pointer;
        background:#2f5233; color:#e8f4dc; border:1px solid rgba(200,255,180,0.3);
        font:700 12px system-ui,sans-serif;
      }
      .farm-shop button:disabled { opacity:0.4; cursor:default; }
      .farm-shop-close { margin-top:10px; width:100%; }
    </style>`;

  const render = (): void => {
    root
      .querySelectorAll('.farm-shop-row, .farm-shop-gold')
      .forEach((el) => el.remove());
    const gold = document.createElement('div');
    gold.className = 'farm-shop-gold';
    gold.textContent = `💰 ${Math.floor(getResource(state, player, GOLD_KIND))}g`;
    root.appendChild(gold);
    for (const entry of shopCatalog(state)) {
      root.appendChild(buyRow(state, player, entry, render));
    }
    root.appendChild(sellAll(state, player, render));
  };

  const title = document.createElement('h3');
  title.textContent = '🥬 Market Stall';
  root.appendChild(title);
  render();

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'farm-shop-close';
  close.textContent = 'Close [J]';
  close.addEventListener('click', () => closeShop());
  root.appendChild(close);

  layer.appendChild(root);
  panel = root;
}

/** Prompt + entity wiring, called once from bootstrap. */
export function registerShop(state: State): void {
  ensureMerchant(state);
  const stall = state.getEntityByName(STALL_ENTITY_NAME);
  if (stall !== null) {
    registerInteractionTarget(state, stall, {
      label: t(state, 'farm.prompt.shop'),
      key: 'J',
      kind: 'shop',
      range: STALL_RANGE,
    });
  }
}
