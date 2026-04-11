import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { ParticleSystem } from '../../../src/plugins/particles/components';
import { ParticlesPlugin } from '../../../src/plugins/particles/plugin';

describe('Particles XML parsing', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('parses direct particle-emitter attributes including enum presets', () => {
    const state = new State();
    state.registerPlugin(ParticlesPlugin);

    const xml =
      '<root><particle-emitter preset="sparks" rate="12" lifetime="2.0" size="0.35"></particle-emitter></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(ParticleSystem.preset[entity]).toBe(
      ParticlesPlugin.config!.enums!.particlesEmitter.preset.sparks
    );
    expect(ParticleSystem.rate[entity]).toBeCloseTo(12);
    expect(ParticleSystem.lifetime[entity]).toBeCloseTo(2.0);
    expect(ParticleSystem.size[entity]).toBeCloseTo(0.35);
  });
});
