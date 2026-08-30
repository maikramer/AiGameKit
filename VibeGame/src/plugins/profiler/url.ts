/**
 * Profiler URL contract (AI-friendly):
 *   ?profiler=1|sample|deep|0|off
 *   ?profiler=audio              → enable sample + open Audio tab
 *   ?profiler=world              → enable sample + open World tab
 *   ?profiler=physics            → enable sample + open Physics tab
 *   ?profilerTab=systems|audio|world|physics|extras
 */

export type ProfilerTabId =
  'systems' | 'audio' | 'world' | 'physics' | 'extras';

export interface ProfilerUrlConfig {
  /** null = do not auto-open / leave disabled */
  mode: 'sample' | 'deep' | null;
  tab: ProfilerTabId;
  /** True when URL asked for audio debug (also arms stack capture). */
  audioDebug: boolean;
}

const TABS: readonly ProfilerTabId[] = [
  'systems',
  'audio',
  'world',
  'physics',
  'extras',
];

export function isProfilerTabId(v: string): v is ProfilerTabId {
  return (TABS as readonly string[]).includes(v);
}

export function parseProfilerUrl(
  search = typeof window !== 'undefined' ? window.location.search : ''
): ProfilerUrlConfig {
  try {
    const params = new URLSearchParams(search);
    const raw = (params.get('profiler') ?? '').trim().toLowerCase();
    const tabRaw = (params.get('profilerTab') ?? '').trim().toLowerCase();

    let mode: 'sample' | 'deep' | null = null;
    let tab: ProfilerTabId = 'systems';
    let audioDebug = false;

    if (raw === 'audio') {
      mode = 'sample';
      tab = 'audio';
      audioDebug = true;
    } else if (raw === 'world') {
      mode = 'sample';
      tab = 'world';
    } else if (raw === 'physics') {
      mode = 'sample';
      tab = 'physics';
    } else if (raw === '' && params.has('profiler')) {
      mode = 'sample';
    } else if (raw === '1' || raw === 'true' || raw === 'sample') {
      mode = 'sample';
    } else if (raw === 'deep') {
      mode = 'deep';
    } else if (raw === '0' || raw === 'false' || raw === 'off') {
      mode = null;
    } else if (raw !== '') {
      mode = 'sample';
    }

    if (isProfilerTabId(tabRaw)) {
      tab = tabRaw;
      if (tab === 'audio') audioDebug = true;
    }

    if (params.get('audioDebug') === '1') {
      audioDebug = true;
      if (mode === null && raw === '') mode = 'sample';
      if (tab === 'systems' && !params.has('profilerTab')) tab = 'audio';
    }

    return { mode, tab, audioDebug };
  } catch {
    return { mode: null, tab: 'systems', audioDebug: false };
  }
}

/** Sync tab (+ shorthand profiler=audio|world) into the address bar without reload. */
export function syncProfilerTabToUrl(tab: ProfilerTabId): void {
  if (typeof window === 'undefined' || typeof history === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('profilerTab', tab);
    const p = url.searchParams.get('profiler');
    const shorthandOk =
      p === null ||
      p === '' ||
      p === '1' ||
      p === 'true' ||
      p === 'sample' ||
      p === 'audio' ||
      p === 'world' ||
      p === 'physics';
    if (tab === 'audio' && shorthandOk) {
      url.searchParams.set('profiler', 'audio');
    } else if (tab === 'world' && shorthandOk) {
      url.searchParams.set('profiler', 'world');
    } else if (tab === 'physics' && shorthandOk) {
      url.searchParams.set('profiler', 'physics');
    } else if (p === 'audio' || p === 'world' || p === 'physics') {
      url.searchParams.set('profiler', '1');
    }
    history.replaceState(null, '', url.toString());
  } catch {
    /* ignore */
  }
}
