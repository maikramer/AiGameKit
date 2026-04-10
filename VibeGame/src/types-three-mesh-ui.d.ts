declare module 'three-mesh-ui/build/three-mesh-ui.module.js' {
  import type { Color, Object3D } from 'three';

  export type BlockOptions = {
    width: number;
    height: number;
    backgroundOpacity?: number;
    backgroundColor?: Color;
    [key: string]: unknown;
  };

  export class Block extends Object3D {
    constructor(options: BlockOptions);
  }

  export class Text extends Object3D {
    constructor(options: Record<string, unknown>);
    set(options: { content?: string; [key: string]: unknown }): void;
  }

  export function update(): void;
}
