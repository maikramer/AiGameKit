/** Timestamped snapshot of position, rotation (quaternion), and scale. */
export interface TransformSnapshot {
  timestamp: number;
  posX: number;
  posY: number;
  posZ: number;
  rotX: number;
  rotY: number;
  rotZ: number;
  rotW: number;
  scaleX: number;
  scaleY: number;
  scaleZ: number;
}

export class JitterBuffer {
  private buffer: TransformSnapshot[] = [];
  private targetDelay: number;

  constructor(targetDelay = 100) {
    this.targetDelay = targetDelay;
  }

  push(snapshot: TransformSnapshot): void {
    this.buffer.push(snapshot);
    this.buffer.sort((a, b) => a.timestamp - b.timestamp);
  }

  sample(renderTime: number): TransformSnapshot | null {
    this.discard(renderTime);
    if (this.buffer.length < 2) return this.buffer[0] ?? null;

    const targetTime = renderTime - this.targetDelay;

    let older = this.buffer[0];
    let newer = this.buffer[1];

    for (let i = 1; i < this.buffer.length; i++) {
      if (this.buffer[i].timestamp <= targetTime) {
        older = this.buffer[i];
        newer = this.buffer[i + 1] ?? this.buffer[i];
      } else {
        break;
      }
    }

    const t = newer.timestamp === older.timestamp
      ? 1
      : (targetTime - older.timestamp) / (newer.timestamp - older.timestamp);

    const alpha = Math.max(0, Math.min(1, t));

    return lerpSnapshot(older, newer, alpha);
  }

  discard(renderTime: number): void {
    const cutoff = renderTime - this.targetDelay;
    while (this.buffer.length > 1 && this.buffer[0].timestamp < cutoff) {
      this.buffer.shift();
    }
  }

  clear(): void {
    this.buffer.length = 0;
  }

  get length(): number {
    return this.buffer.length;
  }
}

function lerpSnapshot(a: TransformSnapshot, b: TransformSnapshot, t: number): TransformSnapshot {
  const rot = slerpQuaternion(a, b, t);
  return {
    timestamp: a.timestamp + (b.timestamp - a.timestamp) * t,
    posX: a.posX + (b.posX - a.posX) * t,
    posY: a.posY + (b.posY - a.posY) * t,
    posZ: a.posZ + (b.posZ - a.posZ) * t,
    rotX: rot.rotX,
    rotY: rot.rotY,
    rotZ: rot.rotZ,
    rotW: rot.rotW,
    scaleX: a.scaleX + (b.scaleX - a.scaleX) * t,
    scaleY: a.scaleY + (b.scaleY - a.scaleY) * t,
    scaleZ: a.scaleZ + (b.scaleZ - a.scaleZ) * t,
  };
}

function slerpQuaternion(
  a: { rotX: number; rotY: number; rotZ: number; rotW: number },
  b: { rotX: number; rotY: number; rotZ: number; rotW: number },
  t: number,
): { rotX: number; rotY: number; rotZ: number; rotW: number } {
  let dot = a.rotX * b.rotX + a.rotY * b.rotY + a.rotZ * b.rotZ + a.rotW * b.rotW;

  let bx = b.rotX;
  let by = b.rotY;
  let bz = b.rotZ;
  let bw = b.rotW;

  if (dot < 0) {
    dot = -dot;
    bx = -bx;
    by = -by;
    bz = -bz;
    bw = -bw;
  }

  if (dot > 0.9995) {
    return {
      rotX: a.rotX + (bx - a.rotX) * t,
      rotY: a.rotY + (by - a.rotY) * t,
      rotZ: a.rotZ + (bz - a.rotZ) * t,
      rotW: a.rotW + (bw - a.rotW) * t,
    };
  }

  const theta0 = Math.acos(dot);
  const theta = theta0 * t;
  const sinTheta = Math.sin(theta);
  const sinTheta0 = Math.sin(theta0);

  const wa = Math.cos(theta) - dot * sinTheta / sinTheta0;
  const wb = sinTheta / sinTheta0;

  return {
    rotX: wa * a.rotX + wb * bx,
    rotY: wa * a.rotY + wb * by,
    rotZ: wa * a.rotZ + wb * bz,
    rotW: wa * a.rotW + wb * bw,
  };
}
