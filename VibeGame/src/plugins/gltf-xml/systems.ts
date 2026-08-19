import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import {
  defineSystem,
  defineQueryLive,
  type State,
  type System,
} from '../../core';
import { Parent } from '../../core/ecs';
import {
  disposeGltfBridge,
  loadGltfLodToSceneForEntity,
  loadGltfMasterTracked,
  loadGltfToSceneForEntity,
} from '../../extras/gltf-bridge';
import { GltfAnimator } from '../../extras/gltf-animator';
import { bumpSceneGeneration } from '../../extras/scene-generation';
import { getScene } from '../rendering';
import { GltfAnimationState } from '../gltf-anim/components';
import { registerAnimator, unregisterAnimator } from '../gltf-anim/systems';
import { MonoBehaviour } from '../entity-script/components';
import { PlayerController, PlayerGltfConfig } from '../player/components';
import { Transform } from '../transforms/components';
import {
  addInstancedGltf,
  getInstancedLodUrls,
  isGltfInstanced,
} from './auto-instance';
import { GltfPending, GltfPhysicsPending } from './components';
import {
  clearGltfBoundsCache,
  registerGltfLocalYBounds,
} from './gltf-bounds-cache';
import {
  clearGltfLodUrls,
  getGltfLodUrls,
  getGltfUrl,
  isGltfInFlight,
  setGltfInFlight,
} from './context';
import { getGltfRootGroup, registerGltfRootGroup } from './group-registry';
import { getLodChild } from '../../extras/gltf-lod-parking';

// Zero-copy: `GltfPending` covers every dressed prop in the world (~91k in
// simple-rpg) and membership never changes inside the loop below — the loads it
// kicks resolve in `.then()`, on a later frame. Snapshotting that dense set into
// a fresh array every frame cost more than the pass itself.
const gltfLoadQuery = defineQueryLive([GltfPending]);

/**
 * Once every pending GLB has been kicked, this system has nothing left to do,
 * but the query still holds every prop in the world. Latch that state and skip
 * the scan entirely; a membership change (spawn/despawn shifts `dense.length`)
 * or the periodic re-scan below re-arms it, so an entity that is reset to
 * `loaded = 0` in place is picked up within {@link LOAD_RESCAN_FRAMES}.
 */
interface LoadScanLatch {
  allKicked: boolean;
  lastCount: number;
  lastScanFrame: number;
}
const loadScanLatch = new WeakMap<State, LoadScanLatch>();
const LOAD_RESCAN_FRAMES = 30;

/**
 * Boot GLB kicks race the world setup: a long synchronous spawn/navmesh pass
 * can starve the event loop past the master load timeout, and the first URLs
 * in the queue (whole districts, in simple-rpg) would be dropped meshless on
 * their first miss. A failure keeps `loaded = 0` so the periodic rescan
 * re-kicks the entity — the boot gate holds honestly through the retry. Real
 * 404s fail fast, so the cap only costs a few quick rejections.
 */
const MAX_GLTF_LOAD_RETRIES = 3;

function shouldRetryGltfLoad(eid: number): boolean {
  return GltfPending.retries[eid] < MAX_GLTF_LOAD_RETRIES;
}

function applyTransformToGroup(group: THREE.Object3D, eid: number): void {
  group.position.set(
    Transform.posX[eid],
    Transform.posY[eid],
    Transform.posZ[eid]
  );
  group.scale.set(
    Transform.scaleX[eid],
    Transform.scaleY[eid],
    Transform.scaleZ[eid]
  );
  const rx = Transform.rotX[eid];
  const ry = Transform.rotY[eid];
  const rz = Transform.rotZ[eid];
  const rw = Transform.rotW[eid];
  const quatIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;
  if (quatIdentity) {
    group.rotation.set(
      Transform.eulerX[eid],
      Transform.eulerY[eid],
      Transform.eulerZ[eid]
    );
  } else {
    group.quaternion.set(rx, ry, rz, rw);
  }
}

