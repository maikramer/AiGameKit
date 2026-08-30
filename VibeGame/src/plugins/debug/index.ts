export { DebugPlugin, DebugOverlaySystem } from './plugin';
export type { VibeGameDebugBridge } from './plugin';
export {
  installDiagnostics,
  isDiagnosticsInstalled,
  getDiagnostics,
  getResourceTimings,
  recordDiagnostic,
  aggregateDiagnostics,
  clearDiagnostics,
  diagnosticsCursor,
  serializeLogArgs,
} from './diagnostics';
export type {
  DiagnosticEntry,
  DiagnosticKind,
  DiagnosticSummary,
  DiagnosticsFilter,
  ResourceTimingEntry,
} from './diagnostics';
export {
  buildAgentReport,
  buildAssetsSummary,
  extractAllComponents,
  extractComponentFields,
  formatDebugValue,
} from './report';
export type {
  AgentReport,
  AgentReportOptions,
  AssetsSummary,
  PerfSummary,
} from './report';
export {
  getDebugRegistry,
  getDebugRegistryHandle,
  registerDebugAction,
  registerDebugVar,
} from './registry';
export type {
  DebugActionEntry,
  DebugRegistry,
  DebugRegistryHandle,
  DebugVarEntry,
  RegisterDebugActionOptions,
} from './registry';
export {
  PostFxToggleSystem,
  postFxToggleRecipe,
  parsePostFxBindings,
  DEFAULT_POSTFX_BINDINGS,
  getPostFxToggleState,
  setPostFxBindings,
  applyPostFxToggle,
} from './postfx-toggle';
export type {
  PostFxEffectField,
  PostFxKeyBindings,
  IsKeyDownFn,
  PostFxToggleOptions,
  PostFxToggleResult,
} from './postfx-toggle';
