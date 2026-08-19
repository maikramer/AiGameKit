import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
// Importing the barrel first is load-order, not decoration: reaching straight
// into `rendering/systems` pulls the plugin module in mid-initialisation and
// it throws on its own system list.
import 'vibegame';
import { snapShadowCenterToTexels } from '../../../src/plugins/rendering/systems';

/**
 * The sun's shadow frustum follows the camera. Two things have to hold or the
 * shadows are worse than none at all:
 *
 *  1. the centre lands on whole shadow-map texels, so edges do not crawl while
 *     the camera moves (a racer at 200 km/h is the pathological case);
 *  2. the snap is stable — the same input always produces the same centre.
 */
describe('snapShadowCenterToTexels', () => {
  const lightDir = new THREE.Vector3(-0.9, 1.2, -0.5).normalize();
  const radius = 32;
  const mapSize = 4096;
  /** Shadow-map texel size in world units for the config above. */
  const texel = (radius * 2) / mapSize;

  it('quantises: nearby centres collapse onto the same texel', () => {
    const a = snapShadowCenterToTexels(
      new THREE.Vector3(10, 2, 5),
      lightDir,
      radius,
      mapSize
    ).clone();
    // Move by a fraction of a texel — the snapped centre must not move.
    const b = snapShadowCenterToTexels(
      new THREE.Vector3(10 + texel * 0.1, 2, 5),
      lightDir,
      radius,
      mapSize
    ).clone();
    // Only the two axes that raster the shadow map are quantised; the depth
    // axis still slides with the camera, which does not move a single texel.
    expect(b.distanceTo(a)).toBeLessThan(texel * 0.2);
  });

  it('still tracks the camera over distances larger than a texel', () => {
    const a = snapShadowCenterToTexels(
      new THREE.Vector3(0, 0, 0),
      lightDir,
      radius,
      mapSize
    ).clone();
    const b = snapShadowCenterToTexels(
      new THREE.Vector3(50, 0, 0),
      lightDir,
      radius,
      mapSize
    ).clone();
    expect(b.distanceTo(a)).toBeGreaterThan(49);
    expect(b.distanceTo(a)).toBeLessThan(51);
  });

  it('never drifts more than one texel from the requested centre', () => {
    const wanted = new THREE.Vector3(123.456, 7.89, -42.13);
    const snapped = snapShadowCenterToTexels(
      wanted.clone(),
      lightDir,
      radius,
      mapSize
    );
    // Only the two axes that span the shadow map are quantised, so the error
    // is bounded by half a texel on each of them.
    expect(snapped.distanceTo(wanted)).toBeLessThan(texel);
  });

  it('is deterministic for the same input', () => {
    const first = snapShadowCenterToTexels(
      new THREE.Vector3(3, 1, -9),
      lightDir,
      radius,
      mapSize
    ).clone();
    const second = snapShadowCenterToTexels(
      new THREE.Vector3(3, 1, -9),
      lightDir,
      radius,
      mapSize
    ).clone();
    expect(second.equals(first)).toBe(true);
  });

  it('survives a light pointing straight down', () => {
    // `lookAt` degenerates when the light direction is parallel to up, which
    // would produce a NaN basis and park every shadow at the origin.
    const straightDown = new THREE.Vector3(0, 1, 0);
    const snapped = snapShadowCenterToTexels(
      new THREE.Vector3(20, 3, -20),
      straightDown,
      radius,
      mapSize
    );
    expect(Number.isFinite(snapped.x)).toBe(true);
    expect(Number.isFinite(snapped.y)).toBe(true);
    expect(Number.isFinite(snapped.z)).toBe(true);
    expect(snapped.length()).toBeGreaterThan(1);
  });

  it('passes the centre through untouched when the texel size is degenerate', () => {
    const wanted = new THREE.Vector3(5, 5, 5);
    const snapped = snapShadowCenterToTexels(wanted.clone(), lightDir, 0, 2048);
    expect(snapped.equals(wanted)).toBe(true);
  });
});