/**
 * Auto-play the ``idle`` clip on a freshly loaded GLTFLoader entity when the
 * GLB ships animations and no script owns the entity's animator.
 *
 * The XML ``<GLTFLoader>`` path attaches only ``transform``/``gltfPending`` —
 * it never creates a {@link GltfAnimator}, so rigged NPCs declared as
 * ``<GameObject><GLTFLoader/></GameObject>`` (no ``script=``) sit in bind/T-pose
 * even though their GLBs carry idle/walk clips. This wires them into the shared
 * {@link GltfAnimationUpdateSystem} (already distance-culled) so they idle.
 *
 * Static props are excluded by the ``animations.length`` guard — trees, rocks,
 * market stalls and other scenery GLBs ship with zero clips. The ``idle`` clip
 * is resolved with the same fuzzy matcher the player uses, so capitalisation
 * variants (``Idle``, ``IDLE``) all work.
 */
async function maybeAutoPlayIdle(
  state: State,
  eid: number,
  url: string
): Promise<void> {
  // Skip entities a script already drives. Creatures attach their own
  // GltfAnimator (via createCreatureBehaviours) and the player owns its rig, so
  // auto-idling their visual GLTFLoader would double-animate the skeleton. The
  // script component sits on the entity itself (merged GLTFLoader) or on the
  // parent GameObject when the GLTFLoader is a child element.
  const parentId = Parent.entity[eid];
  const scriptOwned =
    state.hasComponent(eid, MonoBehaviour) ||
    (parentId > 0 && state.hasComponent(parentId, MonoBehaviour)) ||
    state.hasComponent(eid, PlayerController) ||
    state.hasComponent(eid, PlayerGltfConfig) ||
    (parentId > 0 && state.hasComponent(parentId, PlayerController)) ||
    (parentId > 0 && state.hasComponent(parentId, PlayerGltfConfig));
  if (scriptOwned) return;
  const group = getGltfRootGroup(state, eid);
  if (!group) return;
  // The skinned mesh lives in the lod0 child (LOD triple) or the group itself
  // (single GLB) — that is the root the mixer must drive. Ask the LOD registry:
  // lod0 is detached whenever a farther level is the active one.
  const root = getLodChild(group, 0) ?? group;
  try {
    // Cache hit: the master was already fetched for the visual clone, so this
    // never re-downloads or re-arms the boot gate (_settledMasters short-circuit).
    const master = await loadGltfMasterTracked(state, url, 'background');
    if (!state.exists(eid)) return;
    if (!master.animations?.length) return; // static prop — never animate
    if (state.hasComponent(eid, GltfAnimationState)) return; // already animated
    const animator = new GltfAnimator(master, {
      root,
      crossfadeDuration: 0.25,
    });
    if (!animator.resolveClipName('idle')) {
      // Rigged but no idle clip — leave as-is rather than guessing a loop.
      return;
    }
    const idx = registerAnimator(state, animator);
    state.addComponent(eid, GltfAnimationState);
    GltfAnimationState.registryIndex[eid] = idx;
    animator.play('idle');
    // Drop the animator when the entity is destroyed so the registry + mixer
    // don't leak (the update system's dispose only runs on full teardown).
    state.onDestroy(eid, () => unregisterAnimator(state, idx));
  } catch {
    // Master fetch can 404 for streamed LOD URLs; stay silent — the visual
    // already rendered, only the idle polish is missing.
  }
}

