import { defineSystem, defineQuery, type State, type System } from '../../core';
import { PlayerVehicle, Vehicle } from './components';
import { getRaceState } from './race-state';
import { getSoundDef, playSound } from '../audio';

const playerQuery = defineQuery([Vehicle, PlayerVehicle]);

interface EngineVoice {
  ctx: AudioContext;
  master: GainNode;
  /** Saw + square pair an octave apart: the "engine" body. */
  osc: OscillatorNode[];
  oscGain: GainNode[];
  /** Filtered noise: tyre scrub / wind. */
  noise: AudioBufferSourceNode | null;
  noiseFilter: BiquadFilterNode;
  noiseGain: GainNode;
}

let voice: EngineVoice | null = null;
let failed = false;
let prevBoosting = false;
let prevImpactTimer = 10;
let skidReadyAt = 0;

function playBanked(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

/** Edge detector for banked vehicle SFX (crash / nitro / skid). Pure — tests. */
export function vehicleSfxEdges(input: {
  boosting: boolean;
  prevBoosting: boolean;
  impactTimer: number;
  prevImpactTimer: number;
  slip: number;
  speed: number;
  now: number;
  skidReadyAt: number;
}): { crash: boolean; nitro: boolean; skid: boolean; nextSkidReadyAt: number } {
  const crash = input.impactTimer < 0.08 && input.prevImpactTimer > 0.22;
  const nitro = input.boosting && !input.prevBoosting;
  const sliding = input.slip > 0.55 && input.speed > 8;
  const skid = sliding && input.now >= input.skidReadyAt;
  return {
    crash,
    nitro,
    skid,
    nextSkidReadyAt: skid ? input.now + 0.7 : input.skidReadyAt,
  };
}

/** Base frequency of the engine note at idle (Hz). */
const IDLE_HZ = 46;
/** Frequency at the rev limiter (Hz). */
const REDLINE_HZ = 190;

function createNoiseBuffer(ctx: AudioContext): AudioBuffer {
  const length = Math.floor(ctx.sampleRate * 2);
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
  return buffer;
}

function createVoice(): EngineVoice | null {
  if (failed) return null;
  const Ctor =
    typeof window !== 'undefined'
      ? (window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext)
      : undefined;
  if (!Ctor) {
    failed = true;
    return null;
  }
  try {
    const ctx = new Ctor();
    const master = ctx.createGain();
    master.gain.value = 0;
    master.connect(ctx.destination);

    const osc: OscillatorNode[] = [];
    const oscGain: GainNode[] = [];
    // Fundamental (sawtooth) plus a quieter octave (square) for bite.
    for (const [type, detune, level] of [
      ['sawtooth', 0, 0.6],
      ['square', 1200, 0.18],
      ['triangle', -1200, 0.3],
    ] as [OscillatorType, number, number][]) {
      const o = ctx.createOscillator();
      o.type = type;
      o.detune.value = detune;
      const g = ctx.createGain();
      g.gain.value = level;
      o.connect(g);
      g.connect(master);
      o.start();
      osc.push(o);
      oscGain.push(g);
    }

    const noiseGain = ctx.createGain();
    noiseGain.gain.value = 0;
    const noiseFilter = ctx.createBiquadFilter();
    noiseFilter.type = 'bandpass';
    noiseFilter.frequency.value = 2400;
    noiseFilter.Q.value = 1.1;
    noiseFilter.connect(noiseGain);
    noiseGain.connect(ctx.destination);
    const noise = ctx.createBufferSource();
    noise.buffer = createNoiseBuffer(ctx);
    noise.loop = true;
    noise.connect(noiseFilter);
    noise.start();

    return { ctx, master, osc, oscGain, noise, noiseFilter, noiseGain };
  } catch {
    failed = true;
    return null;
  }
}

/**
 * Synthesised engine + tyre audio for the player's car.
 *
 * Only the player gets a voice: six synthesised engines is mud, and a rival two
 * corners away contributes nothing but CPU. Pitch follows the controller's
 * gear-aware `rpm`, so the note climbs and drops on every shift; a band-passed
 * noise layer rises with tyre slip for the scrub, and with speed for wind.
 *
 * The context starts suspended until a user gesture (browser autoplay policy) —
 * this system resumes it once and stays silent until then rather than spamming
 * warnings.
 */
export const EngineAudioSystem: System = defineSystem({
  name: 'EngineAudioSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const player = playerQuery(state.world)[0];
    if (player === undefined) return;

    if (!voice) voice = createVoice();
    const v = voice;
    if (!v) return;

    if (v.ctx.state === 'suspended') {
      void v.ctx.resume().catch(() => undefined);
      return;
    }
    if (v.ctx.state !== 'running') return;

    const phase = getRaceState().phase;
    const audible =
      phase === 'countdown' || phase === 'racing' || phase === 'finished';
    const rpm = Math.min(1.2, Vehicle.rpm[player] || 0);
    const throttle = Vehicle.throttle[player];
    const speed = Math.abs(Vehicle.speed[player]);
    const maxSpeed = Vehicle.maxSpeed[player] || 40;

    const now = v.ctx.currentTime;
    const freq = IDLE_HZ + (REDLINE_HZ - IDLE_HZ) * rpm;
    for (const o of v.osc) {
      o.frequency.setTargetAtTime(freq, now, 0.04);
    }

    // Volume: idle hum, more on throttle, a touch more while boosting.
    const target = audible
      ? 0.035 +
        throttle * 0.05 +
        rpm * 0.03 +
        (Vehicle.boosting[player] ? 0.03 : 0)
      : 0;
    v.master.gain.setTargetAtTime(target, now, 0.08);

    // Noise: tyre scrub when sliding, wind rush with speed.
    const slip = Vehicle.slip[player];
    const scrub = slip > 0.2 && speed > 4 ? (slip - 0.2) * 0.14 : 0;
    const wind = (speed / maxSpeed) * 0.02;
    v.noiseFilter.frequency.setTargetAtTime(1400 + slip * 2600, now, 0.05);
    v.noiseGain.gain.setTargetAtTime(audible ? scrub + wind : 0, now, 0.06);

    if (audible) {
      const edges = vehicleSfxEdges({
        boosting: Vehicle.boosting[player] === 1,
        prevBoosting,
        impactTimer: Vehicle.impactTimer[player],
        prevImpactTimer,
        slip,
        speed,
        now: state.time.elapsed,
        skidReadyAt,
      });
      if (edges.crash) playBanked('race-crash');
      if (edges.nitro) playBanked('race-nitro');
      if (edges.skid) playBanked('race-skid');
      prevBoosting = Vehicle.boosting[player] === 1;
      prevImpactTimer = Vehicle.impactTimer[player];
      skidReadyAt = edges.nextSkidReadyAt;
    }
  },

  dispose() {
    prevBoosting = false;
    prevImpactTimer = 10;
    skidReadyAt = 0;
    if (!voice) return;
    try {
      for (const o of voice.osc) o.stop();
      voice.noise?.stop();
      void voice.ctx.close();
    } catch {
      // Closing an already-closed context is not an error worth surfacing.
    }
    voice = null;
    failed = false;
  },
});
