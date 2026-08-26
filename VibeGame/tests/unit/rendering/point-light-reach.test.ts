import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { PointLight, WorldTransform } from 'vibegame';
import { filterPointLightsInView } from '../../../src/plugins/rendering/systems';

/**
 * Reach culling for point lights.
 *
 * Every light in the scene is a full iteration in every fragment shader of the
 * frame, whether or not it lights anything visible — in the RPG village that
 * was 12 torches costing ~1 ms each while only a handful could reach the view.
 * A light with a positive `distance` has a hard cutoff there, so one whose
 * sphere misses the frustum can be dropped with no change to a single pixel.
 */
function placeLight(
  eid: number,
  x: number,
  y: number,
  z: number,
  distance: number
): void {
  WorldTransform.posX[eid] = x;
  WorldTransform.posY[eid] = y;
  WorldTransform.posZ[eid] = z;
  PointLight.distance[eid] = distance;
}

/** Camera at the origin looking down -Z, the three default. */
function makeCamera(): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 500);
  camera.position.set(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

describe('filterPointLightsInView', () => {
  const camera = makeCamera();

  beforeEach(() => {
    // 1: in front, well inside the view.
    placeLight(1, 0, 0, -20, 10);
    // 2: far behind the camera, short range — cannot touch any visible pixel.
    placeLight(2, 0, 0, 200, 10);
    // 3: behind the camera but with a range that still reaches in front of it.
    placeLight(3, 0, 0, 30, 60);
    // 4: off to the side, out of the frustum, short range.
    placeLight(4, 400, 0, -20, 10);
  });

  it('keeps lights whose sphere reaches the view', () => {
    expect(filterPointLightsInView([1], camera)).toEqual([1]);
  });

  it('drops a light whose falloff cannot reach the frustum', () => {
    expect(filterPointLightsInView([2], camera)).toEqual([]);
    expect(filterPointLightsInView([4], camera)).toEqual([]);
  });

  it('keeps a light behind the camera whose range still spills into view', () => {
    // The test is the sphere, not the position: a brazier just behind the
    // player still lights the wall in front of them.
    expect(filterPointLightsInView([3], camera)).toEqual([3]);
  });

  it('treats distance 0 as unbounded and never culls it', () => {
    // three reads `distance === 0` as "no cutoff", so there is no sphere to
    // test and dropping it could darken the frame.
    placeLight(5, 0, 0, 5000, 0);
    expect(filterPointLightsInView([5], camera)).toEqual([5]);
  });

  it('preserves the caller order of the lights it keeps', () => {
    expect(filterPointLightsInView([1, 2, 3, 4], camera)).toEqual([1, 3]);
  });
});