export const GltfXmlLoadSystem: System = defineSystem({
  name: 'GltfXmlLoadSystem',
  group: 'setup',
  update: (state) => {
    const scene = getScene(state);
    if (!scene) return;

    const pending = gltfLoadQuery(state.world);
    const frame = state.time.frameCount;
    let latch = loadScanLatch.get(state);
    if (!latch) {
      latch = { allKicked: false, lastCount: -1, lastScanFrame: -1 };
      loadScanLatch.set(state, latch);
    }
    if (
      latch.allKicked &&
      pending.length === latch.lastCount &&
      frame - latch.lastScanFrame < LOAD_RESCAN_FRAMES
    ) {
      return;
    }
    latch.lastCount = pending.length;
    latch.lastScanFrame = frame;
    let unkicked = 0;

    for (const eid of pending) {
      if (GltfPending.loaded[eid]) {
        continue;
      }
      if (isGltfInFlight(state, eid)) {
        unkicked++;
        continue;
      }
      unkicked++;
      const lodTriple = getGltfLodUrls(state, eid);
      const url = getGltfUrl(state, eid);
      if (lodTriple) {
        setGltfInFlight(state, eid, true);
        void loadGltfLodToSceneForEntity(state, lodTriple, eid)
          .then((group) => {
            GltfPending.loaded[eid] = 1;
            registerGltfLocalYBounds(
              lodTriple[0],
              getLodChild(group, 0) ?? group
            );
            applyTransformToGroup(group, eid);
            if (state.exists(eid)) {
              registerGltfRootGroup(state, eid, group);
              // Rigged NPCs without a script (e.g. scout/quest <GameObject>
              // + <GLTFLoader>) idle via the shared animator registry. The
              // master fetch is a cache hit; static props are skipped inside.
              void maybeAutoPlayIdle(state, eid, lodTriple[0] ?? '');
            }
            clearGltfLodUrls(state, eid);
          })
          .catch((err: unknown) => {
            const u = lodTriple.join(' | ');
            const base = err instanceof Error ? err.message : String(err);
            const looksLike404Html =
              base.includes('JSON') ||
              base.includes('parse') ||
              base.includes('<!DOCTYPE');
            const hint = looksLike404Html
              ? ' — resposta não é GLB (muitas vezes 404 HTML); confirme `public/` e `gameassets handoff`.'
              : '';
            logger.error('[gltf-load lod]', u, base + hint);
            if (!state.exists(eid)) return;
            if (shouldRetryGltfLoad(eid)) {
              GltfPending.retries[eid]++;
              return;
            }
            clearGltfLodUrls(state, eid);
            if (state.hasComponent(eid, GltfPhysicsPending)) {
              GltfPhysicsPending.ready[eid] = 1;
            }
            GltfPending.loaded[eid] = 1;
          })
          .finally(() => {
            setGltfInFlight(state, eid, false);
          });
        continue;
      }

      if (!url) {
        GltfPending.loaded[eid] = 1;
        if (state.hasComponent(eid, GltfPhysicsPending)) {
          GltfPhysicsPending.ready[eid] = 1;
        }
        continue;
      }

      // instanced="true": render through the shared InstancedMesh pool for
      // this URL instead of a per-entity clone. LOD roots and GLTFDynamic
      // (physics fitted from the loaded group) keep the group path.
      if (
        isGltfInstanced(state, eid) &&
        !state.hasComponent(eid, GltfPhysicsPending)
      ) {
        const [lod1, lod2] = getInstancedLodUrls(state, eid);
        addInstancedGltf(state, eid, url, lod1, lod2);
        GltfPending.loaded[eid] = 1;
        continue;
      }

      setGltfInFlight(state, eid, true);
      void loadGltfToSceneForEntity(state, url, eid)
        .then((group) => {
          GltfPending.loaded[eid] = 1;
          registerGltfLocalYBounds(url, group);
          applyTransformToGroup(group, eid);
          if (state.exists(eid)) {
            registerGltfRootGroup(state, eid, group);
            void maybeAutoPlayIdle(state, eid, url);
          }
        })
        .catch((err: unknown) => {
          const failedUrl = getGltfUrl(state, eid);
          const base = err instanceof Error ? err.message : String(err);
          const looksLike404Html =
            base.includes('JSON') ||
            base.includes('parse') ||
            base.includes('<!DOCTYPE');
          const hint = looksLike404Html
            ? ' — resposta não é GLB (muitas vezes 404 HTML); confirme `public/` e `gameassets handoff`.'
            : '';
          logger.error('[gltf-load]', failedUrl ?? '(sem url)', base + hint);
          if (!state.exists(eid)) return;
          if (shouldRetryGltfLoad(eid)) {
            GltfPending.retries[eid]++;
            return;
          }
          if (state.hasComponent(eid, GltfPhysicsPending)) {
            GltfPhysicsPending.ready[eid] = 1;
          }
          GltfPending.loaded[eid] = 1;
        })
        .finally(() => {
          setGltfInFlight(state, eid, false);
        });
    }

    latch.allKicked = unkicked === 0;
  },

  dispose(state) {
    // Bump generation BEFORE clearing the cache: an in-flight load whose .then
    // runs after this dispose must see the new generation and bail, never
    // re-populating the just-cleared cache nor attaching to the retired scene.
    bumpSceneGeneration(state);
    disposeGltfBridge();
    clearGltfBoundsCache();
  },
});
