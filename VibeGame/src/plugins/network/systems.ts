import { Client, type Room } from 'colyseus.js';
import { defineQuery, type System } from '../../core';
import { Transform } from '../transforms';
import { NetworkBuffer, Networked, NetworkStatus } from './components';
import { getNetworkContext } from './context';
import { JitterBuffer, type TransformSnapshot } from './jitter-buffer';

const netQuery = defineQuery([Networked, Transform]);
const statusQuery = defineQuery([NetworkStatus]);

const jitterBuffers = new Map<number, JitterBuffer>();

function setStatus(world: import('bitecs').IWorld, status: number): void {
  for (const eid of statusQuery(world)) {
    NetworkStatus.connected[eid] = status;
  }
}

interface TransformMessage {
  eid: number;
  x: number;
  y: number;
  z: number;
  rotX?: number;
  rotY?: number;
  rotZ?: number;
  rotW?: number;
  scaleX?: number;
  scaleY?: number;
  scaleZ?: number;
  timestamp?: number;
}

function toSnapshot(
  msg: TransformMessage,
  fallbackTime: number
): TransformSnapshot {
  return {
    timestamp: msg.timestamp ?? fallbackTime,
    posX: msg.x,
    posY: msg.y,
    posZ: msg.z,
    rotX: msg.rotX ?? 0,
    rotY: msg.rotY ?? 0,
    rotZ: msg.rotZ ?? 0,
    rotW: msg.rotW ?? 1,
    scaleX: msg.scaleX ?? 1,
    scaleY: msg.scaleY ?? 1,
    scaleZ: msg.scaleZ ?? 1,
  };
}

function getJitterBuffer(eid: number): JitterBuffer {
  let jb = jitterBuffers.get(eid);
  if (!jb) {
    jb = new JitterBuffer(100);
    jitterBuffers.set(eid, jb);
  }
  return jb;
}

export const NetworkConnectSystem: System = {
  group: 'setup',
  update: (state) => {
    if (state.headless) return;
    const ctx = getNetworkContext(state);
    if (!ctx.url || ctx.room) return;

    setStatus(state.world, 1);

    const client = new Client(ctx.url);
    client
      .joinOrCreate(ctx.roomName)
      .then((room: Room) => {
        ctx.room = room;
        setStatus(state.world, 2);
        room.onMessage('transform', (msg: TransformMessage) => {
          for (const eid of netQuery(state.world)) {
            if (msg.eid !== eid || Networked.isOwner[eid]) continue;

            const snapshot = toSnapshot(msg, performance.now());
            const jb = getJitterBuffer(eid);
            jb.push(snapshot);

            NetworkBuffer.prevX[eid] = NetworkBuffer.nextX[eid];
            NetworkBuffer.prevY[eid] = NetworkBuffer.nextY[eid];
            NetworkBuffer.prevZ[eid] = NetworkBuffer.nextZ[eid];
            NetworkBuffer.prevRotX[eid] = NetworkBuffer.nextRotX[eid];
            NetworkBuffer.prevRotY[eid] = NetworkBuffer.nextRotY[eid];
            NetworkBuffer.prevRotZ[eid] = NetworkBuffer.nextRotZ[eid];
            NetworkBuffer.prevRotW[eid] = NetworkBuffer.nextRotW[eid];
            NetworkBuffer.prevScaleX[eid] = NetworkBuffer.nextScaleX[eid];
            NetworkBuffer.prevScaleY[eid] = NetworkBuffer.nextScaleY[eid];
            NetworkBuffer.prevScaleZ[eid] = NetworkBuffer.nextScaleZ[eid];
            NetworkBuffer.nextX[eid] = msg.x;
            NetworkBuffer.nextY[eid] = msg.y;
            NetworkBuffer.nextZ[eid] = msg.z;
            NetworkBuffer.nextRotX[eid] = msg.rotX ?? 0;
            NetworkBuffer.nextRotY[eid] = msg.rotY ?? 0;
            NetworkBuffer.nextRotZ[eid] = msg.rotZ ?? 0;
            NetworkBuffer.nextRotW[eid] = msg.rotW ?? 1;
            NetworkBuffer.nextScaleX[eid] = msg.scaleX ?? 1;
            NetworkBuffer.nextScaleY[eid] = msg.scaleY ?? 1;
            NetworkBuffer.nextScaleZ[eid] = msg.scaleZ ?? 1;
          }
        });
      })
      .catch((err: unknown) => {
        setStatus(state.world, 3);
        console.warn('[network]', 'connection failed:', err);
      });
  },
};

