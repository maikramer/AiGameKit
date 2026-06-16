import { State } from '../../../src/core';
import {
  HudScreenUpdateSystem,
  createHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import {
  interactionPromptWidgetFactory,
  registerInteractionTarget,
} from '../../../src/plugins/hud/widgets/interaction-prompt';
import { createCompassWidget } from '../../../src/plugins/hud/widgets/compass';
import { createHealthBarWidget } from '../../../src/plugins/hud/widgets/health-bar';
import { createXpBarWidget } from '../../../src/plugins/hud/widgets/xp-bar';
import { createResourceChipWidget } from '../../../src/plugins/hud/widgets/resource-chip';
import { createMissionWidget } from '../../../src/plugins/hud/widgets/mission';
import { createTimerWidget } from '../../../src/plugins/hud/widgets/timer';
import { createBossBarWidget } from '../../../src/plugins/hud/widgets/boss-bar';
import { createControlsBarWidget } from '../../../src/plugins/hud/widgets/controls-bar';
import {
  createTabbedModalWidget,
  registerModalTab,
  openModal,
  closeModal,
  isModalOpen,
} from '../../../src/plugins/hud/widgets/tabbed-modal';
import {
  createOptionsTab,
  registerOptionDef,
} from '../../../src/plugins/hud/widgets/options-tab';
import { createSkillsTab } from '../../../src/plugins/hud/widgets/skills-tab';
import { createInventoryTab } from '../../../src/plugins/hud/widgets/inventory-tab';
import { getDataRegistry } from '../../../src/plugins/rpg-core';
import {
  InventoryComponent,
  InventoryPlugin,
  addItem,
} from '../../../src/plugins/rpg-inventory';
import { PauseCoordinatorPlugin } from '../../../src/plugins/rpg-pause';
import { RpgCoreEventsPlugin } from '../../../src/plugins/rpg-core';
import { Transform } from '../../../src/plugins/transforms';
import {
  Health,
  CombatPlugin,
  damageHealth,
} from '../../../src/plugins/combat';
import {
  ProgressionComponent,
  ProgressionPlugin,
  addXp,
} from '../../../src/plugins/rpg-progression';
import {
  VaultComponent,
  RpgVaultPlugin,
  addResource,
} from '../../../src/plugins/rpg-vault';
import { loadEngineDefaultDictionary } from '../../../src/plugins/i18n';
import { threeCameras } from '../../../src/plugins/rendering/utils';

interface MockDir {
  x: number;
  y: number;
  z: number;
}

const COMPASS_MOCK_CAMERA_EID = 4242;
const compassDir: MockDir = { x: 0, y: 0, z: 1 };

const compassMockCamera = {
  getWorldDirection(target: MockDir): MockDir {
    target.x = compassDir.x;
    target.y = compassDir.y;
    target.z = compassDir.z;
    return target;
  },
};

async function bootstrap(): Promise<void> {
  const state = new State();
  state.registerPlugin(RpgCoreEventsPlugin);
  state.registerPlugin(PauseCoordinatorPlugin);
  state.registerPlugin(CombatPlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(ProgressionPlugin);
  state.registerPlugin(InventoryPlugin);
  await state.initializePlugins();
  loadEngineDefaultDictionary(state);
  createHudScreenLayer(state);

  const probeWidget = {
    id: 'probe',
    mount: (layer: HTMLDivElement) => {
      const root = document.createElement('div');
      root.className = 'hud-probe-widget';
      root.style.cssText =
        'position:absolute;top:12px;left:12px;' +
        'padding:8px 14px;background:rgba(10,14,26,0.72);color:#e8eef8;' +
        'border-radius:8px;font:600 13px system-ui,sans-serif;' +
        'border:1px solid rgba(120,150,220,0.3);pointer-events:auto;';
      root.textContent = 'HudScreenLayer OK';
      layer.appendChild(root);
      return { root, unmount: () => root.remove() };
    },
  };
  registerHudWidget(state, probeWidget);

  const player = state.createEntity();
  const merchant = state.createEntity();
  Transform.posX[merchant] = 0;
  Transform.posZ[merchant] = 0;
  registerInteractionTarget(state, merchant, { label: 'Talk to Merchant' });

  registerHudWidget(
    state,
    interactionPromptWidgetFactory(
      { range: '4.5', key: 'K', 'player-eid': String(player) },
      state
    )
  );

  threeCameras.set(
    COMPASS_MOCK_CAMERA_EID,
    compassMockCamera as unknown as never
  );
  registerHudWidget(
    state,
    createCompassWidget(
      { fov: '1.7', north: '0', 'mark-color-north': '#ff8a6a' },
      state
    )
  );

  type HarnessWindow = typeof globalThis & {
    __promptHarness?: unknown;
    __compassSetDir?: (x: number, z: number) => void;
  };
  (window as unknown as HarnessWindow).__promptHarness = {
    player,
    merchant,
    setPlayerPos(x: number, z: number): void {
      Transform.posX[player] = x;
      Transform.posZ[player] = z;
    },
  };
  (window as unknown as HarnessWindow).__compassSetDir = (
    x: number,
    z: number
  ) => {
    compassDir.x = x;
    compassDir.z = z;
  };

  const layer = document.querySelector('.vibe-hud-screen-layer');
  console.log(
    '[hud-harness] layer attached:',
    layer?.className,
    layer?.parentElement?.tagName
  );

  const hero = state.createEntity();
  state.setEntityName('hero', hero);
  state.addComponent(hero, Health);
  Health.max[hero] = 100;
  Health.current[hero] = 100;
  state.addComponent(hero, ProgressionComponent);
  state.addComponent(hero, VaultComponent);
  state.addComponent(hero, Transform);

  const boss = state.createEntity();
  state.setEntityName('boss', boss);
  state.addComponent(boss, Health);
  Health.max[boss] = 200;
  Health.current[boss] = 200;
  state.addComponent(boss, Transform);
  Transform.posX[boss] = 200;

  registerHudWidget(
    state,
    createHealthBarWidget({ 'target-entity': 'hero', icon: '❤' }, state)
  );
  registerHudWidget(
    state,
    createXpBarWidget({ 'target-entity': 'hero' }, state)
  );
  registerHudWidget(
    state,
    createResourceChipWidget({ resource: 'gold', icon: '🪙' }, state)
  );
  registerHudWidget(
    state,
    createResourceChipWidget({ resource: 'wood', icon: '🪵' }, state)
  );
  registerHudWidget(
    state,
    createResourceChipWidget({ resource: 'stone', icon: '🪨' }, state)
  );
  registerHudWidget(state, createMissionWidget({}, state));
  registerHudWidget(state, createTimerWidget({}, state));
  registerHudWidget(
    state,
    createBossBarWidget({ range: '50', 'observer-entity': 'hero' }, state)
  );
  registerHudWidget(state, createControlsBarWidget({}, state));

  type HudWidgetsWindow = typeof globalThis & {
    __hudWidgets?: {
      damage: (amount: number) => void;
      addGold: (amount: number) => void;
      gainXp: (amount: number) => void;
      moveBoss: (x: number, z: number) => void;
    };
  };
  (window as unknown as HudWidgetsWindow).__hudWidgets = {
    damage(amount: number): void {
      damageHealth(hero, amount);
    },
    addGold(amount: number): void {
      addResource(state, hero, 'gold', amount);
    },
    gainXp(amount: number): void {
      addXp(state, hero, amount);
    },
    moveBoss(x: number, z: number): void {
      Transform.posX[boss] = x;
      Transform.posZ[boss] = z;
    },
  };

  state.addComponent(hero, InventoryComponent);
  InventoryComponent.capacity[hero] = 10;
  ProgressionComponent.unspentPoints[hero] = 3;
  const registry = getDataRegistry(state);
  registry.register('skill', 'vitality', {
    id: 'vitality',
    name: 'Vitality',
    description: '+10 max health per rank',
    maxRank: 5,
    cost: 1,
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'maxHealth', magnitude: 10, stackMode: 'stack' },
    },
  });
  registry.register('item', 'potion', {
    id: 'potion',
    name: 'Health Potion',
    icon: '🧪',
    maxStack: 99,
    tags: ['consumable'],
  });
  addItem(state, hero, 'potion', 3);
  registerOptionDef(state, {
    id: 'musicVolume',
    labelKey: 'Music Volume',
    type: 'cycle',
    values: ['Off', 'Low', 'Medium', 'High'],
    default: 'Medium',
  });

  registerModalTab(state, 'pause', {
    id: 'skills',
    labelKey: 'modal.tab.skills',
    build: (s) => createSkillsTab(s, { targetEntity: hero }),
  });
  registerModalTab(state, 'pause', {
    id: 'inventory',
    labelKey: 'modal.tab.inventory',
    build: (s) => createInventoryTab(s, { targetEntity: hero }),
  });
  registerModalTab(state, 'pause', {
    id: 'options',
    labelKey: 'modal.tab.options',
    build: (s) => createOptionsTab(s),
  });
  registerHudWidget(
    state,
    createTabbedModalWidget({ id: 'pause', 'target-entity': 'hero' }, state)
  );

  type ModalWindow = typeof globalThis & {
    __modalHarness?: {
      open: () => void;
      close: () => void;
      isOpen: () => boolean;
    };
  };
  (window as unknown as ModalWindow).__modalHarness = {
    open: () => openModal(state, 'pause'),
    close: () => closeModal(state, 'pause'),
    isOpen: () => isModalOpen(state, 'pause'),
  };

  const tick = (): void => {
    HudScreenUpdateSystem.update!(state);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

void bootstrap();
