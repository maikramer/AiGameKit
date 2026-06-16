import type { State } from '../../../core';

export interface TabContent {
  root: HTMLElement;
  refresh(state: State): void;
}

export interface TabDescriptor {
  id: string;
  labelKey: string;
  build: (state: State) => TabContent;
}
