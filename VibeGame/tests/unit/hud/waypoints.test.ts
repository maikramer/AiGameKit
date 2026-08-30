import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../src/core/ecs/state';
import { Transform } from '../../../src/plugins/transforms';
import {
  clearWaypoints,
  formatWaypointDistance,
  getTrackedWaypoint,
  getTrackedWaypointId,
  getWaypoint,
  getWaypoints,
  refreshWaypointPositions,
  removeWaypoint,
  setTrackedWaypointId,
  setWaypoint,
  setWaypointAutoSelect,
  waypointColor,
  waypointDistance,
  waypointGlyph,
  WAYPOINT_STYLES,
} from '../../../src/plugins/hud/waypoints';

function makeState(): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  return state;
}

describe('waypoint registry', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('stores and reads back a marker', () => {
    setWaypoint(state, { id: 'a', x: 1, y: 2, z: 3, kind: 'objective' });
    const wp = getWaypoint(state, 'a');
    expect(wp).not.toBeNull();
    expect(wp!.x).toBe(1);
    expect(wp!.z).toBe(3);
    expect(getWaypoints(state).size).toBe(1);
  });

  it('updates in place instead of duplicating the id', () => {
    setWaypoint(state, { id: 'a', x: 1, y: 0, z: 0, kind: 'objective' });
    setWaypoint(state, { id: 'a', x: 9, y: 0, z: 0, kind: 'quest-turnin' });
    expect(getWaypoints(state).size).toBe(1);
    expect(getWaypoint(state, 'a')!.x).toBe(9);
    expect(getWaypoint(state, 'a')!.kind).toBe('quest-turnin');
  });

  it('clears only ids matching a prefix', () => {
    setWaypoint(state, { id: 'quest:a', x: 0, y: 0, z: 0, kind: 'objective' });
    setWaypoint(state, { id: 'quest:b', x: 0, y: 0, z: 0, kind: 'objective' });
    setWaypoint(state, { id: 'game:c', x: 0, y: 0, z: 0, kind: 'poi' });
    clearWaypoints(state, 'quest:');
    expect([...getWaypoints(state).keys()]).toEqual(['game:c']);
  });

  it('follows an entity and drops the marker when the entity is destroyed', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 5;
    Transform.posY[eid] = 6;
    Transform.posZ[eid] = 7;
    setWaypoint(state, {
      id: 'follow',
      x: 0,
      y: 0,
      z: 0,
      kind: 'objective',
      eid,
    });

    refreshWaypointPositions(state);
    expect(getWaypoint(state, 'follow')!.x).toBe(5);
    expect(getWaypoint(state, 'follow')!.z).toBe(7);

    Transform.posX[eid] = 50;
    refreshWaypointPositions(state);
    expect(getWaypoint(state, 'follow')!.x).toBe(50);

    state.destroyEntity(eid);
    refreshWaypointPositions(state);
    expect(getWaypoint(state, 'follow')).toBeNull();
  });
});

describe('waypoint tracking', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('returns null when there is nothing to point at', () => {
    expect(getTrackedWaypoint(state)).toBeNull();
  });

  it('prefers the pinned marker over priority', () => {
    setWaypoint(state, { id: 'low', x: 0, y: 0, z: 0, kind: 'poi' });
    setWaypoint(state, { id: 'high', x: 0, y: 0, z: 0, kind: 'quest-turnin' });
    setTrackedWaypointId(state, 'low');
    expect(getTrackedWaypoint(state)!.id).toBe('low');
  });

  it('falls back to the highest-priority marker when nothing is pinned', () => {
    setWaypoint(state, { id: 'poi', x: 0, y: 0, z: 0, kind: 'poi' });
    setWaypoint(state, {
      id: 'turnin',
      x: 80,
      y: 0,
      z: 0,
      kind: 'quest-turnin',
    });
    expect(WAYPOINT_STYLES['quest-turnin'].priority).toBeGreaterThan(
      WAYPOINT_STYLES.poi.priority
    );
    expect(getTrackedWaypoint(state)!.id).toBe('turnin');
  });

  it('breaks priority ties by distance from the given origin', () => {
    setWaypoint(state, { id: 'far', x: 100, y: 0, z: 0, kind: 'objective' });
    setWaypoint(state, { id: 'near', x: 4, y: 0, z: 0, kind: 'objective' });
    expect(getTrackedWaypoint(state, 0, 0)!.id).toBe('near');
    expect(getTrackedWaypoint(state, 120, 0)!.id).toBe('far');
  });

  it('unpins when the pinned marker is removed', () => {
    setWaypoint(state, { id: 'gone', x: 0, y: 0, z: 0, kind: 'objective' });
    setTrackedWaypointId(state, 'gone');
    removeWaypoint(state, 'gone');
    expect(getTrackedWaypointId(state)).toBeNull();
  });

  it('ignores a stale pin without hiding the arrow entirely', () => {
    setTrackedWaypointId(state, 'never-registered');
    setWaypoint(state, { id: 'real', x: 0, y: 0, z: 0, kind: 'objective' });
    expect(getTrackedWaypoint(state)!.id).toBe('real');
  });

  it('hides instead of auto-selecting while a pin awaits its marker', () => {
    setWaypoint(state, {
      id: 'new-quest',
      x: 0,
      y: 0,
      z: 0,
      kind: 'quest-available',
    });
    setWaypointAutoSelect(state, false);
    expect(getTrackedWaypoint(state)).toBeNull();

    setWaypointAutoSelect(state, true);
    expect(getTrackedWaypoint(state)!.id).toBe('new-quest');
  });
});

describe('waypoint presentation helpers', () => {
  it('falls back to the kind style and honours per-marker overrides', () => {
    const base = { id: 'x', x: 0, y: 0, z: 0, kind: 'objective' as const };
    expect(waypointColor(base)).toBe(WAYPOINT_STYLES.objective.color);
    expect(waypointGlyph(base)).toBe(WAYPOINT_STYLES.objective.glyph);
    expect(waypointColor({ ...base, color: '#123456' })).toBe('#123456');
    expect(waypointGlyph({ ...base, glyph: '★' })).toBe('★');
  });

  it('measures distance on the ground plane only', () => {
    const wp = { id: 'x', x: 3, y: 999, z: 4, kind: 'poi' as const };
    expect(waypointDistance(wp, 0, 0)).toBe(5);
  });

  it('formats metres and kilometres', () => {
    expect(formatWaypointDistance(12.4)).toBe('12 m');
    expect(formatWaypointDistance(999)).toBe('999 m');
    expect(formatWaypointDistance(1500)).toBe('1.5 km');
    expect(formatWaypointDistance(Number.NaN)).toBe('');
  });
});
