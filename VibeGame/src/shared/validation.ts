/**
 * Reusable Zod validation schemas for shared game types.
 *
 * Canonical definitions live in `core/validation/schemas.ts`; this module is a
 * re-export barrel for the `vibegame/shared` subpath. Do not fork the schemas
 * here — edit the core module.
 */
export {
  booleanSchema,
  bodyTypeSchema,
  colorSchema,
  numberSchema,
  numberStringSchema,
  parseColor,
  parseNumber,
  shapeSchema,
  transformComponentSchema,
  vector2Schema,
  vector3Schema,
} from '../core/validation/schemas';
export type {
  BodyType,
  ColorInput,
  Shape,
  Vector2Input,
  Vector3Input,
} from '../core/validation/schemas';
