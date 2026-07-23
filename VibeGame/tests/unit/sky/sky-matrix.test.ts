import { describe, expect, it } from 'bun:test';
import { DefaultPlugins } from 'vibegame/defaults';
import type { Parser, ParserParams, Plugin, Recipe } from 'vibegame';
import {
  EquirectSky,
  getEquirectSkyUrl,
  setEquirectSkyUrl,
} from '../../../src/plugins/sky/components';

function findSkyPlugin(): Plugin | undefined {
  return DefaultPlugins.find((p: Plugin) =>
    p.recipes?.some((r: Recipe) => r.name === 'EquirectSky')
  );
}

function makeElement(
  tagName: string,
  attributes: Record<string, string>
): ParserParams {
  return {
    entity: 0,
    element: { tagName, attributes, children: [] },
  } as unknown as ParserParams;
}

const parser = findSkyPlugin()!.config!.parsers!.EquirectSky as Parser;
const EID = 7;

describe('Sky matrix — rotation-deg parsing', () => {
  for (let deg = -90; deg <= 89; deg++) {
    it(`rotation-deg=${deg} is stored on component`, () => {
      EquirectSky.rotationDeg[EID] = 0;
      const params = makeElement('equirectsky', {
        url: '/sky.hdr',
        'rotation-deg': String(deg),
      });
      params.entity = EID;
      parser(params);
      expect(EquirectSky.rotationDeg[EID]).toBe(deg);
    });
  }
});

describe('Sky matrix — set-background truthy values', () => {
  const truthy = ['1', 'true', 'TRUE', 'yes', 'Yes', 'YES'];

  for (const val of truthy) {
    it(`set-background="${val}" → setBackground=1`, () => {
      const params = makeElement('equirectsky', {
        url: '/sky.hdr',
        'set-background': val,
      });
      params.entity = EID;
      parser(params);
      expect(EquirectSky.setBackground[EID]).toBe(1);
    });
  }
});

describe('Sky matrix — set-background falsy values', () => {
  const falsy = ['0', 'false', 'FALSE', 'no', 'No', 'NO', 'off'];

  for (const val of falsy) {
    it(`set-background="${val}" → setBackground=0`, () => {
      const params = makeElement('equirectsky', {
        url: '/sky.hdr',
        'set-background': val,
      });
      params.entity = EID;
      parser(params);
      expect(EquirectSky.setBackground[EID]).toBe(0);
    });
  }
});

describe('Sky matrix — environment-intensity', () => {
  for (let intensity = 0; intensity <= 19; intensity++) {
    it(`environment-intensity=${intensity}`, () => {
      const params = makeElement('equirectsky', {
        url: '/sky.hdr',
        'environment-intensity': String(intensity * 0.5),
      });
      params.entity = EID;
      parser(params);
      expect(EquirectSky.environmentIntensity[EID]).toBe(intensity * 0.5);
    });
  }
});

describe('Sky matrix — url side map', () => {
  for (let i = 0; i < 15; i++) {
    it(`setEquirectSkyUrl stores path #${i}`, () => {
      const url = `/assets/skies/sky-${i}.png`;
      setEquirectSkyUrl(EID + i, url);
      expect(getEquirectSkyUrl(EID + i)).toBe(url);
    });
  }
});

describe('Sky matrix — applied latch on valid url', () => {
  for (let i = 0; i < 10; i++) {
    it(`valid url clears applied latch run ${i}`, () => {
      EquirectSky.applied[EID] = 1;
      const params = makeElement('equirectsky', {
        url: `/env/sky-${i}.hdr`,
      });
      params.entity = EID;
      parser(params);
      expect(EquirectSky.applied[EID]).toBe(0);
    });
  }
});
