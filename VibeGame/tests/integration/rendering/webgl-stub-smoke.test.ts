import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import * as THREE from 'three';
import { State } from 'vibegame';
import {
  MeshRenderer,
  RenderingPlugin,
  RenderContext,
  getRenderingContext,
  setCanvasElement,
} from 'vibegame/rendering';
import { TransformsPlugin } from 'vibegame/transforms';
import { installWebGLStub, uninstallWebGLStub } from '../../helpers/webgl-stub';

describe('WebGL stub smoke (rendering under Bun/JSDOM)', () => {
  let dom: JSDOM;

  beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser as typeof DOMParser;
    global.document = dom.window.document as unknown as any;
    global.window = dom.window as unknown as any;
    global.navigator = dom.window.navigator as unknown as any;
    global.MutationObserver = dom.window
      .MutationObserver as typeof MutationObserver;
    global.Node = dom.window.Node as typeof Node;
    global.HTMLElement = dom.window.HTMLElement as typeof HTMLElement;
    global.HTMLCanvasElement = dom.window
      .HTMLCanvasElement as typeof HTMLCanvasElement;
    global.requestAnimationFrame = ((cb: (time: number) => void) =>
      setTimeout(() => cb(0), 16)) as unknown as typeof requestAnimationFrame;
    global.cancelAnimationFrame =
      clearTimeout as unknown as typeof cancelAnimationFrame;
    global.performance = { now: () => Date.now() } as unknown as any;

    installWebGLStub();
  });

  afterEach(() => {
    uninstallWebGLStub();
  });

  it('constructs WebGLRenderer via SceneRenderSystem without throwing', async () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(RenderingPlugin);
    state.headless = false;

    const canvas = document.createElement('canvas');
    document.body.appendChild(canvas);

    const ctxEntity = state.createEntity();
    state.addComponent(ctxEntity, RenderContext);
    setCanvasElement(ctxEntity, canvas);

    const meshEntity = state.createEntity();
    state.addComponent(meshEntity, MeshRenderer);

    expect(() => state.step()).not.toThrow();

    // SceneRenderSystem.setup is async and dispatched fire-and-forget by the
    // scheduler; flush the microtask queue so createRenderer + PMREM resolve.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const context = getRenderingContext(state);
    expect(context.renderer).toBeInstanceOf(THREE.WebGLRenderer);

    state.dispose();
  });

  it('runs a full render() pass through the WebGL stub', () => {
    const renderer = new THREE.WebGLRenderer({
      canvas: document.createElement('canvas'),
      antialias: true,
    });
    renderer.setSize(64, 64, false);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 100);
    scene.add(
      new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial())
    );

    expect(() => renderer.render(scene, camera)).not.toThrow();
    renderer.dispose();
  });
});
