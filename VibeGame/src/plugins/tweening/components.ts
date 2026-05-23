import { MAX_ENTITIES } from '../../core/ecs/constants';

export const enum ShakerMode {
  Additive = 0,
  Multiplicative = 1,
}

export const Shaker = {
  target: new Uint32Array(MAX_ENTITIES),
  value: new Float32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  mode: new Uint8Array(MAX_ENTITIES),
} as const;

export const Tween = {
  duration: new Float32Array(MAX_ENTITIES),
  elapsed: new Float32Array(MAX_ENTITIES),
  easingIndex: new Uint8Array(MAX_ENTITIES),
} as const;

export const TweenValue = {
  source: new Uint32Array(MAX_ENTITIES),
  target: new Uint32Array(MAX_ENTITIES),
  componentId: new Uint32Array(MAX_ENTITIES),
  fieldIndex: new Uint32Array(MAX_ENTITIES),
  from: new Float32Array(MAX_ENTITIES),
  to: new Float32Array(MAX_ENTITIES),
  value: new Float32Array(MAX_ENTITIES),
} as const;

export const KinematicTween = {
  tweenEntity: new Uint32Array(MAX_ENTITIES),
  targetEntity: new Uint32Array(MAX_ENTITIES),
  axis: new Uint8Array(MAX_ENTITIES),
  from: new Float32Array(MAX_ENTITIES),
  to: new Float32Array(MAX_ENTITIES),
  lastPosition: new Float32Array(MAX_ENTITIES),
  targetPosition: new Float32Array(MAX_ENTITIES),
} as const;

export const KinematicRotationTween = {
  tweenEntity: new Uint32Array(MAX_ENTITIES),
  targetEntity: new Uint32Array(MAX_ENTITIES),
  axis: new Uint8Array(MAX_ENTITIES),
  from: new Float32Array(MAX_ENTITIES),
  to: new Float32Array(MAX_ENTITIES),
  lastRotation: new Float32Array(MAX_ENTITIES),
  targetRotation: new Float32Array(MAX_ENTITIES),
} as const;

export const enum SequenceState {
  Idle = 0,
  Playing = 1,
}

export const Sequence = {
  state: new Uint8Array(MAX_ENTITIES),
  currentIndex: new Uint32Array(MAX_ENTITIES),
  itemCount: new Uint32Array(MAX_ENTITIES),
  pauseRemaining: new Float32Array(MAX_ENTITIES),
} as const;

export const enum TransformShakerType {
  Position = 0,
  Scale = 1,
  Rotation = 2,
}

export const enum TransformShakerAxes {
  X = 1,
  Y = 2,
  Z = 4,
  XY = 3,
  XZ = 5,
  YZ = 6,
  XYZ = 7,
}

export const TransformShaker = {
  target: new Uint32Array(MAX_ENTITIES),
  type: new Uint8Array(MAX_ENTITIES),
  axes: new Uint8Array(MAX_ENTITIES),
  value: new Float32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  mode: new Uint8Array(MAX_ENTITIES),
} as const;
