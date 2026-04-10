import type { EntityScriptContext } from 'vibegame';

/** Rotação lenta do modelo do cristal (entity script de exemplo). */
export function update({ object3d, deltaTime }: EntityScriptContext): void {
  if (!object3d) return;
  object3d.rotation.y += deltaTime * 0.45;
}
