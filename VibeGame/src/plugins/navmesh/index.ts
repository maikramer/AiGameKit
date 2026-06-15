import type * as THREE from 'three';
import type { State } from '../../core';
import { NavMeshAgent } from './components';
import { Transform } from '../transforms/components';
import {
  _getActiveRuntime,
  getNavMeshRuntime,
  stateToRuntime,
} from './systems';

export { NavMeshAgent, NavMeshSurface, NavMeshWalkable } from './components';
export { collectNavmeshGeometry } from './geometry';
export type { NavMeshGeometry } from './geometry';
export { NavMeshPlugin } from './plugin';
export {
  navMeshAgentRecipe,
  navMeshRecipe,
  navMeshWalkableRecipe,
} from './recipes';
export { NavMeshAgentSystem, NavMeshInitSystem } from './systems';
export type { NavMeshRuntime } from './systems';

export function isNavMeshReady(): boolean {
  return _getActiveRuntime()?.ready ?? false;
}

export interface AgentConfig {
  speed?: number;
  radius?: number;
  height?: number;
}

export function createAgent(
  state: State,
  eid: number,
  config?: AgentConfig
): number {
  const rt = getNavMeshRuntime(state);
  if (!rt.ready || !rt.crowd) return -1;

  if (config) {
    if (config.speed !== undefined) NavMeshAgent.speed[eid] = config.speed;
    if (config.radius !== undefined) NavMeshAgent.radius[eid] = config.radius;
    if (config.height !== undefined) NavMeshAgent.height[eid] = config.height;
  }

  const existing = rt.agents.get(eid);
  if (existing) return existing.agentIndex;

  const radius = NavMeshAgent.radius[eid] || 0.4;
  const height = NavMeshAgent.height[eid] || 1.0;
  const maxSpeed = NavMeshAgent.speed[eid] || 3.0;

  const pos = {
    x: Transform.posX[eid],
    y: Transform.posY[eid],
    z: Transform.posZ[eid],
  };

  const agent = rt.crowd.addAgent(pos, {
    radius,
    height,
    maxAcceleration: 8.0,
    maxSpeed,
    collisionQueryRange: radius * 5,
    pathOptimizationRange: 2.0,
    separationWeight: 1.0,
  });

  NavMeshAgent.agentIndex[eid] = agent.agentIndex;
  rt.agents.set(eid, agent);
  return agent.agentIndex;
}

export function setAgentTarget(
  state: State,
  eid: number,
  x: number,
  y: number,
  z: number
): void {
  const rt = getNavMeshRuntime(state);
  if (!rt.ready) return;
  NavMeshAgent.targetX[eid] = x;
  NavMeshAgent.targetY[eid] = y;
  NavMeshAgent.targetZ[eid] = z;
  NavMeshAgent.hasTarget[eid] = 1;
}

export function clearAgentTarget(state: State, eid: number): void {
  NavMeshAgent.hasTarget[eid] = 0;
  const rt = stateToRuntime.get(state);
  const agent = rt?.agents.get(eid);
  agent?.resetMoveTarget();
}

export function removeAgent(state: State, eid: number): void {
  const rt = stateToRuntime.get(state);
  if (!rt || !rt.crowd) return;
  const agent = rt.agents.get(eid);
  if (agent) {
    rt.crowd.removeAgent(agent);
    rt.agents.delete(eid);
  }
  NavMeshAgent.agentIndex[eid] = -1;
}

export function getAgentPosition(
  eid: number
): { x: number; y: number; z: number } | null {
  const rt = _getActiveRuntime();
  const agent = rt?.agents.get(eid);
  if (!agent) return null;
  return agent.position();
}

export async function getNavMeshDebugMesh(): Promise<THREE.Object3D | null> {
  const rt = _getActiveRuntime();
  if (!rt?.navMesh) return null;
  const { NavMeshHelper } = await import('@recast-navigation/three');
  return new NavMeshHelper(rt.navMesh);
}
