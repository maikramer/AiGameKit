import { describe, expect, it } from 'bun:test';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import {
  CombatPlugin,
  DebugPlugin,
  Health,
  damageHealth,
  healHealth,
  isDead,
  ProjectileData,
  Rigidbody,
  Collider,
  CollisionEvents,
  getBodyForEntity,
  getRapierWorld,
  PhysicsStepSystem,
  getBodyYForFeetAt,
  getCharacterFeetY,
  GROUND_CONTACT_SKIN,
  getTerrainHeightAt,
  getTerrainContext,
  isTerrainDynamicsBlocking,
  setInputMovementSuppressed,
  Postprocessing,
  Transform,
  Destructible,
  isKeyDown,
  getBvhSurfaceHeight,
  NavMeshAgent,
  isNavMeshReady,
  setAgentTarget,
  clearAgentTarget,
  removeAgent,
  getAgentPosition,
  MeshRenderer,
  spawnParticleBurst,
  spawnFloatingText,
  getRenderingContext,
  threeCameras,
  registerDebugAction,
  registerDebugVar,
  getDebugRegistry,
} from 'vibegame';

describe('Public API surface — promoted symbols', () => {
  it('exports combat gameplay helpers (Health, damage/heal/isDead, ProjectileData)', () => {
    expect(Health).toBeDefined();
    expect(Health.current).toBeInstanceOf(Float32Array);
    expect(Health.max).toBeInstanceOf(Float32Array);
    expect(typeof damageHealth).toBe('function');
    expect(typeof healHealth).toBe('function');
    expect(typeof isDead).toBe('function');
    expect(ProjectileData).toBeDefined();
    expect(ProjectileData.damage).toBeInstanceOf(Float32Array);
    expect(CombatPlugin).toBeDefined();
  });

  it('exports physics components and body helpers (Rigidbody, Collider, CollisionEvents, getBodyForEntity)', () => {
    expect(Rigidbody).toBeDefined();
    expect(Rigidbody.mass).toBeInstanceOf(Float32Array);
    expect(Collider).toBeDefined();
    expect(CollisionEvents).toBeDefined();
    expect(typeof getBodyForEntity).toBe('function');
    expect(typeof getRapierWorld).toBe('function');
    expect(PhysicsStepSystem).toBeDefined();
    expect(typeof getBodyYForFeetAt).toBe('function');
    expect(typeof getCharacterFeetY).toBe('function');
    expect(typeof GROUND_CONTACT_SKIN).toBe('number');
  });

  it('exports terrain + bvh height queries (getTerrainHeightAt, getBvhSurfaceHeight, getTerrainContext)', () => {
    expect(typeof getTerrainHeightAt).toBe('function');
    expect(typeof getBvhSurfaceHeight).toBe('function');
    expect(typeof getTerrainContext).toBe('function');
    expect(typeof isTerrainDynamicsBlocking).toBe('function');
  });

  it('exports input helpers (isKeyDown, setInputMovementSuppressed)', () => {
    expect(typeof isKeyDown).toBe('function');
    expect(typeof setInputMovementSuppressed).toBe('function');
  });

  it('exports transform, destructible, rendering, postprocessing, debug symbols', () => {
    expect(Transform).toBeDefined();
    expect(Transform.posX).toBeInstanceOf(Float32Array);
    expect(Destructible).toBeDefined();
    expect(MeshRenderer).toBeDefined();
    expect(Postprocessing).toBeDefined();
    expect(Postprocessing.bloom).toBeInstanceOf(Uint8Array);
    expect(DebugPlugin).toBeDefined();
  });

  it('exports the debug registry API (registerDebugAction/registerDebugVar)', () => {
    expect(typeof registerDebugAction).toBe('function');
    expect(typeof registerDebugVar).toBe('function');
    expect(typeof getDebugRegistry).toBe('function');
  });

  it('exports navmesh agent helpers', () => {
    expect(NavMeshAgent).toBeDefined();
    expect(typeof isNavMeshReady).toBe('function');
    expect(typeof setAgentTarget).toBe('function');
    expect(typeof clearAgentTarget).toBe('function');
    expect(typeof removeAgent).toBe('function');
    expect(typeof getAgentPosition).toBe('function');
  });

  it('exports particle + floating-text spawners', () => {
    expect(typeof spawnParticleBurst).toBe('function');
    expect(typeof spawnFloatingText).toBe('function');
  });

  it('exposes rendering escape-hatches marked @internal (getRenderingContext, threeCameras)', () => {
    expect(typeof getRenderingContext).toBe('function');
    expect(threeCameras).toBeDefined();
  });
});

describe('Examples must not deep-import engine internals', () => {
  // Examples (games built on the engine) must import from the public 'vibegame'
  // barrel only. Reaching into ../../src/plugins/* couples games to internal
  // module layout and breaks when the engine is reorganised. This test is the
  // CI gate that enforces the boundary; the equivalent shell check is
  // `grep -rn "\.\./\.\./\.\./src/" VibeGame/examples/ || echo CLEAN`.
  const DEEP_IMPORT_RE = /(\.\.\/){3,}src\//;

  function listTsFiles(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      if (entry === 'node_modules' || entry === 'dist' || entry === '.cache')
        continue;
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        listTsFiles(full, out);
      } else if (
        entry.endsWith('.ts') ||
        entry.endsWith('.tsx') ||
        entry.endsWith('.js') ||
        entry.endsWith('.mjs')
      ) {
        out.push(full);
      }
    }
    return out;
  }

  it('has zero `../../../src/` imports under examples/', () => {
    const examplesDir = join(process.cwd(), 'examples');
    const offenders: string[] = [];
    for (const file of listTsFiles(examplesDir)) {
      const text = readFileSync(file, 'utf8');
      const lines = text.split('\n');
      for (let i = 0; i < lines.length; i++) {
        if (DEEP_IMPORT_RE.test(lines[i])) {
          offenders.push(
            `${relative(process.cwd(), file)}:${i + 1}: ${lines[i].trim()}`
          );
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
