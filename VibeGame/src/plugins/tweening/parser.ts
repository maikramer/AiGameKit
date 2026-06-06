import type { Parser } from '../../core';
import { EasingType, TweenAxis, TweenData } from './components';

function toNum(value: unknown, fallback: number): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return parseFloat(value) || fallback;
  return fallback;
}

function parseVector3Component(value: unknown, axis: 0 | 1 | 2): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const parts = value.trim().split(/\s+/).map(Number);
    if (parts.length > axis && !Number.isNaN(parts[axis])) return parts[axis];
    return 0;
  }
  if (
    Array.isArray(value) &&
    value.length > axis &&
    typeof value[axis] === 'number'
  ) {
    return value[axis];
  }
  if (value !== null && typeof value === 'object') {
    const keys = ['x', 'y', 'z'] as const;
    const k = keys[axis];
    const v = (value as Record<string, unknown>)[k];
    if (typeof v === 'number') return v;
  }
  return 0;
}

function toBool(value: unknown): number {
  if (value === 'true' || value === '1' || value === 1) return 1;
  return 0;
}

export const tweenParser: Parser = ({ entity, element, state, context }) => {
  const targetName = element.attributes['target'];
  if (!targetName || typeof targetName !== 'string') return;

  const targetEid = context.getEntityByName(targetName);
  if (targetEid === null) return;

  const attr = element.attributes['attr'];
  const from = element.attributes['from'];
  const to = element.attributes['to'];
  const duration = element.attributes['duration'];
  const delay = element.attributes['delay'];
  const loop = element.attributes['loop'];
  const easing = element.attributes['easing'];
  const pingPong = element.attributes['ping-pong'];

  let axis = TweenAxis.None;
  if (typeof attr === 'string') {
    if (attr === 'rigidbody.pos-x' || attr === 'pos-x') axis = TweenAxis.PosX;
    else if (attr === 'rigidbody.pos-y' || attr === 'pos-y')
      axis = TweenAxis.PosY;
    else if (attr === 'rigidbody.pos-z' || attr === 'pos-z')
      axis = TweenAxis.PosZ;
    else if (attr === 'rotation' || attr === 'rot-y') axis = TweenAxis.RotY;
    else if (attr === 'rot-x') axis = TweenAxis.RotX;
    else if (attr === 'rot-z') axis = TweenAxis.RotZ;
  }

  const isRotationAxis = axis >= TweenAxis.RotX && axis <= TweenAxis.RotZ;
  const vectorAxis = isRotationAxis
    ? ((axis === TweenAxis.RotX ? 0 : axis === TweenAxis.RotY ? 1 : 2) as
        | 0
        | 1
        | 2)
    : 0;

  state.addComponent(entity, TweenData);
  TweenData.targetEntity[entity] = targetEid;
  TweenData.axis[entity] = axis;
  TweenData.from[entity] = isRotationAxis
    ? parseVector3Component(from, vectorAxis)
    : toNum(from, 0);
  TweenData.to[entity] = isRotationAxis
    ? parseVector3Component(to, vectorAxis)
    : toNum(to, 0);
  TweenData.duration[entity] = toNum(duration, 1);
  TweenData.delay[entity] = toNum(delay, 0);
  TweenData.loop[entity] = toBool(loop);
  TweenData.pingPong[entity] = toBool(pingPong);
  TweenData.active[entity] = 1;
  TweenData.elapsed[entity] = 0;

  if (typeof easing === 'string') {
    if (easing === 'ease-in-out')
      TweenData.easing[entity] = EasingType.EaseInOut;
    else if (easing === 'ease-out-quad')
      TweenData.easing[entity] = EasingType.EaseOutQuad;
    else TweenData.easing[entity] = EasingType.Linear;
  } else {
    TweenData.easing[entity] = EasingType.Linear;
  }
};
