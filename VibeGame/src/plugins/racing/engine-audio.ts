import { defineSystem, defineQuery, type State, type System } from '../../core';
import { Vehicle } from './components';
import { isRacingActive } from './race-state';

const vehicleQuery = defineQuery([Vehicle]);

// Per-vehicle audio state.
const audioState = new Map<number, EngineAudio>();

interface EngineAudio {
  ctx: AudioContext | null;
  gainNode: GainNode | null;
  oscillators: OscillatorNode[];
  lastRpm: number;
  initialized: boolean;
}

/**
 * EngineAudioSystem — synthesizes engine sound using Web Audio API oscillators.
 * Creates a layered engine tone: fundamental RPM + harmonics for a raw
 * mechanical sound. No external audio files needed.
 *
 * Only runs when racing is active; respects user gesture requirement by
 * initializing AudioContext on first race start.
 */
export const EngineAudioSystem: System = defineSystem({
  name: 'EngineAudioSystem',
  group: 'draw', // runs after vehicle control so speed is current

  update(state: State) {
    if (!isRacingActive()) return;

    const vehicles = vehicleQuery(state.world);

    for (const eid of vehicles) {
      let audio = audioState.get(eid);
      if (!audio) {
        audio = { ctx: null, gainNode: null, oscillators: [], lastRpm: 0, initialized: false };
        audioState.set(eid, audio);
      }

      // Lazy-init AudioContext (needs user gesture in most browsers).
      if (!audio.initialized) {
        try {
          const ctx = new AudioContext();
          const masterGain = ctx.createGain();
          masterGain.gain.value = 0.18; // overall volume
          masterGain.connect(ctx.destination);

          // Layered oscillators for engine timbre.
          const oscs: OscillatorNode[] = [];
          const layers = [
            { freqMult: 1.0, type: 'sawtooth' as OscillatorType, gain: 0.5 },
            { freqMult: 2.0, type: 'square' as OscillatorType, gain: 0.2 },
            { freqMult: 3.0, type: 'sawtooth' as OscillatorType, gain: 0.12 },
            { freqMult: 0.5, type: 'sine' as OscillatorType, gain: 0.25 }, // low rumble
          ];

          for (const layer of layers) {
            const osc = ctx.createOscillator();
            osc.type = layer.type;
            const oscGain = ctx.createGain();
            oscGain.gain.value = layer.gain;
            osc.connect(oscGain);
            oscGain.connect(masterGain);
            osc.start();
            oscs.push(osc);
          }

          audio.ctx = ctx;
          audio.gainNode = masterGain;
          audio.oscillators = oscs;
          audio.initialized = true;
        } catch {
          // Audio not available (headless, permissions, etc.)
          return;
        }
      }

      if (!audio.ctx || !audio.gainNode) return;

      // Map vehicle speed to engine RPM frequency.
      const speed = Vehicle.speed[eid] || 0;
      const maxSpeed = Vehicle.maxSpeed[eid] || 40;
      const speedFrac = Math.max(0, Math.min(1, Math.abs(speed) / maxSpeed));

      // Engine idle ~80 Hz, redline ~800 Hz (exaggerated arcade feel).
      const baseFreq = 80 + speedFrac * 720;

      // Smooth RPM changes (engine inertia).
      const rpmSmoothing = 0.08;
      audio.lastRpm += (baseFreq - audio.lastRpm) * rpmSmoothing;

      // Update oscillator frequencies.
      const layers = [1.0, 2.0, 3.0, 0.5];
      for (let i = 0; i < Math.min(audio.oscillators.length, layers.length); i++) {
        audio.oscillators[i]!.frequency.setValueAtTime(
          audio.lastRpm * layers[i]!,
          audio.ctx.currentTime
        );
      }

      // Slight volume increase at high speed (engine strain).
      const targetVol = 0.14 + speedFrac * 0.1;
      audio.gainNode.gain.setValueAtTime(targetVol, audio.ctx.currentTime);

      // Add subtle "load" variation when accelerating hard.
      const throttle = Vehicle.throttle[eid] || 0;
      if (throttle > 0.8 && speedFrac > 0.5) {
        // Tiny pitch wobble under load.
        const wobble = Math.sin(performance.now() * 0.008) * 3;
        for (const osc of audio.oscillators) {
          osc.detune.setValueAtTime(wobble, audio.ctx.currentTime);
        }
      }
    }
  },

  dispose() {
    for (const audio of audioState.values()) {
      try {
        for (const osc of audio.oscillators) {
          try { osc.stop(); } catch { /* already stopped */ }
        }
        if (audio.ctx && audio.ctx.state !== 'closed') {
          audio.ctx.close();
        }
      } catch {
        // cleanup best-effort
      }
    }
    audioState.clear();
  },
});
