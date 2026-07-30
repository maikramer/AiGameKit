import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../src/core/ecs/state';
import { Transform } from '../../../src/plugins/transforms';
import {
  COMPASS_PIP_MAX_DISTANCE,
  markTransform,
  selectCompassPips,
  waypointAzimuth,
} from '../../../src/plugins/hud/widgets/compass';
import {
  setTrackedWaypointId,
  setWaypoint,
  WAYPOINT_STYLES,
} from '../../../src/plugins/hud/waypoints';

function makeState(): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  return state;
}

describe('waypointAzimuth', () => {
  it('matches the camera convention: +Z is 0, +X is +90°', () => {
    expect(waypointAzimuth(0, 0, 0, 10)).toBeCloseTo(0, 5);
    expect(waypointAzimuth(0, 0, 10, 0)).toBeCloseTo(Math.PI / 2, 5);
    expect(waypointAzimuth(0, 0, -10, 0)).toBeCloseTo(-Math.PI / 2, 5);
    expect(Math.abs(waypointAzimuth(0, 0, 0, -10))).toBeCloseTo(Math.PI, 5);
  });

  it('is measured from the observer, not the origin', () => {
    expect(waypointAzimuth(100, 100, 100, 110)).toBeCloseTo(0, 5);
  });
});

describe('selectCompassPips', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('returns nothing when there are no markers', () => {
    expect(selectCompassPips(state, 0, 0)).toEqual([]);
  });

  it('carries the marker style onto the pip', () => {
    setWaypoint(state, {
      id: 'a',
      x: 0,
      y: 0,
      z: 20,
      kind: 'quest-available',
    });
    const [pip] = selectCompassPips(state, 0, 0);
    expect(pip.color).toBe(WAYPOINT_STYLES['quest-available'].color);
    expect(pip.glyph).toBe(WAYPOINT_STYLES['quest-available'].glyph);
    expect(pip.distance).toBeCloseTo(20, 5);
    expect(pip.az).toBeCloseTo(0, 5);
  });

  it('honours a per-marker style override', () => {
    setWaypoint(state, {
      id: 'a',
      x: 0,
      y: 0,
      z: 20,
      kind: 'poi',
      color: '#ff0000',
      glyph: '★',
    });
    const [pip] = selectCompassPips(state, 0, 0);
    expect(pip.color).toBe('#ff0000');
    expect(pip.glyph).toBe('★');
  });

  it('orders by distance and caps the list', () => {
    setWaypoint(state, { id: 'far', x: 0, y: 0, z: 150, kind: 'poi' });
    setWaypoint(state, { id: 'near', x: 0, y: 0, z: 5, kind: 'poi' });
    setWaypoint(state, { id: 'mid', x: 0, y: 0, z: 50, kind: 'poi' });

    expect(selectCompassPips(state, 0, 0).map((p) => p.id)).toEqual([
      'near',
      'mid',
      'far',
    ]);
    expect(selectCompassPips(state, 0, 0, 2).map((p) => p.id)).toEqual([
      'near',
      'mid',
    ]);
    expect(selectCompassPips(state, 0, 0, 0)).toEqual([]);
  });

  it('drops markers past the distance cap', () => {
    setWaypoint(state, { id: 'near', x: 0, y: 0, z: 10, kind: 'poi' });
    setWaypoint(state, {
      id: 'across-the-map',
      x: 0,
      y: 0,
      z: COMPASS_PIP_MAX_DISTANCE + 50,
      kind: 'poi',
    });
    expect(selectCompassPips(state, 0, 0).map((p) => p.id)).toEqual(['near']);
  });

  it('keeps the tracked marker at any distance, first in the list', () => {
    setWaypoint(state, { id: 'near', x: 0, y: 0, z: 10, kind: 'poi' });
    setWaypoint(state, { id: 'goal', x: 0, y: 0, z: 900, kind: 'objective' });
    setTrackedWaypointId(state, 'goal');

    const pips = selectCompassPips(state, 0, 0);
    expect(pips.map((p) => p.id)).toEqual(['goal', 'near']);
    expect(pips[0].tracked).toBe(true);
    expect(pips[1].tracked).toBe(false);
  });

  it('survives the row cap when pinned', () => {
    for (let i = 0; i < 8; i++) {
      setWaypoint(state, { id: `n${i}`, x: i, y: 0, z: 1, kind: 'poi' });
    }
    setWaypoint(state, { id: 'goal', x: 0, y: 0, z: 400, kind: 'objective' });
    setTrackedWaypointId(state, 'goal');
    expect(selectCompassPips(state, 0, 0, 2)[0].id).toBe('goal');
  });

  it('produces azimuths the strip transform can place', () => {
    setWaypoint(state, { id: 'east', x: 40, y: 0, z: 0, kind: 'objective' });
    const [pip] = selectCompassPips(state, 0, 0);
    // Camera facing east: the pip lands dead centre of the strip.
    const centred = markTransform(pip.az, Math.PI / 2, 1.7, 140);
    expect(centred.visible).toBe(true);
    expect(centred.translateX).toBeCloseTo(0, 5);
    // A marker behind the player falls outside the strip's field of view.
    setWaypoint(state, { id: 'south', x: 0, y: 0, z: -40, kind: 'objective' });
    const behind = selectCompassPips(state, 0, 0).find(
      (p) => p.id === 'south'
    )!;
    expect(markTransform(behind.az, 0, 1.7, 140).visible).toBe(false);
  });
});
