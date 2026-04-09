import type { Room } from 'colyseus.js';
import type { State } from '../../core';

export interface NetworkContext {
  url: string;
  roomName: string;
  room: Room | null;
}

const stateToNet = new WeakMap<State, NetworkContext>();

export function getNetworkContext(state: State): NetworkContext {
  let ctx = stateToNet.get(state);
  if (!ctx) {
    ctx = { url: '', roomName: '', room: null };
    stateToNet.set(state, ctx);
  }
  return ctx;
}

export function setNetworkConfig(
  state: State,
  url: string,
  roomName: string
): void {
  const ctx = getNetworkContext(state);
  ctx.url = url;
  ctx.roomName = roomName;
}
