import { JointData, RotationOps, Vector3 } from '@dimforge/rapier3d-compat';
import type { ImpulseJoint } from '@dimforge/rapier3d-compat';
import { defineQuery, type System } from '../../core';
import { Rigidbody } from '../physics/components';
import {
  getPhysicsContext,
  PhysicsInitializationSystem,
} from '../physics/systems';
import { Joint } from './components';
import { getJointHandles } from './context';

const jointQuery = defineQuery([Joint]);

function buildJointData(eid: number): JointData | null {
  const a1 = new Vector3(
    Joint.anchorAX[eid],
    Joint.anchorAY[eid],
    Joint.anchorAZ[eid]
  );
  const a2 = new Vector3(
    Joint.anchorBX[eid],
    Joint.anchorBY[eid],
    Joint.anchorBZ[eid]
  );
  const axis = new Vector3(
    Joint.axisX[eid],
    Joint.axisY[eid],
    Joint.axisZ[eid]
  );
  const t = Joint.jointType[eid];
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
      return JointData.rope(Joint.ropeLength[eid] || 1, a1, a2);
    case 5:
      return JointData.spring(
        Joint.limitsMax[eid] || 1,
        Joint.springStiffness[eid] || 10,
        Joint.springDamping[eid] || 1,
        a1,
        a2
      );
    default:
      return null;
  }
}

interface UnitJoint extends ImpulseJoint {
  setLimits(min: number, max: number): void;
  configureMotorVelocity(targetVel: number, factor: number): void;
}

function configureJoint(joint: ImpulseJoint, eid: number): void {
  if (!('setLimits' in joint)) return;

  const unitJoint = joint as unknown as UnitJoint;

  const limitsMin = Joint.limitsMin[eid];
  const limitsMax = Joint.limitsMax[eid];
  if (limitsMin !== 0 || limitsMax !== 0) {
    unitJoint.setLimits(limitsMin, limitsMax);
  }

  const motorSpeed = Joint.motorSpeed[eid];
  const motorMaxForce = Joint.motorMaxForce[eid];
  if (motorMaxForce > 0) {
    unitJoint.configureMotorVelocity(motorSpeed, motorMaxForce);
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
      if (Joint.created[eid]) continue;

      const ba = Joint.bodyA[eid];
      const bb = Joint.bodyB[eid];
      if (!ba || !bb) continue;

      const rbA = ctx.entityToRigidbody.get(ba);
      const rbB = ctx.entityToRigidbody.get(bb);
      if (!rbA || !rbB) continue;
      if (!state.hasComponent(ba, Rigidbody) || !state.hasComponent(bb, Rigidbody))
        continue;

      const desc = buildJointData(eid);
      if (!desc) continue;

      let joint: ImpulseJoint;
      try {
        joint = world.createImpulseJoint(desc, rbA, rbB, true);
      } catch (e) {
        console.warn('[joints] failed to create joint:', e);
        continue;
      }

      configureJoint(joint, eid);
      handles.set(eid, joint);
      Joint.created[eid] = 1;
    }
  },
};

export const JointCleanupSystem: System = {
  group: 'fixed',
  after: [JointCreateSystem],
  update: (state) => {
    const ctx = getPhysicsContext(state);
    const world = ctx.physicsWorld;
    if (!world) return;

    const handles = getJointHandles(state);

    for (const [eid, joint] of handles) {
      if (!state.hasComponent(eid, Joint)) {
        world.removeImpulseJoint(joint, true);
        handles.delete(eid);
      }
    }
  },
};
