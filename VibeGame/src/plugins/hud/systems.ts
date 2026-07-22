import * as THREE from 'three';
import { Container, reversePainterSortStable, Text } from '@pmndrs/uikit';
import { defineSystem, defineQueryLive, type State, type System } from '../../core';
import { getRenderingContext, getScene } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { HudPanel } from './components';
import { getStringAt } from './context';
import { I18nText } from '../i18n/components';

const hudQuery = defineQueryLive([HudPanel, Transform]);

const panelByEntity = new WeakMap<State, Map<number, Container>>();
const textByEntity = new WeakMap<State, Map<number, Text>>();
const textValueByEntity = new WeakMap<State, Map<number, string>>();
const panelLastUpdateAt = new WeakMap<Container, number>();
const rendererConfigured = new WeakSet<THREE.WebGLRenderer>();
const PANEL_LAYOUT_INTERVAL = 1 / 15;

function getPanels(state: State): Map<number, Container> {
  let m = panelByEntity.get(state);
  if (!m) {
    m = new Map();
    panelByEntity.set(state, m);
  }
  return m;
}

function getTexts(state: State): Map<number, Text> {
  let m = textByEntity.get(state);
  if (!m) {
    m = new Map();
    textByEntity.set(state, m);
  }
  return m;
}

function getTextValues(state: State): Map<number, string> {
  let m = textValueByEntity.get(state);
  if (!m) {
    m = new Map();
    textValueByEntity.set(state, m);
  }
  return m;
}

/** uikit needs sorted-transparency + local clipping on the renderer once. */
function ensureRendererConfigured(renderer: THREE.WebGLRenderer): void {
  if (rendererConfigured.has(renderer)) return;
  rendererConfigured.add(renderer);
  renderer.localClippingEnabled = true;
  renderer.setTransparentSort(reversePainterSortStable);
}

export const HudBuildSystem: System = defineSystem({
  name: 'HudBuildSystem',
  group: 'setup',
  update: (state) => {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const panels = getPanels(state);
    const texts = getTexts(state);
    const textValues = getTextValues(state);
    for (const eid of hudQuery(state.world)) {
      if (HudPanel.built[eid]) {
        if (state.hasComponent(eid, I18nText) && I18nText.resolved[eid]) {
          const text = texts.get(eid);
          const value = getStringAt(state, HudPanel.textIndex[eid]);
          if (text && textValues.get(eid) !== value) {
            text.setProperties({
              text: value,
            });
            textValues.set(eid, value);
            panels.get(eid)?.update(0);
          }
        }
        continue;
      }

      const renderer = getRenderingContext(state).renderer;
      if (renderer) ensureRendererConfigured(renderer);

      // pixelSize: 1 keeps width/height in plain world units (metres), matching
      // the previous three-mesh-ui convention (no px→world scaling).
      const panel = new Container({
        pixelSize: 1,
        width: HudPanel.width[eid],
        height: HudPanel.height[eid],
        backgroundColor: new THREE.Color(
          HudPanel.bgR[eid],
          HudPanel.bgG[eid],
          HudPanel.bgB[eid]
        ),
        // uikit has no separate background-alpha prop (unlike three-mesh-ui's
        // backgroundOpacity): `opacity` applies to the whole panel, background
        // and content together.
        opacity: HudPanel.opacity[eid],
        alignItems: 'center',
        justifyContent: 'center',
      });

      const initialText = getStringAt(state, HudPanel.textIndex[eid]);
      const text = new Text({
        text: initialText,
        fontSize: 0.08,
        color: 0xffffff,
      });
      panel.add(text);

      scene.add(panel);
      panels.set(eid, panel);
      texts.set(eid, text);
      textValues.set(eid, initialText);
      HudPanel.built[eid] = 1;
    }
  },
});

export const HudSyncSystem: System = defineSystem({
  name: 'HudSyncSystem',
  group: 'draw',
  update: (state) => {
    if (state.headless) return;
    const panels = getPanels(state);
    const dt = state.time.deltaTime || 0;
    const now = state.time.elapsed;
    for (const eid of hudQuery(state.world)) {
      const panel = panels.get(eid);
      if (!panel) continue;

      const wx = state.hasComponent(eid, WorldTransform)
        ? WorldTransform.posX[eid]
        : Transform.posX[eid];
      const wy = state.hasComponent(eid, WorldTransform)
        ? WorldTransform.posY[eid]
        : Transform.posY[eid];
      const wz = state.hasComponent(eid, WorldTransform)
        ? WorldTransform.posZ[eid]
        : Transform.posZ[eid];
      if (
        panel.position.x !== wx ||
        panel.position.y !== wy ||
        panel.position.z !== wz
      ) {
        panel.position.set(wx, wy, wz);
      }
      // uikit's per-instance update() drives its own layout/text re-flow;
      // static panels need only a modest cadence after initial/text updates.
      const lastUpdate = panelLastUpdateAt.get(panel) ?? -Infinity;
      if (now - lastUpdate >= PANEL_LAYOUT_INTERVAL) {
        panel.update(dt * 1000);
        panelLastUpdateAt.set(panel, now);
      }
    }
  },
});
