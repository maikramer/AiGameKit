import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { Transform, TransformsPlugin } from 'vibegame/transforms';
import { OrbitCamera, OrbitCameraPlugin } from 'vibegame/orbit-camera';
import { RenderingPlugin, MainCamera } from 'vibegame/rendering';
import { PlayerPlugin } from 'vibegame/player';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';
import { Water, WaterPlugin } from '../../../src/plugins/water';
import { Terrain, TerrainPlugin } from '../../../src/plugins/terrain';
import { NavMeshAgent, NavMeshSurface, NavmeshPlugin } from '../../../src/plugins/navmesh';
import { TextMesh, Text3dPlugin } from '../../../src/plugins/text-3d';
import { HudPanel, HudPlugin } from '../../../src/plugins/hud';
import { Joint, JointsPlugin } from '../../../src/plugins/joints';
import { FollowCamera, FollowCameraPlugin } from '../../../src/plugins/follow-camera';
import { GltfPending, GltfPhysicsPending, GltfXmlPlugin } from '../../../src/plugins/gltf-xml';
import { RaycastPlugin, RaycastHit, RaycastSource } from '../../../src/plugins/raycast';
import { I18nPlugin, I18nText } from '../../../src/plugins/i18n';
import { TextPlugin, Word, Paragraph } from '../../../src/plugins/text';
import { NetworkPlugin, Networked, NetworkBuffer } from '../../../src/plugins/network';
import { SkyPlugin, Skybox } from '../../../src/plugins/sky';
import { PhysicsPlugin } from '../../../src/plugins/physics';
import { InputPlugin } from '../../../src/plugins/input';

/**
 * Integration tests verifying that each component-like recipe works
 * as a child of <GameObject> (merge:true) AND standalone (backward compat).
 */
