import { defineComponent, Types } from 'bitecs';

export const Layer = defineComponent({
  value: Types.ui8,
});

const layerById = new Map<number, string>();
const layerByName = new Map<string, number>();

function registerLayer(name: string, id: number): void {
  layerById.set(id, name);
  layerByName.set(name, id);
}

registerLayer('Default', 0);
registerLayer('TransparentFX', 1);
registerLayer('IgnoreRaycast', 2);
registerLayer('Water', 3);
registerLayer('UI', 4);
// 5 is reserved (matching Unity's gap)
registerLayer('Player', 6);
registerLayer('Enemy', 7);
registerLayer('PhysicsBody', 8);
registerLayer('Trigger', 9);

export const LayerMask = {
  NameToLayer(name: string): number {
    return layerByName.get(name) ?? -1;
  },

  LayerToName(layer: number): string {
    return layerById.get(layer) ?? '';
  },

  GetMask(names: string[]): number {
    let mask = 0;
    for (const name of names) {
      const layer = layerByName.get(name);
      if (layer !== undefined) {
        mask |= 1 << layer;
      }
    }
    return mask;
  },
};
