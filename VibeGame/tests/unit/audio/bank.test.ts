import { beforeEach, describe, expect, it, mock } from 'bun:test';

let nextId = 1;
const howlInstances: any[] = [];

class MockHowl {
  _opts: any;
  _ends = new Map<number, () => void>();
  play = mock((_id?: number) => {
    const id = nextId++;
    return id;
  });
  stop = mock((_id?: number) => {});
  unload = mock(() => {});
  volume = mock((_v?: number, _id?: number) => {});
  rate = mock((_r?: number, _id?: number) => {});
  loop = mock(() => {});
  pos = mock((_x?: number, _y?: number, _z?: number, _id?: number) => {});
  pannerAttr = mock(() => {});
  fade = mock(() => {});
  once = mock((event: string, cb: () => void, id?: number) => {
    if (event === 'end' && id != null) this._ends.set(id, cb);
  });
  constructor(opts: any) {
    this._opts = opts;
    howlInstances.push(this);
  }
}

mock.module('howler', () => ({ Howl: MockHowl, Howler: { pos: () => {} } }));

const bank = await import('../../../src/plugins/audio/bank');

describe('Sound bank', () => {
  beforeEach(() => {
    bank._resetSoundBank();
    bank.setAudioEnabled(true);
    howlInstances.length = 0;
    nextId = 1;
  });

  it('plays a defined sound through the sfx bus by default', () => {
    bank.defineSoundBank({ coin: { url: '/coin.ogg', volume: 0.5 } });
    const h = bank.playSound('coin');

    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0].play).toHaveBeenCalledTimes(1);
    expect(h.key).toBe('coin');
    // master(1) * sfx(1) * clip(0.5)
    expect(howlInstances[0].volume).toHaveBeenLastCalledWith(0.5, h.id);
  });

  it('routes gain through master × bus × clip', () => {
    bank.defineSoundBank({ bgm: { url: '/bgm.wav', volume: 0.8, bus: 'music', loop: true } });
    const h = bank.playSound('bgm');
    expect(howlInstances[0].volume).toHaveBeenLastCalledWith(0.8, h.id);

    bank.setBusVolume('music', 0.5);
    expect(howlInstances[0].volume).toHaveBeenLastCalledWith(0.4, h.id); // 1*0.5*0.8

    bank.setMasterVolume(0.5);
    expect(howlInstances[0].volume).toHaveBeenLastCalledWith(0.2, h.id); // 0.5*0.5*0.8
  });

  it('mutes a bus to zero gain', () => {
    bank.defineSoundBank({ ping: { url: '/p.ogg', volume: 1, bus: 'ui', loop: true } });
    const h = bank.playSound('ping');
    bank.setBusMuted('ui', true);
    expect(howlInstances[0].volume).toHaveBeenLastCalledWith(0, h.id);
    expect(bank.isBusMuted('ui')).toBe(true);
  });

  it('warns and returns a null handle for an unknown key', () => {
    const h = bank.playSound('nope');
    expect(h.id).toBe(-1);
    expect(howlInstances).toHaveLength(0);
  });

  it('returns a null handle when audio is disabled', () => {
    bank.setAudioEnabled(false);
    bank.defineSoundBank({ coin: { url: '/c.ogg' } });
    const h = bank.playSound('coin');
    expect(h.id).toBe(-1);
    expect(howlInstances).toHaveLength(0);
  });

  it('overlaps one-shots on a single Howl with distinct ids', () => {
    bank.defineSoundBank({ hit: { url: '/h.ogg' } });
    const a = bank.playSound('hit');
    const b = bank.playSound('hit');
    expect(howlInstances).toHaveLength(1);
    expect(a.id).not.toBe(b.id);
    expect(howlInstances[0].play).toHaveBeenCalledTimes(2);
  });

  it('positions a spatial one-shot at a world point', () => {
    bank.defineSoundBank({ boom: { url: '/b.ogg', spatial: true } });
    const h = bank.playSoundAt('boom', 1, 2, 3);
    expect(howlInstances[0].pos).toHaveBeenCalledWith(1, 2, 3, h.id);
  });

  it('stops via handle and drops from active set', () => {
    bank.defineSoundBank({ loop: { url: '/l.ogg', loop: true } });
    const h = bank.playSound('loop');
    h.stop();
    expect(howlInstances[0].stop).toHaveBeenCalledWith(h.id);
    // No longer affected by bus changes once stopped.
    howlInstances[0].volume.mockClear();
    bank.setMasterVolume(0.1);
    expect(howlInstances[0].volume).not.toHaveBeenCalled();
  });

  it('fires animation-pinned sounds when normalized time crosses the marker', () => {
    bank.defineSoundBank({ step: { url: '/s.ogg' } });
    bank.addClipSound('Walk', { at: 0.5, sound: 'step' });

    bank.fireClipMarkers(7, 'Walk', 0.2, 0.4); // not crossed yet
    expect(howlInstances).toHaveLength(0);

    bank.fireClipMarkers(7, 'Walk', 0.4, 0.6); // crosses 0.5
    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0].play).toHaveBeenCalledTimes(1);
  });

  it('fires markers across a loop wrap', () => {
    bank.defineSoundBank({ step: { url: '/s.ogg' } });
    bank.addClipSound('Walk', { at: 0.1, sound: 'step' });
    bank.fireClipMarkers(7, 'Walk', 0.9, 0.15); // wrapped past 0.1
    expect(howlInstances).toHaveLength(1);
  });
});
