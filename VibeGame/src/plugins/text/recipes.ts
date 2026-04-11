import type { Recipe } from '../../core';

export const paragraphRecipe: Recipe = {
  name: 'Paragraph',
  components: ['transform', 'paragraph'],
};

export const wordRecipe: Recipe = {
  name: 'Word',
  components: ['transform', 'word'],
};
