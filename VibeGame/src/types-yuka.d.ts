declare module 'yuka' {
  export class Vector3 {
    x: number;
    y: number;
    z: number;
    constructor(x?: number, y?: number, z?: number);
    set(x: number, y: number, z: number): this;
    copy(v: Vector3): this;
    clone(): Vector3;
  }

  export class Quaternion {
    x: number;
    y: number;
    z: number;
    w: number;
    constructor(x?: number, y?: number, z?: number, w?: number);
    copy(q: Quaternion): this;
    clone(): Quaternion;
  }

  export class Matrix4 {
    elements: Float64Array;
    constructor();
  }

  export class Time {
    elapsedTime: number;
    deltaTime: number;
    constructor();
    update(): void;
  }

  export abstract class SteeringBehavior {
    active: boolean;
    calculate(vehicle: Vehicle, force: Vector3): Vector3;
  }

  export class SeekBehavior extends SteeringBehavior {
    target: Vector3;
    constructor(target?: Vector3);
  }

  export class FleeBehavior extends SteeringBehavior {
    target: Vector3;
    panicDistance: number;
    constructor(target?: Vector3, panicDistance?: number);
  }

  export class WanderBehavior extends SteeringBehavior {
    wanderRadius: number;
    wanderDistance: number;
    wanderJitter: number;
    constructor();
  }

  export class ObstacleAvoidanceBehavior extends SteeringBehavior {
    maxSightRange: number;
    obstacles: unknown[];
    constructor(obstacles?: unknown[]);
  }

  export class SteeringManager {
    add(behavior: SteeringBehavior): this;
    remove(behavior: SteeringBehavior): this;
    clear(): this;
    behaviors: SteeringBehavior[];
  }

  export class Vehicle {
    position: Vector3;
    rotation: Quaternion;
    maxSpeed: number;
    maxForce: number;
    velocity: Vector3;
    steering: SteeringManager;
    constructor();
    update(delta: number): this;
  }
}
