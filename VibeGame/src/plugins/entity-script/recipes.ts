import type { Recipe } from '../../core';

/** Standalone or merged child: `<MonoBehaviour script="file.ts">` under an `<GameObject>`. */
export const entityScriptRecipe: Recipe = {
  name: 'MonoBehaviour',
  merge: true,
  components: ['transform', 'monoBehaviour'],
};
