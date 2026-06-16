import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
} from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import type { XMLValue } from '../../../src/core';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  type HudWidget,
  type WidgetHandle,
  getHudScreenLayer,
} from '../../../src/plugins/hud/screen-layer';
import { threeCameras } from '../../../src/plugins/rendering/utils';
import {
  COMPASS_DEFAULT_FOV,
  COMPASS_DEFAULT_NORTH,
  COMPASS_DEFAULT_NORTH_COLOR,
  cameraAzimuth,
  cardinalAzimuths,
  createCompassWidget,
  markTransform,
  wrapAngle,
} from '../../../src/plugins/hud/widgets/compass';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window
    .HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window
    .HTMLDivElement as unknown as typeof HTMLDivElement;
});

const MOCK_CAMERA_EID = 99999;

interface MockCamera {
  getWorldDirection(target: { x: number; y: number; z: number }): {
    x: number;
    y: number;
    z: number;
  };
  setDir(x: number, z: number): void;
}

function makeMockCamera(dirX: number, dirZ: number): MockCamera {
  const dir = { x: dirX, y: 0, z: dirZ };
  return {
    getWorldDirection(target) {
      target.x = dir.x;
      target.y = dir.y;
      target.z = dir.z;
      return target;
    },
    setDir(x, z) {
      dir.x = x;
      dir.z = z;
    },
  };
}

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgb(${r}, ${g}, ${b})`;
}

function newState(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  return state;
}

function mountCompass(
  state: State,
  attrs: Record<string, XMLValue> = {}
): { widget: HudWidget; handle: WidgetHandle; root: HTMLElement } {
  const widget = createCompassWidget(attrs, state);
  const layer = getHudScreenLayer(state);
  const handle = widget.mount(layer, state);
  return { widget, handle, root: handle.root };
}

function setStripWidth(root: HTMLElement, width: number): void {
  Object.defineProperty(root, 'clientWidth', {
    configurable: true,
    value: width,
  });
}

describe('compass — pure math helpers', () => {
  it('cameraAzimuth matches atan2(x, z): +Z=0, +X=+π/2, -X=-π/2', () => {
    expect(cameraAzimuth(0, 1)).toBeCloseTo(0, 6);
    expect(cameraAzimuth(1, 0)).toBeCloseTo(Math.PI / 2, 6);
    expect(cameraAzimuth(-1, 0)).toBeCloseTo(-Math.PI / 2, 6);
  });

  it('cameraAzimuth for -Z is ±π', () => {
    const az = cameraAzimuth(0, -1);
    expect(Math.abs(az)).toBeCloseTo(Math.PI, 6);
  });

  it('wrapAngle folds into (-π, +π]', () => {
    expect(wrapAngle(0)).toBe(0);
    expect(wrapAngle(Math.PI * 2)).toBeCloseTo(0, 6);
    expect(wrapAngle(Math.PI * 3)).toBeCloseTo(Math.PI, 6);
    expect(wrapAngle(-Math.PI * 3)).toBeCloseTo(-Math.PI, 6);
  });

  it('cardinalAzimuths places N/E/S/W at north + k·π/2 (default north=0)', () => {
    const marks = cardinalAzimuths(0);
    const byLabel = new Map(marks.map((m) => [m.label, m.az]));
    expect(byLabel.get('N')).toBeCloseTo(0, 6);
    expect(byLabel.get('E')).toBeCloseTo(Math.PI / 2, 6);
    expect(byLabel.get('S')).toBeCloseTo(Math.PI, 6);
    expect(byLabel.get('W')).toBeCloseTo(-Math.PI / 2, 6);
    expect(byLabel.get('NE')).toBeCloseTo(Math.PI / 4, 6);
    expect(byLabel.get('NW')).toBeCloseTo(-Math.PI / 4, 6);
  });

  it('cardinalAzimuths respects a custom north offset', () => {
    const marks = cardinalAzimuths(Math.PI / 2);
    const north = marks.find((m) => m.label === 'N')!.az;
    expect(north).toBeCloseTo(Math.PI / 2, 6);
  });

  it('cardinalAzimuths marks N/E/S/W as major', () => {
    const marks = cardinalAzimuths(0);
    for (const label of ['N', 'E', 'S', 'W']) {
      expect(marks.find((m) => m.label === label)!.major).toBe(true);
    }
    for (const label of ['NE', 'SE', 'SW', 'NW']) {
      expect(marks.find((m) => m.label === label)!.major).toBe(false);
    }
  });

  it('markTransform centres a mark whose azimuth equals the camera heading', () => {
    const t = markTransform(0.5, 0.5, 1.7, 150);
    expect(t.visible).toBe(true);
    expect(t.translateX).toBeCloseTo(0, 6);
    expect(t.opacity).toBeCloseTo(1, 6);
  });

  it('markTransform hides marks further than fov from the heading', () => {
    const t = markTransform(0, Math.PI, 1.7, 150);
    expect(t.visible).toBe(false);
    expect(t.opacity).toBe(0);
  });

  it('markTransform places a mark at the right edge when offset equals fov', () => {
    const t = markTransform(1.7, 0, 1.7, 150);
    expect(t.visible).toBe(true);
    expect(t.translateX).toBeCloseTo(150, 6);
    expect(t.opacity).toBeCloseTo(0.25, 6);
  });
});

describe('compass — createCompassWidget attribute parsing', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('applies defaults when no attributes are given', () => {
    const { root } = mountCompass(state);
    const nMark = root.querySelector('[data-cardinal="N"]') as HTMLDivElement;
    expect(nMark).not.toBeNull();
    expect(nMark.style.color).toBe(hexToRgb(COMPASS_DEFAULT_NORTH_COLOR));
  });

  it('honours mark-color-north override', () => {
    const { root } = mountCompass(state, { 'mark-color-north': '#abcdef' });
    const nMark = root.querySelector('[data-cardinal="N"]') as HTMLDivElement;
    expect(nMark.style.color).toBe(hexToRgb('#abcdef'));
  });

  it('exposes default constants matching the spec (fov 1.7, north 0)', () => {
    expect(COMPASS_DEFAULT_FOV).toBe(1.7);
    expect(COMPASS_DEFAULT_NORTH).toBe(0);
  });

  it('widget id is "compass"', () => {
    const { widget } = mountCompass(state);
    expect(widget.id).toBe('compass');
  });
});

describe('compass — DOM structure', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('mounts a .vibe-compass strip with 8 cardinal marks and a centre tick', () => {
    const { root } = mountCompass(state);
    expect(root.className).toBe('vibe-compass');
    const marks = root.querySelectorAll('.vibe-compass-mark');
    expect(marks).toHaveLength(8);
    const labels = Array.from(marks).map((m) => m.textContent);
    expect(labels).toEqual(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']);
    expect(root.querySelectorAll('.vibe-compass-tick')).toHaveLength(1);
  });

  it('the strip lives inside the HudScreenLayer', () => {
    const { root } = mountCompass(state);
    const layer = getHudScreenLayer(state);
    expect(root.closest('.vibe-hud-screen-layer')).toBe(layer);
  });

  it('unmount removes the strip from the layer', () => {
    const { handle, root } = mountCompass(state);
    expect(root.parentElement).not.toBeNull();
    handle.unmount();
    expect(root.parentElement).toBeNull();
  });

  it('injects the scoped compass stylesheet once', () => {
    document.getElementById('vibe-compass-style')?.remove();
    mountCompass(state);
    expect(document.getElementById('vibe-compass-style')).not.toBeNull();
    const before = document.querySelectorAll('#vibe-compass-style').length;
    mountCompass(newState());
    const after = document.querySelectorAll('#vibe-compass-style').length;
    expect(after).toBe(before);
  });
});

describe('compass — update with camera azimuth', () => {
  let state: State;
  let camera: MockCamera;

  beforeEach(() => {
    state = newState();
    camera = makeMockCamera(0, 1);
    threeCameras.set(MOCK_CAMERA_EID, camera as unknown as never);
  });

  afterEach((): void => {
    threeCameras.delete(MOCK_CAMERA_EID);
  });

  function opacityOf(root: HTMLElement, label: string): number {
    const el = root.querySelector(
      `[data-cardinal="${label}"]`
    ) as HTMLDivElement;
    return Number(el.style.opacity);
  }

  it('N is centred (opacity 1) when the camera faces +Z (north=0)', () => {
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);
    camera.setDir(0, 1);
    handle.update!(state);
    expect(opacityOf(root, 'N')).toBeCloseTo(1, 6);
  });

  it('E is centred (opacity 1) when the camera rotates to face +X', () => {
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);
    camera.setDir(1, 0);
    handle.update!(state);
    expect(opacityOf(root, 'E')).toBeCloseTo(1, 6);
  });

  it('S is centred when the camera faces -Z', () => {
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);
    camera.setDir(0, -1);
    handle.update!(state);
    expect(opacityOf(root, 'S')).toBeCloseTo(1, 6);
  });

  it('W is centred when the camera faces -X', () => {
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);
    camera.setDir(-1, 0);
    handle.update!(state);
    expect(opacityOf(root, 'W')).toBeCloseTo(1, 6);
  });

  it('rotating +Z → +X moves the centred mark from N to E', () => {
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);

    camera.setDir(0, 1);
    handle.update!(state);
    const nOpacityAtZ = opacityOf(root, 'N');
    const eOpacityAtZ = opacityOf(root, 'E');
    expect(nOpacityAtZ).toBeCloseTo(1, 6);
    expect(eOpacityAtZ).toBeLessThan(1);

    camera.setDir(1, 0);
    handle.update!(state);
    const nOpacityAtX = opacityOf(root, 'N');
    const eOpacityAtX = opacityOf(root, 'E');
    expect(eOpacityAtX).toBeCloseTo(1, 6);
    expect(nOpacityAtX).toBeLessThan(1);
  });

  it('update is a no-op when no camera is registered', () => {
    threeCameras.delete(MOCK_CAMERA_EID);
    const { handle, root } = mountCompass(state);
    setStripWidth(root, 300);
    const before = (root.querySelector('[data-cardinal="N"]') as HTMLDivElement)
      .style.opacity;
    expect(() => handle.update!(state)).not.toThrow();
    const after = (root.querySelector('[data-cardinal="N"]') as HTMLDivElement)
      .style.opacity;
    expect(after).toBe(before);
  });

  it('update is a no-op before layout (clientWidth === 0)', () => {
    const { handle, root } = mountCompass(state);
    Object.defineProperty(root, 'clientWidth', {
      configurable: true,
      value: 0,
    });
    camera.setDir(0, 1);
    expect(() => handle.update!(state)).not.toThrow();
  });
});
