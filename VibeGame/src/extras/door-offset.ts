/**
 * Local-space door → world XZ offset for building portals.
 *
 * Convention: building origin at feet/center; `faceYawDeg` 0 = outward along
 * local +Z (Omni eye-level 3/4 “front”). Apply building yaw with the same
 * Y-up rotation as Three.js / VibeGame Transform.eulerY.
 */

export interface DoorLocal {
  /** Door center on the facade, local X (metres). */
  localX: number;
  /** Door center on the facade, local Z (metres). */
  localZ: number;
  /**
   * Outward face normal yaw in local degrees.
   * 0 = +Z, 90 = +X, 180 = −Z, −90 = −X.
   */
  faceYawDeg: number;
  /** Metres to stand outside the door along the face normal. */
  standOff: number;
}

export interface Vec2 {
  x: number;
  z: number;
}

/** Rotate local XZ by yaw degrees (Y-up, Three.js convention). */
export function rotateYawXZ(x: number, z: number, yawDeg: number): Vec2 {
  const rad = (yawDeg * Math.PI) / 180;
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  return {
    x: x * c + z * s,
    z: -x * s + z * c,
  };
}

/**
 * World-space offset from building origin to a stand-in-front-of-door point.
 * Add to the building's world place XZ.
 */
export function doorWorldOffset(door: DoorLocal, buildingYawDeg: number): Vec2 {
  const faceRad = (door.faceYawDeg * Math.PI) / 180;
  const fx = Math.sin(faceRad);
  const fz = Math.cos(faceRad);
  const lx = door.localX + fx * door.standOff;
  const lz = door.localZ + fz * door.standOff;
  return rotateYawXZ(lx, lz, buildingYawDeg);
}

/** Building place + door offset → absolute world XZ. */
export function doorWorldPosition(
  buildingX: number,
  buildingZ: number,
  buildingYawDeg: number,
  door: DoorLocal
): Vec2 {
  const off = doorWorldOffset(door, buildingYawDeg);
  return { x: buildingX + off.x, z: buildingZ + off.z };
}
