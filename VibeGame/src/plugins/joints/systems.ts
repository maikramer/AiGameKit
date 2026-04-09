import { JointData, RotationOps, Vector3 } from '@dimforge/rapier3d-compat';
import type { ImpulseJoint } from '@dimforge/rapier3d-compat';
import { defineQuery, type System } from '../../core';
import { Body } from '../physics/components';
import {
  getPhysicsContext,
  PhysicsInitializationSystem,
} from '../physics/systems';
import { PhysicsJoint } from './components';
import { getJointHandles } from './context';

const jointQuery = defineQuery([PhysicsJoint]);

function buildJointData(eid: number): JointData | null {
  const a1 = new Vector3(
    PhysicsJoint.anchorAX[eid],
    PhysicsJoint.anchorAY[eid],
    PhysicsJoint.anchorAZ[eid]
  );
  const a2 = new Vector3(
    PhysicsJoint.anchorBX[eid],
    PhysicsJoint.anchorBY[eid],
    PhysicsJoint.anchorBZ[eid]
  );
  const axis = new Vector3(
    PhysicsJoint.axisX[eid],
    PhysicsJoint.axisY[eid],
    PhysicsJoint.axisZ[eid]
  );
  const t = PhysicsJoint.jointType[eid];
  const frame = RotationOps.identity();

  switch (t) {
    case 0:
      return JointData.fixed(a1, frame, a2, frame);
    case 1:
      return JointData.revolute(a1, a2, axis);
    case 2:
      return JointData.prismatic(a1, a2, axis);
    case 3:
      return JointData.spherical(a1, a2);
    case 4:
      return JointData.rope(PhysicsJoint.ropeLength[eid] || 1, a1, a2);
    case 5:
      return JointData.spring(
        PhysicsJoint.limitsMax[eid] || 1,
        PhysicsJoint.springStiffness[eid] || 10,
        PhysicsJoint.springDamping[eid] || 1,
        a1,
        a2
      );
    default:
      return null;
  }
}

export const JointCreateSystem: System = {
  group: 'fixed',
  after: [PhysicsInitializationSystem],
  update: (state) => {
    const ctx = getPhysicsContext(state);
    const world = ctx.physicsWorld;
    if (!world) return;

    const handles = getJointHandles(state);

    for (const eid of jointQuery(state.world)) {
      if (PhysicsJoint.created[eid]) continue;

      const ba = PhysicsJoint.bodyA[eid];
      const bb = PhysicsJoint.bodyB[eid];
      if (!ba || !bb) continue;

      const rbA = ctx.entityToRigidbody.get(ba);
      const rbB = ctx.entityToRigidbody.get(bb);
      if (!rbA || !rbB) continue;
      if (!state.hasComponent(ba, Body) || !state.hasComponent(bb, Body))
        continue;

      const desc = buildJointData(eid);
      if (!desc) continue;

      let joint: ImpulseJoint;
      try {
        joint = world.createImpulseJoint(desc, rbA, rbB, true);
      } catch {
        continue;
      }

      handles.set(eid, joint);
      PhysicsJoint.created[eid] = 1;
    }
  },
};
