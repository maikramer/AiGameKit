import * as THREE from 'three';
import { defineQueryLive } from '../../core';
import type { State, System } from '../../core';
import { getRenderingContext, MainCamera, threeCameras } from '../rendering';
import { WorldTransform } from '../transforms/components';
import { WeatherComponent } from './components';
import { createCloudField, updateCloudField } from './clouds';
import type { CloudField } from './clouds';
import { createRain, RAIN_COUNT } from './rain';
import { effectiveRainTarget, getWeather, setWeather } from './state';

const weatherQuery = defineQueryLive([WeatherComponent]);
const cameraQuery = defineQueryLive([MainCamera, WorldTransform]);

interface WeatherSideCars {
  clouds: CloudField | null;
  rain: { points: THREE.Points; material: THREE.ShaderMaterial } | null;
  cyclePhase: number;
}

const SIDECARS = new WeakMap<State, WeatherSideCars>();

function sidecars(state: State): WeatherSideCars {
  let s = SIDECARS.get(state);
  if (!s) {
    s = { clouds: null, rain: null, cyclePhase: Math.random() * 1000 };
    SIDECARS.set(state, s);
  }
  return s;
}

function approach(
  current: number,
  target: number,
  dt: number,
  seconds: number
): number {
  if (seconds <= 0) return target;
  const k = 1 - Math.exp(-(3 / seconds) * dt);
  return current + (target - current) * k;
}

/**
 * Global weather driver: seeds runtime targets from the `<Weather>` entity,
 * runs the optional slow weather cycle, smooths current values and animates
 * the cloud field + rain volume around the camera.
 */
export const WeatherSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

    const entities = weatherQuery(state.world);
    if (entities.length === 0) return;
    const eid = entities[0]!;
    const w = getWeather(state);
    const cars = sidecars(state);
    const dt = state.time.deltaTime;

    // Seed / live-sync declarative values (XML attrs are authoritative for
    // wind + cloud height; clouds/rain are targets the cycle can override).
    if (WeatherComponent.seeded[eid] === 0) {
      WeatherComponent.seeded[eid] = 1;
      setWeather(state, {
        windDirX: WeatherComponent.windDirX[eid],
        windDirZ: WeatherComponent.windDirZ[eid],
        windStrength: WeatherComponent.windStrength[eid],
        clouds: WeatherComponent.clouds[eid],
        cloudHeight: WeatherComponent.cloudHeight[eid],
        rain: WeatherComponent.rain[eid],
      });
    }

    // Slow ambient cycle: cloud coverage breathes around the declared value
    // (±0.25) over ~4 min so the sky never looks frozen. Rain stays fully
    // authored (API / biome) unless `storm-cycle` is enabled.
    if (WeatherComponent.cycle[eid] === 1) {
      cars.cyclePhase += dt;
      const base = WeatherComponent.clouds[eid];
      const drift =
        Math.sin(cars.cyclePhase * 0.008) * 0.15 +
        Math.sin(cars.cyclePhase * 0.021 + 1.7) * 0.1;
      w.cloudsTarget = Math.min(1, Math.max(0, base + drift));
    }

    // Smooth toward targets.
    w.clouds = approach(w.clouds, w.cloudsTarget, dt, w.fadeSeconds);
    w.rain = approach(w.rain, effectiveRainTarget(w), dt, w.fadeSeconds);

    // Camera anchor.
    const camIds = cameraQuery(state.world);
    if (camIds.length === 0) return;
    const camEid = camIds[0]!;
    const cam = threeCameras.get(camEid);
    if (!cam) return;
    const cx = WorldTransform.posX[camEid];
    const cz = WorldTransform.posZ[camEid];
    const cy = WorldTransform.posY[camEid];

    const scene = getRenderingContext(state).scene;

    // ── Clouds ──
    if (!cars.clouds) {
      cars.clouds = createCloudField(WeatherComponent.seed[eid]);
      scene.add(cars.clouds.mesh);
    }
    const windX = w.windDirX * w.windStrength;
    const windZ = w.windDirZ * w.windStrength;
    cars.clouds.material.opacity = w.clouds * 0.8;
    cars.clouds.mesh.visible = w.clouds > 0.02;
    if (cars.clouds.mesh.visible) {
      updateCloudField(
        cars.clouds,
        cx,
        cz,
        w.cloudHeight,
        windX * 2.2,
        windZ * 2.2,
        dt,
        cam.quaternion
      );
    }

    // ── Rain ──
    if (!cars.rain && w.rain > 0.01) {
      cars.rain = createRain(WeatherComponent.seed[eid]);
      scene.add(cars.rain.points);
    }
    if (cars.rain) {
      const u = cars.rain.material.uniforms;
      (u.uTime as { value: number }).value = state.time.elapsed;
      (u.uOpacity as { value: number }).value = w.rain;
      (u.uCenter as { value: THREE.Vector3 }).value.set(cx, cy, cz);
      (u.uWind as { value: THREE.Vector2 }).value.set(windX * 0.6, windZ * 0.6);
      cars.rain.points.visible = w.rain > 0.01;
    }
  },
  dispose(state: State) {
    const cars = SIDECARS.get(state);
    if (!cars) return;
    SIDECARS.delete(state);
    if (cars.clouds) {
      cars.clouds.mesh.removeFromParent();
      cars.clouds.mesh.geometry.dispose();
      cars.clouds.material.map?.dispose();
      cars.clouds.material.dispose();
    }
    if (cars.rain) {
      cars.rain.points.removeFromParent();
      cars.rain.points.geometry.dispose();
      cars.rain.material.dispose();
    }
  },
};

export { RAIN_COUNT };
