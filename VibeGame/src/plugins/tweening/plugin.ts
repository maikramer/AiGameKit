import type { Plugin } from '../../core';
import {
  KinematicRotationTween,
  KinematicTween,
  Sequence,
  Shaker,
  TransformShaker,
  Tween,
  TweenValue,
} from './components';
import { sequenceParser, shakerParser, tweenParser } from './parser';
import {
  KinematicRotationTweenSystem,
  KinematicTweenSystem,
  SequenceSystem,
  ShakerApplySystem,
  ShakerCleanupSystem,
  ShakerRestoreSystem,
  TransformShakerApplySystem,
  TransformShakerCleanupSystem,
  TransformShakerRestoreSystem,
  TweenSystem,
} from './systems';

export const TweenPlugin: Plugin = {
  systems: [
    KinematicTweenSystem,
    KinematicRotationTweenSystem,
    TweenSystem,
    SequenceSystem,
    ShakerApplySystem,
    ShakerRestoreSystem,
    ShakerCleanupSystem,
    TransformShakerApplySystem,
    TransformShakerRestoreSystem,
    TransformShakerCleanupSystem,
  ],
  components: {
    Tween,
    TweenValue,
    KinematicTween,
    KinematicRotationTween,
    Sequence,
    Shaker,
    TransformShaker,
  },
  recipes: [
    { name: 'tween', components: [], parserOnlyAsChild: true },
    {
      name: 'sequence',
      components: [],
      parserOwnsChildren: true,
      parserOnlyAsChild: true,
    },
    { name: 'shaker', components: [], parserOnlyAsChild: true },
  ],
  config: {
    parsers: {
      tween: tweenParser,
      sequence: sequenceParser,
      shaker: shakerParser,
    },
  },
};
