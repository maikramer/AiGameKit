import { Client } from 'colyseus.js';
import { defineQuery, type System } from '../../core';
import { Transform } from '../transforms';
import { NetworkBuffer, Networked } from './components';
import { getNetworkContext } from './context';

const netQuery = defineQuery([Networked, Transform]);

export const NetworkConnectSystem: System = {
  group: 'setup',
  update: (state) => {
    if (state.headless) return;
    const ctx = getNetworkContext(state);
    if (!ctx.url || ctx.room) return;

    const client = new Client(ctx.url);
    client
      .joinOrCreate(ctx.roomName)
      .then((room) => {
        ctx.room = room;
        room.onMessage(
          'transform',
          (msg: { eid: number; x: number; y: number; z: number }) => {
            for (const eid of netQuery(state.world)) {
              if (msg.eid !== eid || Networked.isOwner[eid]) continue;
              NetworkBuffer.prevX[eid] = Transform.posX[eid];
              NetworkBuffer.prevY[eid] = Transform.posY[eid];
              NetworkBuffer.prevZ[eid] = Transform.posZ[eid];
              NetworkBuffer.nextX[eid] = msg.x;
              NetworkBuffer.nextY[eid] = msg.y;
              NetworkBuffer.nextZ[eid] = msg.z;
            }
          }
        );
      })
      .catch(() => {
        /* servidor opcional */
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
      });
    }
  },
};

export const NetworkInterpolationSystem: System = {
  group: 'draw',
  update: (state) => {
    const t = state.time.deltaTime;
    for (const eid of netQuery(state.world)) {
      if (Networked.isOwner[eid]) continue;
      if (!Networked.interpolate[eid]) continue;
      const ax = NetworkBuffer.prevX[eid];
      const ay = NetworkBuffer.prevY[eid];
      const az = NetworkBuffer.prevZ[eid];
      const bx = NetworkBuffer.nextX[eid];
      const by = NetworkBuffer.nextY[eid];
      const bz = NetworkBuffer.nextZ[eid];
      const k = Math.min(1, t * 10);
      Transform.posX[eid] = ax + (bx - ax) * k;
      Transform.posY[eid] = ay + (by - ay) * k;
      Transform.posZ[eid] = az + (bz - az) * k;
    }
  },
};
