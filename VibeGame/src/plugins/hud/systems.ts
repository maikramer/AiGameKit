import { hasComponent } from 'bitecs';
import * as THREE from 'three';
import {
  Block,
  Text,
  update as meshUiUpdate,
} from 'three-mesh-ui/build/three-mesh-ui.module.js';
import { defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { HudPanel } from './components';
import { getStringAt } from './context';

const hudQuery = defineQuery([HudPanel, Transform]);

const blockByEntity = new WeakMap<State, Map<number, Block>>();

function getBlocks(state: State): Map<number, Block> {
  let m = blockByEntity.get(state);
  if (!m) {
    m = new Map();
    blockByEntity.set(state, m);
  }
  return m;
}

export const HudBuildSystem: System = {
  group: 'setup',
  update: (state) => {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const blocks = getBlocks(state);
    for (const eid of hudQuery(state.world)) {
      if (HudPanel.built[eid]) continue;

      const block = new Block({
        width: HudPanel.width[eid],
        height: HudPanel.height[eid],
        backgroundOpacity: HudPanel.opacity[eid],
        backgroundColor: new THREE.Color(
          HudPanel.bgR[eid],
          HudPanel.bgG[eid],
          HudPanel.bgB[eid]
        ),
      });

      const text = new Text({
        content: getStringAt(state, HudPanel.textIndex[eid]),
        fontSize: 0.08,
      });
      block.add(text);

      scene.add(block);
      blocks.set(eid, block);
      HudPanel.built[eid] = 1;
    }
  },
};

export const HudSyncSystem: System = {
  group: 'draw',
  update: (state) => {
    if (state.headless) return;
    const blocks = getBlocks(state);
    for (const eid of hudQuery(state.world)) {
      const block = blocks.get(eid);
      if (!block) continue;

      const wx = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posX[eid]
        : Transform.posX[eid];
      const wy = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posY[eid]
        : Transform.posY[eid];
      const wz = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posZ[eid]
        : Transform.posZ[eid];
      block.position.set(wx, wy, wz);
    }
    meshUiUpdate();
  },
};