export const NetworkSendSystem: System = {
  group: 'simulation',
  last: true,
  update: (state) => {
    const ctx = getNetworkContext(state);
    if (!ctx.room) return;

    for (const eid of netQuery(state.world)) {
      if (!Networked.isOwner[eid]) continue;
      ctx.room.send('transform', {
        eid,
        x: Transform.posX[eid],
        y: Transform.posY[eid],
        z: Transform.posZ[eid],
        rotX: Transform.rotX[eid],
        rotY: Transform.rotY[eid],
        rotZ: Transform.rotZ[eid],
        rotW: Transform.rotW[eid],
        scaleX: Transform.scaleX[eid],
        scaleY: Transform.scaleY[eid],
        scaleZ: Transform.scaleZ[eid],
        timestamp: performance.now(),
      });
    }
  },
};

function slerp(
  ax: number,
  ay: number,
  az: number,
  aw: number,
  bx: number,
  by: number,
  bz: number,
  bw: number,
  t: number
): [number, number, number, number] {
  let dot = ax * bx + ay * by + az * bz + aw * bw;

  if (dot < 0) {
    dot = -dot;
    bx = -bx;
    by = -by;
    bz = -bz;
    bw = -bw;
  }

  if (dot > 0.9995) {
    return [
      ax + (bx - ax) * t,
      ay + (by - ay) * t,
      az + (bz - az) * t,
      aw + (bw - aw) * t,
    ];
  }

  const theta0 = Math.acos(dot);
  const theta = theta0 * t;
  const sinTheta = Math.sin(theta);
  const sinTheta0 = Math.sin(theta0);
  const wa = Math.cos(theta) - (dot * sinTheta) / sinTheta0;
  const wb = sinTheta / sinTheta0;

  return [
    wa * ax + wb * bx,
    wa * ay + wb * by,
    wa * az + wb * bz,
    wa * aw + wb * bw,
  ];
}

export const NetworkInterpolationSystem: System = {
  group: 'draw',
  update: (state) => {
    const renderTime = performance.now();

    for (const eid of netQuery(state.world)) {
      if (Networked.isOwner[eid]) continue;
      if (!Networked.interpolate[eid]) continue;

      const jb = jitterBuffers.get(eid);
      if (jb && jb.length >= 2) {
        const sample = jb.sample(renderTime);
        if (sample) {
          Transform.posX[eid] = sample.posX;
          Transform.posY[eid] = sample.posY;
          Transform.posZ[eid] = sample.posZ;
          Transform.rotX[eid] = sample.rotX;
          Transform.rotY[eid] = sample.rotY;
          Transform.rotZ[eid] = sample.rotZ;
          Transform.rotW[eid] = sample.rotW;
          Transform.scaleX[eid] = sample.scaleX;
          Transform.scaleY[eid] = sample.scaleY;
          Transform.scaleZ[eid] = sample.scaleZ;
          continue;
        }
      }

      const t = Math.min(1, state.time.deltaTime * 10);

      Transform.posX[eid] =
        NetworkBuffer.prevX[eid] +
        (NetworkBuffer.nextX[eid] - NetworkBuffer.prevX[eid]) * t;
      Transform.posY[eid] =
        NetworkBuffer.prevY[eid] +
        (NetworkBuffer.nextY[eid] - NetworkBuffer.prevY[eid]) * t;
      Transform.posZ[eid] =
        NetworkBuffer.prevZ[eid] +
        (NetworkBuffer.nextZ[eid] - NetworkBuffer.prevZ[eid]) * t;

      const [rx, ry, rz, rw] = slerp(
        NetworkBuffer.prevRotX[eid],
        NetworkBuffer.prevRotY[eid],
        NetworkBuffer.prevRotZ[eid],
        NetworkBuffer.prevRotW[eid],
        NetworkBuffer.nextRotX[eid],
        NetworkBuffer.nextRotY[eid],
        NetworkBuffer.nextRotZ[eid],
        NetworkBuffer.nextRotW[eid],
        t
      );
      Transform.rotX[eid] = rx;
      Transform.rotY[eid] = ry;
      Transform.rotZ[eid] = rz;
      Transform.rotW[eid] = rw;

      Transform.scaleX[eid] =
        NetworkBuffer.prevScaleX[eid] +
        (NetworkBuffer.nextScaleX[eid] - NetworkBuffer.prevScaleX[eid]) * t;
      Transform.scaleY[eid] =
        NetworkBuffer.prevScaleY[eid] +
        (NetworkBuffer.nextScaleY[eid] - NetworkBuffer.prevScaleY[eid]) * t;
      Transform.scaleZ[eid] =
        NetworkBuffer.prevScaleZ[eid] +
        (NetworkBuffer.nextScaleZ[eid] - NetworkBuffer.prevScaleZ[eid]) * t;
    }
  },
};
