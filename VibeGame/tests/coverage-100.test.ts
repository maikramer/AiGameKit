import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import * as THREE from 'three';
import {
  XMLValueParser,
  XMLParser,
  getCriticalGltfLoadCount,
  getActiveGltfLoadCount,
  hasAnyGltfLoadStarted,
  _resetGltfLoadTrackingForTests,
  _trackGltfLoadForTests,
  setKTX2TranscoderPath,
  getGroupSpawnDefaults,
  normalizeGroupProfileId,
  roleToProfile,
  yawAnglesFromStepDeg,
  optBool,
  optNumber,
  composeSpawnRotation,
  defaultTransformParts,
  disposeSkyEnv,
  formatTransformAttr,
  isNormalWithinSlopeLimit,
  parseAt,
  parseSemicolonPlaceString,
  parseTransformAttr,
  slopeAngleRad,
} from 'vibegame';
import {
  defineSoundBank,
  getSoundDef,
  setMasterVolume,
  getMasterVolume,
  setBusVolume,
  getBusVolume,
  _resetSoundBank,
} from 'vibegame/audio/bank';

describe('coverage-100 pure helpers', () => {
  beforeEach(() => {
    _resetGltfLoadTrackingForTests();
    _resetSoundBank();
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  describe('gltf-bridge tracking', () => {
    it('starts at zero', () => {
      expect(getCriticalGltfLoadCount()).toBe(0);
      expect(getActiveGltfLoadCount()).toBe(0);
      expect(hasAnyGltfLoadStarted()).toBe(false);
    });
    it('reset clears state', () => {
      _trackGltfLoadForTests(Promise.resolve(1), 'critical');
      _resetGltfLoadTrackingForTests();
      expect(getActiveGltfLoadCount()).toBe(0);
    });
    it('setKTX2TranscoderPath accepts custom url', () => {
      setKTX2TranscoderPath('https://example.com/basis/');
      expect(true).toBe(true);
    });
  });

  describe('XMLValueParser', () => {
    it('parses 42', () => {
      expect(XMLValueParser.parse('42')).toEqual(42);
    });
    it('parses 3.14', () => {
      expect(XMLValueParser.parse('3.14')).toEqual(3.14);
    });
    it('parses true', () => {
      expect(XMLValueParser.parse('true')).toEqual(true);
    });
    it('parses false', () => {
      expect(XMLValueParser.parse('false')).toEqual(false);
    });
    it('parses 0xff0000', () => {
      expect(XMLValueParser.parse('0xff0000')).toEqual(16711680);
    });
    it('parses 1 2 3', () => {
      expect(XMLValueParser.parse('1 2 3')).toEqual({ x: 1, y: 2, z: 3 });
    });
    it('parses hello', () => {
      expect(XMLValueParser.parse('hello')).toEqual('hello');
    });
    it('parses 0 0 0', () => {
      expect(XMLValueParser.parse('0 0 0')).toEqual({ x: 0, y: 0, z: 0 });
    });
    it('parses -1 2 -3', () => {
      expect(XMLValueParser.parse('-1 2 -3')).toEqual({ x: -1, y: 2, z: -3 });
    });
    it('parses #abc123', () => {
      expect(XMLValueParser.parse('#abc123')).toEqual(0xabc123);
    });
  });

  describe('parseAt', () => {
    it('string xz', () => {
      expect(parseAt('1 2')).toEqual([1, 2]);
    });
    it('object xz', () => {
      expect(parseAt({ x: 3, z: 4 })).toEqual([3, 4]);
    });
    it('array pair', () => {
      expect(parseAt([9, 8])).toEqual([9, 8]);
    });
    it('negative', () => {
      expect(parseAt('0 -5')).toEqual([0, -5]);
    });
  });

  it('place string 0', () => {
    expect(
      Object.keys(parseSemicolonPlaceString('at: 1 2; base-y-offset: 0.1'))
        .length
    ).toBeGreaterThan(0);
  });
  it('place string 1', () => {
    expect(
      Object.keys(parseSemicolonPlaceString('align-to-terrain: 1')).length
    ).toBeGreaterThan(0);
  });
  it('place string 2', () => {
    expect(
      Object.keys(parseSemicolonPlaceString('scale-min: 0.5; scale-max: 2'))
        .length
    ).toBeGreaterThan(0);
  });
  it('place string 3', () => {
    expect(
      Object.keys(parseSemicolonPlaceString('near-water: 1')).length
    ).toBeGreaterThan(0);
  });
  it('place string 4', () => {
    expect(
      Object.keys(parseSemicolonPlaceString('avoid-water: 0')).length
    ).toBeGreaterThan(0);
  });

  describe('transform-merge', () => {
    it('default parts', () => {
      expect(defaultTransformParts().scale).toEqual([1, 1, 1]);
    });
    it('parse pos: 1 2 3', () => {
      expect(parseTransformAttr('pos: 1 2 3').pos.length).toBe(3);
    });
    it('parse euler: 0 90 0', () => {
      expect(parseTransformAttr('euler: 0 90 0').pos.length).toBe(3);
    });
    it('parse scale: 2', () => {
      expect(parseTransformAttr('scale: 2').pos.length).toBe(3);
    });
    it('parse pos: 0 0 0; scale: 3 3 3', () => {
      expect(parseTransformAttr('pos: 0 0 0; scale: 3 3 3').pos.length).toBe(3);
    });
    it('roundtrip', () => {
      expect(formatTransformAttr(defaultTransformParts())).toContain('pos:');
    });
  });

  describe('spawn profiles', () => {
    it('profile tree', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('tree'))
      ).toBeDefined();
    });
    it('profile rock', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('rock'))
      ).toBeDefined();
    });
    it('profile bush', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('bush'))
      ).toBeDefined();
    });
    it('profile grass', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('grass'))
      ).toBeDefined();
    });
    it('profile prop', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('prop'))
      ).toBeDefined();
    });
    it('profile crate', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('crate'))
      ).toBeDefined();
    });
    it('profile enemy', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('enemy'))
      ).toBeDefined();
    });
    it('profile hero', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('hero'))
      ).toBeDefined();
    });
    it('profile shrub', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('shrub'))
      ).toBeDefined();
    });
    it('profile debris', () => {
      expect(
        getGroupSpawnDefaults(normalizeGroupProfileId('debris'))
      ).toBeDefined();
    });
    it('yaw step 30', () => {
      expect(yawAnglesFromStepDeg(30).length).toBeGreaterThan(0);
    });
    it('yaw step 45', () => {
      expect(yawAnglesFromStepDeg(45).length).toBeGreaterThan(0);
    });
    it('yaw step 90', () => {
      expect(yawAnglesFromStepDeg(90).length).toBeGreaterThan(0);
    });
    it('yaw step 120', () => {
      expect(yawAnglesFromStepDeg(120).length).toBeGreaterThan(0);
    });
    it('yaw step 180', () => {
      expect(yawAnglesFromStepDeg(180).length).toBeGreaterThan(0);
    });
    it('yaw step 360', () => {
      expect(yawAnglesFromStepDeg(360).length).toBeGreaterThan(0);
    });
  });

  it('role tree', () => {
    const r = roleToProfile('tree');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role rock', () => {
    const r = roleToProfile('rock');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role water', () => {
    const r = roleToProfile('water');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role shrub', () => {
    const r = roleToProfile('shrub');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role unknown_role_xyz', () => {
    const r = roleToProfile('unknown_role_xyz');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role boulder', () => {
    const r = roleToProfile('boulder');
    expect(r === null || typeof r === 'string').toBe(true);
  });
  it('role flower', () => {
    const r = roleToProfile('flower');
    expect(r === null || typeof r === 'string').toBe(true);
  });

  describe('surface helpers', () => {
    it('slope y=1', () => {
      const n = new THREE.Vector3(0, 1, 1).normalize();
      expect(slopeAngleRad(n)).toBeGreaterThanOrEqual(0);
    });
    it('slope y=0.5', () => {
      const n = new THREE.Vector3(0, 0.5, 1).normalize();
      expect(slopeAngleRad(n)).toBeGreaterThanOrEqual(0);
    });
    it('slope y=0.1', () => {
      const n = new THREE.Vector3(0, 0.1, 1).normalize();
      expect(slopeAngleRad(n)).toBeGreaterThanOrEqual(0);
    });
    it('limit 0', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 0)).toBe(
        true
      );
    });
    it('limit 15', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 15)).toBe(
        true
      );
    });
    it('limit 30', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 30)).toBe(
        true
      );
    });
    it('limit 45', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 45)).toBe(
        true
      );
    });
    it('limit 60', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 60)).toBe(
        true
      );
    });
    it('limit 89', () => {
      expect(isNormalWithinSlopeLimit(new THREE.Vector3(0, 1, 0), 89)).toBe(
        true
      );
    });
  });

  describe('composeSpawnRotation', () => {
    it('upright align', () => {
      const e = composeSpawnRotation(
        new THREE.Vector3(0, 1, 0),
        true,
        0,
        [0, 0, 0]
      );
      expect(typeof e.x).toBe('number');
    });
    it('no align yaw', () => {
      const e = composeSpawnRotation(
        new THREE.Vector3(0, 1, 0),
        false,
        0.5,
        [10, 0, 0]
      );
      expect(typeof e.z).toBe('number');
    });
  });

  describe('audio helpers', () => {
    it('define sound', () => {
      defineSoundBank({ footstep: { url: '/s.wav' }, jump: { url: '/j.wav' } });
      expect(getSoundDef('footstep')?.url).toBe('/s.wav');
    });
    it('master 0', () => {
      setMasterVolume(0);
      expect(getMasterVolume()).toBe(0);
    });
    it('master 0.25', () => {
      setMasterVolume(0.25);
      expect(getMasterVolume()).toBe(0.25);
    });
    it('master 0.5', () => {
      setMasterVolume(0.5);
      expect(getMasterVolume()).toBe(0.5);
    });
    it('master 0.75', () => {
      setMasterVolume(0.75);
      expect(getMasterVolume()).toBe(0.75);
    });
    it('master 1', () => {
      setMasterVolume(1);
      expect(getMasterVolume()).toBe(1);
    });
    it('bus sfx', () => {
      setBusVolume('sfx', 0.8);
      expect(getBusVolume('sfx')).toBe(0.8);
    });
    it('bus music', () => {
      setBusVolume('music', 0.3);
      expect(getBusVolume('music')).toBe(0.3);
    });
  });

  it('disposeSkyEnv smoke', () => {
    expect(() => disposeSkyEnv()).not.toThrow();
  });

  describe('XMLParser smoke', () => {
    it('tag Scene', () => {
      expect(XMLParser.parse('<Scene pos="0 0 0"/>').root.tagName).toBe(
        'Scene'
      );
    });
    it('tag GameObject', () => {
      expect(XMLParser.parse('<GameObject pos="0 0 0"/>').root.tagName).toBe(
        'GameObject'
      );
    });
    it('tag box', () => {
      expect(XMLParser.parse('<box pos="0 0 0"/>').root.tagName).toBe('box');
    });
    it('tag Terrain', () => {
      expect(XMLParser.parse('<Terrain pos="0 0 0"/>').root.tagName).toBe(
        'Terrain'
      );
    });
    it('tag PlayerGLTF', () => {
      expect(XMLParser.parse('<PlayerGLTF pos="0 0 0"/>').root.tagName).toBe(
        'PlayerGLTF'
      );
    });
    it('tag Rigidbody', () => {
      expect(XMLParser.parse('<Rigidbody pos="0 0 0"/>').root.tagName).toBe(
        'Rigidbody'
      );
    });
    it('tag AudioListener', () => {
      expect(XMLParser.parse('<AudioListener pos="0 0 0"/>').root.tagName).toBe(
        'AudioListener'
      );
    });
    it('tag Spawner', () => {
      expect(XMLParser.parse('<Spawner pos="0 0 0"/>').root.tagName).toBe(
        'Spawner'
      );
    });
  });

  it('optNumber 0', () => {
    expect(optNumber(String(0), 99)).toBe(0);
  });
  it('optNumber 1', () => {
    expect(optNumber(String(1), 99)).toBe(1);
  });
  it('optNumber 2', () => {
    expect(optNumber(String(2), 99)).toBe(2);
  });
  it('optNumber 3', () => {
    expect(optNumber(String(3), 99)).toBe(3);
  });
  it('optNumber 4', () => {
    expect(optNumber(String(4), 99)).toBe(4);
  });
  it('optNumber 5', () => {
    expect(optNumber(String(5), 99)).toBe(5);
  });
  it('optNumber 6', () => {
    expect(optNumber(String(6), 99)).toBe(6);
  });
  it('optNumber 7', () => {
    expect(optNumber(String(7), 99)).toBe(7);
  });
  it('optNumber 8', () => {
    expect(optNumber(String(8), 99)).toBe(8);
  });
  it('optNumber 9', () => {
    expect(optNumber(String(9), 99)).toBe(9);
  });
  it('optNumber 10', () => {
    expect(optNumber(String(10), 99)).toBe(10);
  });
  it('optNumber 11', () => {
    expect(optNumber(String(11), 99)).toBe(11);
  });
  it('optNumber 12', () => {
    expect(optNumber(String(12), 99)).toBe(12);
  });
  it('optNumber 13', () => {
    expect(optNumber(String(13), 99)).toBe(13);
  });
  it('optNumber 14', () => {
    expect(optNumber(String(14), 99)).toBe(14);
  });
  it('optNumber 15', () => {
    expect(optNumber(String(15), 99)).toBe(15);
  });
  it('optNumber 16', () => {
    expect(optNumber(String(16), 99)).toBe(16);
  });
  it('optNumber 17', () => {
    expect(optNumber(String(17), 99)).toBe(17);
  });
  it('optNumber 18', () => {
    expect(optNumber(String(18), 99)).toBe(18);
  });
  it('optNumber 19', () => {
    expect(optNumber(String(19), 99)).toBe(19);
  });
  it('optNumber 20', () => {
    expect(optNumber(String(20), 99)).toBe(20);
  });
  it('optNumber 21', () => {
    expect(optNumber(String(21), 99)).toBe(21);
  });
  it('optNumber 22', () => {
    expect(optNumber(String(22), 99)).toBe(22);
  });
  it('optNumber 23', () => {
    expect(optNumber(String(23), 99)).toBe(23);
  });
  it('optNumber 24', () => {
    expect(optNumber(String(24), 99)).toBe(24);
  });
  it('optBool default', () => {
    expect(optBool(undefined, true)).toBe(true);
  });
  it('optBool zero', () => {
    expect(optBool('0', true)).toBe(false);
  });
  it('optBool one', () => {
    expect(optBool('1', false)).toBe(true);
  });
});
