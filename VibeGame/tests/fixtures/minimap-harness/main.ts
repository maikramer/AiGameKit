import { State } from '../../../src/core';
import { HudPlugin, MinimapWidget, registerHudWidget } from '../../../src/plugins/hud';
import { Transform } from '../../../src/plugins/transforms';
import { PlayerController } from '../../../src/plugins/player';
import { FactionComponent, Health } from '../../../src/plugins/combat';
import { NavMeshAgent } from '../../../src/plugins/navmesh';
import { ResourceNode } from '../../../src/plugins/rpg-resource-node';

async function bootstrap(): Promise<void> {
  const state = new State();
  state.registerPlugin(HudPlugin);
  state.config.register({
    enums: {
      'resource-node': { kind: { wood: 0, stone: 1, ore: 2 } },
    },
  });
  await state.initializePlugins();

  const player = state.createEntity();
  state.addComponent(player, Transform);
  state.addComponent(player, PlayerController);

  function spawnEnemy(x: number, z: number, faction: number, hp: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, NavMeshAgent);
    state.addComponent(eid, Health, { current: hp, max: hp });
    state.addComponent(eid, FactionComponent, { tag: faction });
    Transform.posX[eid] = x;
    Transform.posZ[eid] = z;
    return eid;
  }

  function spawnResource(x: number, z: number, kind: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, ResourceNode, { kind });
    Transform.posX[eid] = x;
    Transform.posZ[eid] = z;
    return eid;
  }

  const enemy = spawnEnemy(10, 0, 1, 50);
  const boss = spawnEnemy(40, 0, 2, 400);
  void enemy;
  void boss;
  spawnResource(-6, 8, 0);
  spawnResource(7, -10, 1);

  registerHudWidget(
    state,
    new MinimapWidget({
      range: 60,
      size: 168,
      categories: new Set(['player', 'enemy', 'boss', 'merchant', 'wood', 'stone', 'neutral']),
      colors: {
        player: '#ffffff',
        enemy: '#ff4d4d',
        boss: '#c060ff',
        merchant: '#ffd24a',
        wood: '#6fdc6f',
        stone: '#b9b2a6',
        neutral: '#e8eef7',
      },
      radii: {
        player: 7,
        enemy: 2.6,
        boss: 4.5,
        merchant: 3.5,
        wood: 1.8,
        stone: 1.8,
        neutral: 2.2,
      },
      anchor: 'top-right',
    })
  );

  (window as unknown as { __minimapHarness: unknown }).__minimapHarness = {
    clearBlips(): void {
      Transform.posX[enemy] = 9999;
      Transform.posZ[enemy] = 9999;
      Transform.posX[boss] = 9999;
      Transform.posZ[boss] = 9999;
    },
  };

  let last = performance.now();
  const tick = (): void => {
    const now = performance.now();
    state.step(Math.min((now - last) / 1000, 0.1));
    last = now;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

void bootstrap();
