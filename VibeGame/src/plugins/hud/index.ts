export { HudPanel } from './components';
export { getStringAt, internString } from './context';
export {
  HudPlugin,
  MinimapWidget,
  minimapParser,
  minimapRecipe,
  registerMinimapWidgetFactory,
} from './plugin';
export {
  HudScreenUpdateSystem,
  createHudScreenLayer,
  getHudScreenLayer,
  getHudWidgetFactory,
  hudScreenLayerParser,
  hudWidgetParser,
  registerHudWidget,
  registerHudWidgetFactory,
  unregisterHudWidget,
} from './screen-layer';
export type { HudWidget, HudWidgetFactory, WidgetHandle } from './screen-layer';
export {
  hudPanelRecipe,
  hudScreenLayerRecipe,
  hudWidgetRecipe,
  compassRecipe,
} from './recipes';
export {
  collectMinimapDots,
  drawMinimap,
  parseMinimapOptions,
  resolveMinimapCategory,
} from './widgets/minimap';
export type {
  MinimapAnchor,
  MinimapCategory,
  MinimapCollection,
  MinimapDot,
  MinimapOptions,
  MinimapPlayerMarker,
} from './widgets/minimap';
export {
  DEFAULT_MINIMAP_COLORS,
  DEFAULT_MINIMAP_RADII,
  DEFAULT_MINIMAP_RANGE,
  DEFAULT_MINIMAP_SIZE,
  MINIMAP_CATEGORY_VALUES,
  MINIMAP_WIDGET_TYPE,
} from './widgets/minimap';
export {
  getInteractionTargets,
  interactionPromptParser,
  interactionPromptRecipe,
  interactionPromptWidgetFactory,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from './widgets/interaction-prompt';
export type {
  InteractionTarget,
  PromptPosition,
} from './widgets/interaction-prompt';
export {
  COMPASS_DEFAULT_FOV,
  COMPASS_DEFAULT_NORTH,
  COMPASS_DEFAULT_NORTH_COLOR,
  cameraAzimuth,
  cardinalAzimuths,
  compassParser,
  createCompassWidget,
  markTransform,
  wrapAngle,
} from './widgets/compass';
export type {
  CardinalMark,
  CompassConfig,
  MarkTransform,
} from './widgets/compass';
export {
  bossBarFactory,
  BOSS_BAR_TAG,
  BOSS_BAR_TYPE,
  controlsBarFactory,
  CONTROLS_BAR_TAG,
  CONTROLS_BAR_TYPE,
  createBossBarWidget,
  createControlsBarWidget,
  createHealthBarWidget,
  createMissionWidget,
  createResourceChipWidget,
  createTimerWidget,
  createXpBarWidget,
  healthBarFactory,
  HEALTH_BAR_TAG,
  HEALTH_BAR_TYPE,
  missionFactory,
  MISSION_TAG,
  MISSION_TYPE,
  registerHudWidgetFactories,
  resourceChipFactory,
  RESOURCE_CHIP_TAG,
  RESOURCE_CHIP_TYPE,
  timerFactory,
  TIMER_TAG,
  TIMER_TYPE,
  widgetParsers,
  widgetRecipes,
  xpBarFactory,
  XP_BAR_TAG,
  XP_BAR_TYPE,
} from './widgets';
export {
  applyPosition,
  formatTime,
  injectWidgetCss,
  makeWidgetParser,
  readAttr,
  readPosition,
  resolveTargetEntity,
} from './widgets/shared';
export type { HudPosition } from './widgets/shared';
export {
  buildTabsFromChildren,
  closeModal,
  createTabbedModalWidget,
  isModalOpen,
  MODAL_ACTION,
  openModal,
  registerModalTab,
  TABBED_MODAL_TAG,
  tabbedModalParser,
  tabbedModalRecipe,
  toggleModal,
  WIDGET_TYPE as TABBED_MODAL_TYPE,
} from './widgets/tabbed-modal';
export type { TabbedModalConfig } from './widgets/tabbed-modal';
export {
  createOptionsTab,
  getOptionValue,
  MODAL_OPTION_CHANGED,
  parseOptionDef,
  registerOptionDef,
  setOptionValue,
} from './widgets/options-tab';
export type { OptionDef, OptionRowType } from './widgets/options-tab';
export { createSkillsTab } from './widgets/skills-tab';
export type { SkillsTabConfig } from './widgets/skills-tab';
export { createInventoryTab } from './widgets/inventory-tab';
export type { InventoryTabConfig } from './widgets/inventory-tab';
export type { TabContent, TabDescriptor } from './widgets/tabbed-modal-shared';
