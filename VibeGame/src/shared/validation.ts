/**
 * Reusable Zod validation schemas for shared game types.
 */

import { z } from 'zod';

const numberSchema = z.number();

const numberStringSchema = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/)
  .transform((val) => parseFloat(val));

const booleanSchema = z.union([
  z.boolean(),
  z.literal('true').transform(() => true),
  z.literal('false').transform(() => false),
  z.literal(1).transform(() => true),
  z.literal(0).transform(() => false),
]);

export { numberSchema, numberStringSchema, booleanSchema };

export const vector3Schema = z.union([
  z.object({ x: z.number(), y: z.number(), z: z.number() }),
  numberSchema.transform((val) => ({ x: val, y: val, z: val })),
  numberStringSchema.transform((val) => ({ x: val, y: val, z: val })),
  z
    .string()
    .regex(/^-?\d+(\.\d+)?\s+-?\d+(\.\d+)?\s+-?\d+(\.\d+)?$/)
    .transform((val) => {
      const [x, y, z] = val.split(/\s+/).map(Number);
      return { x, y, z };
    }),
]);

export const vector2Schema = z.union([
  z.object({ x: z.number(), y: z.number() }),
  numberSchema.transform((val) => ({ x: val, y: val })),
  numberStringSchema.transform((val) => ({ x: val, y: val })),
  z
    .string()
    .regex(/^-?\d+(\.\d+)?\s+-?\d+(\.\d+)?$/)
    .transform((val) => {
      const [x, y] = val.split(/\s+/).map(Number);
      return { x, y };
    }),
]);

export const colorSchema = z.union([
  z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .transform((val) => parseInt(val.slice(1), 16)),
  z
    .string()
    .regex(/^0x[0-9a-fA-F]{6}$/)
    .transform((val) => parseInt(val.slice(2), 16)),
  numberSchema,
  numberStringSchema,
]);

export const shapeSchema = z.enum(['box', 'sphere']);
export const bodyTypeSchema = z.enum(['static', 'dynamic', 'kinematic']);

export type Vector3Input = z.infer<typeof vector3Schema>;
export type Vector2Input = z.infer<typeof vector2Schema>;
export type ColorInput = z.infer<typeof colorSchema>;
export type Shape = z.infer<typeof shapeSchema>;
export type BodyType = z.infer<typeof bodyTypeSchema>;
