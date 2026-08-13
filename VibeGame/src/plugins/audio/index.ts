export { AudioSource, AudioListener, MusicLayerComponent } from './components';
export { AudioPlugin } from './plugin';
export { audioClipRecipe } from './recipes';
export {
  AudioSystem,
  SoundBankSystem,
  playAudioEmitter,
  registerAudioClip,
  resumeAudioContextIfSuspended,
  resumeAudioContextOnFirstUserGesture,
} from './systems';
export {
  NamedSfxResolverSystem,
  playNamedSfx,
  registerNamedSfx,
} from './sfx-registry';
export {
  allowSoundPreload,
  defineSoundBank,
  getSoundDef,
  preloadSounds,
  playSound,
  playSoundAt,
  playSoundOn,
  setBusVolume,
  getBusVolume,
  setBusMuted,
  isBusMuted,
  setAudioEnabled,
  addClipSound,
  getClipSounds,
  listActiveBankPlays,
  listBusDebugState,
  stopAllBankPlays,
  setAudioListenerWorldPos,
  getAudioListenerWorldPos,
  _resetSoundBank,
} from './bank';
export type {
  SoundDef,
  PlayOptions,
  SoundHandle,
  ClipSoundMarker,
} from './bank';
export {
  armAudioDebug,
  armAudioDebugFromUrl,
  clearAudioDebugLog,
  formatAudioOrigin,
  getAudioDebugEvents,
  getAudioDebugSnapshot,
  isAudioDebugArmed,
  recordAudioDebugEvent,
  setAudioEntityNameProvider,
  _resetAudioDebugLog,
} from './debug-log';
export type {
  AudioDebugActivePlay,
  AudioDebugEvent,
  AudioDebugEventKind,
  AudioDebugSnapshot,
  AudioDebugSource,
} from './debug-log';
export {
  createAudioHandle,
  ensureAudioBridge,
  installAudioBridge,
  type VibeGameAudioHandle,
} from './bridge';
export {
  MUSIC_ENTER_BATTLE,
  MUSIC_EXIT_BATTLE,
  MUSIC_LAYER_BATTLE,
  MUSIC_LAYER_CUSTOM,
  MUSIC_LAYER_EXPLORE,
  MusicMixerSystem,
  audioMixerParser,
  audioMixerRecipe,
  crossfadeMusicLayers,
  getAudioMix,
  getMasterVolume,
  getMusicVolume,
  getSfxVolume,
  musicLayerRecipe,
  playMusicLayer,
  registerMusicLayerName,
  resolveMusicLayer,
  setMasterVolume,
  setMusicVolume,
  setSfxVolume,
} from './mixer';
export type { AudioMix } from './mixer';
export { createMusicLayerDriver, getActiveMusicLayer } from './music-driver';
export type { MusicLayerDriverOptions } from './music-driver';
