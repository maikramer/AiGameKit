import { hasComponent } from 'bitecs';
import {
  FleeBehavior,
  GameEntity,
  ObstacleAvoidanceBehavior,
  SeekBehavior,
  Vector3,
  Vehicle,
  WanderBehavior,
} from 'yuka';
import { defineQuery, type State, type System } from '../../core';
import { BodyType, Collider, Rigidbody } from '../physics/components';
import { Transform, WorldTransform } from '../transforms';
import { SteeringAgent, SteeringTarget } from './components';
import { getSteeringMap, type SteeringRow } from './context';

const steerQuery = defineQuery([SteeringAgent, SteeringTarget, Transform]);
const obstacleQuery = defineQuery([Rigidbody, Collider, Transform]);
const _obstacleCache: GameEntity[] = [];

function ensureVehicle(state: State, eid: number): SteeringRow {
  const map = getSteeringMap(state);
  let row = map.get(eid);
  if (row) return row;

  const vehicle = new Vehicle();
  const seek = new SeekBehavior(new Vector3());
  const flee = new FleeBehavior(new Vector3(), 500);
  const wander = new WanderBehavior();
  const obstacle = new ObstacleAvoidanceBehavior([]);

  vehicle.steering.add(seek);
  vehicle.steering.add(flee);
  vehicle.steering.add(wander);
  vehicle.steering.add(obstacle);

  seek.active = true;
  flee.active = false;
  wander.active = false;
  obstacle.active = true;
  obstacle.weight = 1.5;

  row = { vehicle, seek, flee, wander, obstacle };
  map.set(eid, row);
  return row;
}

function syncFromEcs(eid: number, row: SteeringRow): void {
  const v = row.vehicle;
  v.position.x = Transform.posX[eid];
  v.position.y = Transform.posY[eid];
  v.position.z = Transform.posZ[eid];
}

function syncTarget(state: State, eid: number, row: SteeringRow): void {
  const te = SteeringTarget.targetEntity[eid];
  let tx = SteeringTarget.targetX[eid];
  let ty = SteeringTarget.targetY[eid];
  let tz = SteeringTarget.targetZ[eid];
  if (te > 0 && state.exists(te) && hasComponent(state.world, Transform, te)) {
    tx = Transform.posX[te];
    ty = Transform.posY[te];
    tz = Transform.posZ[te];
  }
  row.seek!.target.set(tx, ty, tz);
  row.flee!.target.set(tx, ty, tz);
}

function applyBehavior(eid: number, row: SteeringRow): void {
  const b = SteeringAgent.behavior[eid];
  row.seek!.active = b === 0;
  row.wander!.active = b === 1;
  row.flee!.active = b === 2;
}

export const SteeringSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime || 1 / 60;

    let obstacleCount = 0;
    for (const eid of obstacleQuery(state.world)) {
      if (Rigidbody.type[eid] !== BodyType.Fixed) continue;
      let ge = _obstacleCache[obstacleCount];
      if (!ge) {
        ge = new GameEntity();
        _obstacleCache[obstacleCount] = ge;
      }
      ge.position.set(
        Transform.posX[eid],
        Transform.posY[eid],
        Transform.posZ[eid]
      );
      const r = Collider.radius[eid];
      ge.boundingRadius =
        r > 0
          ? r
          : Math.max(
              Collider.sizeX[eid],
              Collider.sizeY[eid],
              Collider.sizeZ[eid]
            ) / 2;
      obstacleCount++;
    }
    const obstacles = _obstacleCache.slice(0, obstacleCount);

    for (const eid of steerQuery(state.world)) {
      if (!SteeringAgent.active[eid]) continue;

      const row = ensureVehicle(state, eid);
      row.vehicle.maxSpeed = SteeringAgent.maxSpeed[eid];
      row.vehicle.maxForce = SteeringAgent.maxForce[eid];
      syncFromEcs(eid, row);
      syncTarget(state, eid, row);
      applyBehavior(eid, row);
      row.obstacle!.obstacles = obstacles;

      row.vehicle.update(dt);

      Transform.posX[eid] = row.vehicle.position.x;
      Transform.posY[eid] = row.vehicle.position.y;
      Transform.posZ[eid] = row.vehicle.position.z;
      Transform.rotX[eid] = row.vehicle.rotation.x;
      Transform.rotY[eid] = row.vehicle.rotation.y;
      Transform.rotZ[eid] = row.vehicle.rotation.z;
      Transform.rotW[eid] = row.vehicle.rotation.w;

      if (hasComponent(state.world, WorldTransform, eid)) {
        WorldTransform.posX[eid] = Transform.posX[eid];
        WorldTransform.posY[eid] = Transform.posY[eid];
        WorldTransform.posZ[eid] = Transform.posZ[eid];
        WorldTransform.rotX[eid] = Transform.rotX[eid];
        WorldTransform.rotY[eid] = Transform.rotY[eid];
        WorldTransform.rotZ[eid] = Transform.rotZ[eid];
        WorldTransform.rotW[eid] = Transform.rotW[eid];
      }
    }
  },
};
