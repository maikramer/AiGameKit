import * as THREE from 'three';

/**
 * Minimal Reynolds-style steering vehicle (seek/flee/wander/obstacle
 * avoidance), replacing the former `yuka` dependency (unmaintained since
 * ~2023). Semantics mirror yuka's `Vehicle` + behaviors closely enough for
 * this project's use (planar movement — Y is owned by the caller): forces
 * accumulate, clamp to `maxForce`, integrate into `velocity`/`position`, and
 * `rotation` aligns to the movement direction each update.
 */

export interface ObstacleLike {
  position: THREE.Vector3;
  boundingRadius: number;
}

const DEFAULT_FORWARD = new THREE.Vector3(0, 0, 1);

export class SteeringVehicle {
  position = new THREE.Vector3();
  velocity = new THREE.Vector3();
  rotation = new THREE.Quaternion();
  maxSpeed = 1;
  maxForce = 1;

  seekActive = false;
  seekTarget = new THREE.Vector3();

  fleeActive = false;
  fleeTarget = new THREE.Vector3();
  fleePanicDistance = 500;

  wanderActive = false;
  wanderRadius = 1;
  wanderDistance = 5;
  wanderJitter = 5;
  private wanderTarget = new THREE.Vector3(1, 0, 0);

  obstacleActive = true;
  obstacleWeight = 1.5;
  obstacles: ObstacleLike[] = [];

  private _force = new THREE.Vector3();
  private _steer = new THREE.Vector3();
  private _desired = new THREE.Vector3();
  private _fwd = new THREE.Vector3();
  private _toObstacle = new THREE.Vector3();
  private _lateral = new THREE.Vector3();

  private forwardVector(out: THREE.Vector3): THREE.Vector3 {
    return out.copy(DEFAULT_FORWARD).applyQuaternion(this.rotation);
  }

  private seek(target: THREE.Vector3, out: THREE.Vector3): THREE.Vector3 {
    out.subVectors(target, this.position);
    if (out.lengthSq() > 1e-8) out.normalize().multiplyScalar(this.maxSpeed);
    return out.sub(this.velocity);
  }

  private flee(target: THREE.Vector3, out: THREE.Vector3): THREE.Vector3 {
    const panicSq = this.fleePanicDistance * this.fleePanicDistance;
    if (this.position.distanceToSquared(target) > panicSq)
      return out.set(0, 0, 0);
    out.subVectors(this.position, target);
    if (out.lengthSq() > 1e-8) out.normalize().multiplyScalar(this.maxSpeed);
    return out.sub(this.velocity);
  }

  private wander(dt: number, out: THREE.Vector3): THREE.Vector3 {
    const jitter = this.wanderJitter * dt;
    this.wanderTarget.x += (Math.random() * 2 - 1) * jitter;
    this.wanderTarget.z += (Math.random() * 2 - 1) * jitter;
    if (this.wanderTarget.lengthSq() < 1e-8) this.wanderTarget.set(1, 0, 0);
    this.wanderTarget.normalize().multiplyScalar(this.wanderRadius);

    this.forwardVector(this._fwd);
    out
      .copy(this._fwd)
      .multiplyScalar(this.wanderDistance)
      .add(this.wanderTarget);
    if (out.lengthSq() > 1e-8) out.normalize().multiplyScalar(this.maxSpeed);
    return out.sub(this.velocity);
  }

  /** Steer away from the nearest obstacle ahead within a speed-scaled lookahead. */
  private avoidObstacles(out: THREE.Vector3): THREE.Vector3 {
    out.set(0, 0, 0);
    if (this.obstacles.length === 0) return out;

    const speed = this.velocity.length();
    if (speed > 1e-4) this._fwd.copy(this.velocity).divideScalar(speed);
    else this.forwardVector(this._fwd);
    const lookahead = 1 + speed * 2;

    let closest: ObstacleLike | null = null;
    let closestForward = 0;

    for (const obstacle of this.obstacles) {
      this._toObstacle.subVectors(obstacle.position, this.position);
      const forwardDist = this._toObstacle.dot(this._fwd);
      if (forwardDist <= 0 || forwardDist > lookahead) continue;
      const lateralSq = this._toObstacle.lengthSq() - forwardDist * forwardDist;
      const threshold = obstacle.boundingRadius + 0.6;
      if (lateralSq > threshold * threshold) continue;
      if (!closest || forwardDist < closestForward) {
        closest = obstacle;
        closestForward = forwardDist;
      }
    }

    if (!closest) return out;
    this._toObstacle.subVectors(closest.position, this.position);
    this._lateral
      .copy(this._toObstacle)
      .sub(this._fwd.clone().multiplyScalar(closestForward));
    if (this._lateral.lengthSq() < 1e-8) {
      this._lateral.set(this._fwd.z, 0, -this._fwd.x);
    }
    this._lateral.normalize().multiplyScalar(-1);
    const urgency = 1 - closestForward / lookahead;
    return out.copy(this._lateral).multiplyScalar(this.maxForce * urgency);
  }

  update(dt: number): void {
    this._force.set(0, 0, 0);
    if (this.seekActive)
      this._force.add(this.seek(this.seekTarget, this._steer));
    if (this.fleeActive)
      this._force.add(this.flee(this.fleeTarget, this._steer));
    if (this.wanderActive) this._force.add(this.wander(dt, this._steer));
    if (this.obstacleActive) {
      this._force.addScaledVector(
        this.avoidObstacles(this._steer),
        this.obstacleWeight
      );
    }

    if (this._force.lengthSq() > this.maxForce * this.maxForce) {
      this._force.normalize().multiplyScalar(this.maxForce);
    }

    this.velocity.addScaledVector(this._force, dt);
    if (this.velocity.lengthSq() > this.maxSpeed * this.maxSpeed) {
      this.velocity.normalize().multiplyScalar(this.maxSpeed);
    }
    this.position.addScaledVector(this.velocity, dt);

    if (this.velocity.lengthSq() > 1e-6) {
      this._desired.copy(this.velocity).normalize();
      this.rotation.setFromUnitVectors(DEFAULT_FORWARD, this._desired);
    }
  }
}
