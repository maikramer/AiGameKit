import { MAX_ENTITIES } from '../../core/ecs/constants';

export const ProfilerStats = {
  lastFPS: new Float32Array(MAX_ENTITIES),
  frameTimeMs: new Float32Array(MAX_ENTITIES),
  systemCount: new Uint32Array(MAX_ENTITIES),
} as const;
