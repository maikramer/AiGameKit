declare module 'troika-three-text' {
  import { Material, Mesh } from 'three';

  /** Minimal surface of troika-three-text used by the floating-text plugin. */
  export class Text extends Mesh {
    text: string;
    font: string | null;
    fontSize: number;
    color: number | string;
    anchorX: number | string;
    anchorY: number | string;
    textAlign: string;
    outlineWidth: number | string;
    outlineColor: number | string;
    fillOpacity: number;
    outlineOpacity: number;
    depthOffset: number;
    material: Material;
    sync(callback?: () => void): void;
    dispose(): void;
  }
}
