import type { Recipe } from '../../core';

export const paragraphRecipe: Recipe = {
  name: 'Paragraph',
  components: ['transform', 'paragraph'],
  merge: true,
};

export const wordRecipe: Recipe = {
  name: 'Word',
  components: ['transform', 'word'],
  merge: true,
};
