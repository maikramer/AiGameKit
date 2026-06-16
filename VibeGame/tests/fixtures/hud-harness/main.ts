import { State } from '../../../src/core';
import {
  HudPlugin,
  getHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud';

async function bootstrap(): Promise<void> {
  const state = new State();
  state.registerPlugin(HudPlugin);
  await state.initializePlugins();

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

  const layer = getHudScreenLayer(state);
  console.log(
    '[hud-harness] layer attached:',
    layer.className,
    layer.parentElement?.tagName
  );
}

void bootstrap();
