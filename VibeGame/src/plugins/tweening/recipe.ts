import type { Recipe } from '../../core';

const tweenRecipe: Recipe = {
  name: 'Tween',
  parserAttributes: [
    'target',
    'attr',
    'from',
    'to',
    'duration',
    'delay',
    'loop',
    'easing',
    'ping-pong',
  ],
};

export { tweenRecipe };
