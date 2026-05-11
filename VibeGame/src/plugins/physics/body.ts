import * as RAPIER from "@dimforge/rapier3d-simd-compat";
import { Rigidbody, Collider, BodyType, ColliderShape } from "./components";

export function createRapierBody(entity: number): RAPIER.RigidBodyDesc {
  const type = Rigidbody.type[entity] ?? BodyType.Dynamic;

  let desc: RAPIER.RigidBodyDesc;
  switch (type) {
    case BodyType.Fixed:
      desc = RAPIER.RigidBodyDesc.fixed();
      break;
    case BodyType.Dynamic:
    default:
      desc = RAPIER.RigidBodyDesc.dynamic();
      break;
  }

  desc.setTranslation(
    Rigidbody.posX[entity] ?? 0,
    Rigidbody.posY[entity] ?? 0,
    Rigidbody.posZ[entity] ?? 0,
  );

  const mass = Rigidbody.mass[entity];
  if (type === BodyType.Dynamic && mass !== undefined && mass > 0) {
    desc.setAdditionalMass(mass);
  }

  const gravityScale = Rigidbody.gravityScale[entity];
  if (gravityScale !== undefined && gravityScale !== 1) {
    desc.setGravityScale(gravityScale);
  }

  const lockRx = Rigidbody.lockRotX[entity];
  const lockRy = Rigidbody.lockRotY[entity];
  const lockRz = Rigidbody.lockRotZ[entity];
  if (lockRx || lockRy || lockRz) {
    desc.enabledRotations(!lockRx, !lockRy, !lockRz);
  }

  return desc;
}

export function createRapierColliderDesc(entity: number): RAPIER.ColliderDesc {
  const shape = Collider.shape[entity] ?? ColliderShape.Box;

  let desc: RAPIER.ColliderDesc;
  switch (shape) {
    case ColliderShape.Sphere:
      desc = RAPIER.ColliderDesc.ball(Collider.radius[entity] || 0.5);
      break;
    case ColliderShape.Capsule:
      desc = RAPIER.ColliderDesc.capsule(
        (Collider.height[entity] || 1) / 2,
        Collider.radius[entity] || 0.5,
      );
      break;
    case ColliderShape.Box:
    default:
      desc = RAPIER.ColliderDesc.cuboid(
        (Collider.sizeX[entity] || 1) / 2,
        (Collider.sizeY[entity] || 1) / 2,
        (Collider.sizeZ[entity] || 1) / 2,
      );
      break;
  }

  desc.setFriction(Collider.friction[entity] ?? 0.5);
  desc.setRestitution(Collider.restitution[entity] ?? 0);

  const sensor = Collider.sensor[entity] || 0;
  if (sensor) {
    desc.setSensor(true);
    desc.setDensity(0);
  } else {
    desc.setDensity(Collider.density[entity] ?? 1);
  }

  const groups = Collider.membershipGroups[entity] || 0xffff;
  const filter = Collider.filterGroups[entity] || 0xffff;
  desc.setCollisionGroups((groups & 0xffff) | ((filter & 0xffff) << 16));

  desc.setTranslation(
    Collider.posOffsetX[entity] || 0,
    Collider.posOffsetY[entity] || 0,
    Collider.posOffsetZ[entity] || 0,
  );

  return desc;
}