describe('Component-children merge:true integration', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(PhysicsPlugin);
    state.registerPlugin(InputPlugin);
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(PlayerPlugin);
    state.registerPlugin(SkyPlugin);
    state.registerPlugin(FogPlugin);
    state.registerPlugin(WaterPlugin);
    state.registerPlugin(TerrainPlugin);
    state.registerPlugin(NavmeshPlugin);
    state.registerPlugin(Text3dPlugin);
    state.registerPlugin(HudPlugin);
    state.registerPlugin(JointsPlugin);
    state.registerPlugin(FollowCameraPlugin);
    state.registerPlugin(OrbitCameraPlugin);
    state.registerPlugin(GltfXmlPlugin);
    state.registerPlugin(RaycastPlugin);
    state.registerPlugin(I18nPlugin);
    state.registerPlugin(TextPlugin);
    state.registerPlugin(NetworkPlugin);
  });

  function parse(xml: string) {
    const parsed = XMLParser.parse(xml);
    return parseXMLToEntities(state, parsed.root);
  }

  // --- Skybox ---

  it('Skybox works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Skybox url="test-sky.png" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Skybox)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Skybox works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Skybox url="test-sky.png" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Skybox)).toBe(true);
  });

  // --- Fog ---

  it('Fog works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Fog mode="linear" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Fog works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Fog mode="linear" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(true);
  });

  // --- Water ---

  it('Water works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Water size="128" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Water)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Water works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Water size="128" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Water)).toBe(true);
  });

  // --- Terrain ---

  it('Terrain works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Terrain /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Terrain)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Terrain works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Terrain /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Terrain)).toBe(true);
  });

  // --- NavMeshSurface ---

  it('NavMeshSurface works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><NavMeshSurface /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, NavMeshSurface)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('NavMeshSurface works standalone (backward compat)', () => {
    const entities = parse(
      '<root><NavMeshSurface /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, NavMeshSurface)).toBe(true);
  });

  // --- NavMeshAgent ---

  it('NavMeshAgent works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><NavMeshAgent /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, NavMeshAgent)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('NavMeshAgent works standalone (backward compat)', () => {
    const entities = parse(
      '<root><NavMeshAgent /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, NavMeshAgent)).toBe(true);
  });

  // --- TextMesh ---

  it('TextMesh works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><TextMesh /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, TextMesh)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('TextMesh works standalone (backward compat)', () => {
    const entities = parse(
      '<root><TextMesh /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, TextMesh)).toBe(true);
  });

  // --- HudPanel ---

  it('HudPanel works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><HudPanel /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, HudPanel)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('HudPanel works standalone (backward compat)', () => {
    const entities = parse(
      '<root><HudPanel /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, HudPanel)).toBe(true);
  });

  // --- Joint ---

  it('Joint works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Joint joint-type="1" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Joint)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Joint works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Joint joint-type="1" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Joint)).toBe(true);
  });

  // --- FollowCamera ---

  it('FollowCamera works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><FollowCamera /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, FollowCamera)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(state.hasComponent(entity, MainCamera)).toBe(true);
  });

  it('FollowCamera works standalone (backward compat)', () => {
    const entities = parse(
      '<root><FollowCamera /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, FollowCamera)).toBe(true);
  });

  // --- OrbitCamera ---

  it('OrbitCamera works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><OrbitCamera /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, OrbitCamera)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(state.hasComponent(entity, MainCamera)).toBe(true);
  });

  it('OrbitCamera works standalone (backward compat)', () => {
    const entities = parse(
      '<root><OrbitCamera /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, OrbitCamera)).toBe(true);
  });

  // --- GLTFLoader ---

  it('GLTFLoader works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><GLTFLoader url="model.glb" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, GltfPending)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('GLTFLoader works standalone (backward compat)', () => {
    const entities = parse(
      '<root><GLTFLoader url="model.glb" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, GltfPending)).toBe(true);
  });

  // --- GLTFDynamic ---

  it('GLTFDynamic works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><GLTFDynamic url="box.glb" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, GltfPending)).toBe(true);
    expect(state.hasComponent(entity, GltfPhysicsPending)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('GLTFDynamic works standalone (backward compat)', () => {
    const entities = parse(
      '<root><GLTFDynamic url="box.glb" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, GltfPending)).toBe(true);
    expect(state.hasComponent(entity, GltfPhysicsPending)).toBe(true);
  });

  // --- RaycastSource ---

  it('RaycastSource works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><RaycastSource /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, RaycastSource)).toBe(true);
    expect(state.hasComponent(entity, RaycastHit)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('RaycastSource works standalone (backward compat)', () => {
    const entities = parse(
      '<root><RaycastSource /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, RaycastSource)).toBe(true);
  });

  // --- I18nText ---

  it('I18nText works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><I18nText key="hello" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, I18nText)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('I18nText works standalone (backward compat)', () => {
    const entities = parse(
      '<root><I18nText key="hello" /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, I18nText)).toBe(true);
  });

  // --- Paragraph ---

  it('Paragraph works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Paragraph /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Paragraph)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Paragraph works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Paragraph /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Paragraph)).toBe(true);
  });

  // --- Word ---

  it('Word works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><Word /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Word)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('Word works standalone (backward compat)', () => {
    const entities = parse(
      '<root><Word /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Word)).toBe(true);
  });

  // --- NetworkedPlayer ---

  it('NetworkedPlayer works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><NetworkedPlayer /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Networked)).toBe(true);
    expect(state.hasComponent(entity, NetworkBuffer)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('NetworkedPlayer works standalone (backward compat)', () => {
    const entities = parse(
      '<root><NetworkedPlayer /></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Networked)).toBe(true);
    expect(state.hasComponent(entity, NetworkBuffer)).toBe(true);
  });

  // --- PlayerGLTF ---

  it('PlayerGLTF works as child of GameObject', () => {
    const entities = parse(
      '<root><GameObject><PlayerGLTF model-url="hero.glb" /></GameObject></root>'
    );
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Transform)).toBe(true);
    // PlayerGLTF inherits all player components
    const PlayerController = state.getComponent('playerController')!;
    expect(state.hasComponent(entity, PlayerController)).toBe(true);
  });

  it('PlayerGLTF works standalone (backward compat)', () => {
    const entities = parse(
      '<root><PlayerGLTF model-url="hero.glb" /></root>'
    );
    const entity = entities[0].entity;
    const PlayerController = state.getComponent('playerController')!;
    expect(state.hasComponent(entity, PlayerController)).toBe(true);
  });

  // --- Multi-component merge ---

  it('multiple merge components merge onto single parent entity', () => {
    const entities = parse(`<root>
      <GameObject name="MultiComponent">
        <Fog mode="linear" />
        <RaycastSource max-dist="50" />
      </GameObject>
    </root>`);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(true);
    expect(state.hasComponent(entity, RaycastSource)).toBe(true);
    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(state.hasComponent(entity, RaycastHit)).toBe(true);
  });
});
