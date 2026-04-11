import type { System, State } from 'vibegame';
import {
  AudioSource,
  PlayerController,
  configure,
  getBuilder,
  playAudioEmitter,
  registerEntityScripts,
  resetBuilder,
  resumeAudioContextIfSuspended,
  withPlugin,
  withSystem,
  SaveLoadPlugin,
  I18nPlugin,
  saveToLocalStorage,
  loadFromLocalStorage,
  loadDictionary,
  setLocale,
  getLocale,
  t,
  isKeyDown,
} from 'vibegame';
import { CombatPlugin } from '../../../src/plugins/combat/index.ts';
import { Health, isDead } from '../../../src/plugins/combat/components.ts';
import { getWaveNumber, getEnemiesAlive } from './scripts/wave-manager';

const SAVE_KEY = 'simple-rpg-save';

const dictEN: Record<string, string> = {
  'hud.title': 'Crystal Vale',
  'hud.mission': 'Survive the enemy waves!',
  'hud.hp': 'HP: {hp} / {max}',
  'hud.wave': 'Wave {wave} — Enemies: {enemies}',
  'hud.time': 'Time: {time}',
  'hud.locale': 'Language: {lang}  [L] switch',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
  'hud.no-save': 'No save found.',
  'hud.healed': 'Health restored!',
  'hud.waveComplete': 'Wave {wave} Complete! — Next wave incoming...',
  'hud.gameOver': 'GAME OVER',
  'hud.waveReached': 'Wave {wave} reached',
  'hud.restart': 'Restart',
  'hud.controls':
    '[WASD] move  [Space] jump  [Mouse] look  [Q] save  [E] load  [L] EN/PT',
};

const dictPT: Record<string, string> = {
  'hud.title': 'Vale do Cristal',
  'hud.mission': 'Sobrevive às ondas de inimigos!',
  'hud.hp': 'HP: {hp} / {max}',
  'hud.wave': 'Onda {wave} — Inimigos: {enemies}',
  'hud.time': 'Tempo: {time}',
  'hud.locale': 'Idioma: {lang}  [L] trocar',
  'hud.saved': 'Jogo gravado!',
  'hud.loaded': 'Progresso restaurado.',
  'hud.no-save': 'Nenhuma gravação encontrada.',
  'hud.healed': 'Saúde restaurada!',
  'hud.waveComplete': 'Onda {wave} Completa! — Próxima onda a chegar...',
  'hud.gameOver': 'FIM DE JOGO',
  'hud.waveReached': 'Onda {wave} alcançada',
  'hud.restart': 'Recomeçar',
  'hud.controls':
    '[WASD] mover  [Espaço] saltar  [Rato] câmara  [Q] gravar  [E] carregar  [L] EN/PT',
};

let overlayMissionEl: HTMLDivElement | null = null;
let overlayStatsEl: HTMLDivElement | null = null;
let overlayControlsEl: HTMLDivElement | null = null;
let healthBarFill: HTMLDivElement | null = null;
let healthBarText: HTMLSpanElement | null = null;
let damageFlashEl: HTMLDivElement | null = null;
let waveCompleteEl: HTMLDivElement | null = null;
let gameOverEl: HTMLDivElement | null = null;
let waveTopEl: HTMLDivElement | null = null;

let eidSfxJump = -1;
let eidSfxSave = -1;
let eidSfxLoad = -1;
let eidSfxHeal = -1;

let saveDebounce = false;
let loadDebounce = false;
let localeDebounce = false;
let prevHeroIsJumping = 0;

let playTimeSec = 0;
let statusFlashUntil = 0;
let statusFlashKey = '';

let prevPlayerHp = 100;
let healFlashUntil = 0;
let prevWaveNumber = 1;
let waveCompleteFlashUntil = 0;
let gameOverShown = false;

const DAMAGE_NUMBER_POOL_SIZE = 10;
const damageNumberPool: HTMLDivElement[] = [];
let damageNumberIndex = 0;

function formatTime(sec: number): string {
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60) % 60;
  const h = Math.floor(sec / 3600);
  if (h > 0)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function pushFlash(state: State, key: string, seconds = 2.2): void {
  statusFlashKey = key;
  statusFlashUntil = state.time.elapsed + seconds;
}

function refreshHud(state: State): void {
  if (overlayStatsEl) {
    const wave = getWaveNumber();
    const enemies = getEnemiesAlive();
    overlayStatsEl.innerHTML =
      `${t(state, 'hud.wave', { wave: String(wave), enemies: String(enemies) })}<br/>` +
      `${t(state, 'hud.time', { time: formatTime(playTimeSec) })}<br/>` +
      `${t(state, 'hud.locale', { lang: getLocale(state) === 'pt' ? 'PT' : 'EN' })}`;
  }
}

function updateHealthBar(heroEid: number, state: State): void {
  if (!healthBarFill || !healthBarText) return;
  const maxHp = Health.max[heroEid] || 100;
  const hp = Math.max(0, Health.current[heroEid]);
  const pct = Math.max(0, Math.min(100, (hp / maxHp) * 100));
  healthBarFill.style.width = `${pct}%`;
  healthBarText.textContent = t(state, 'hud.hp', {
    hp: String(Math.round(hp)),
    max: String(Math.round(maxHp)),
  });
}

function showDamageNumber(amount: number, screenX: number, screenY: number): void {
  if (damageNumberPool.length === 0) return;
  const el = damageNumberPool[damageNumberIndex % DAMAGE_NUMBER_POOL_SIZE];
  damageNumberIndex++;
  el.textContent = `-${amount}`;
  el.style.left = `${screenX}px`;
  el.style.top = `${screenY}px`;
  el.style.opacity = '1';
  el.style.transform = 'translateY(0)';
  el.style.transition = 'none';
  void el.offsetHeight;
  el.style.transition = 'opacity 1s ease-out, transform 1s ease-out';
  el.style.opacity = '0';
  el.style.transform = 'translateY(-40px)';
}

function project3Dto2D(
  worldX: number,
  worldY: number,
  worldZ: number,
  state: State
): { x: number; y: number } | null {
  const renderer = state.renderer;
  if (!renderer) return null;
  const camera = renderer.getCamera();
  if (!camera) return null;

  const THREE = renderer.getTHREE();
  if (!THREE) return null;

  const vec = new THREE.Vector3(worldX, worldY, worldZ);
  vec.project(camera);

  if (vec.z > 1) return null;

  const canvas = renderer.getCanvas();
  const hw = (canvas?.clientWidth ?? window.innerWidth) / 2;
  const hh = (canvas?.clientHeight ?? window.innerHeight) / 2;

  return {
    x: vec.x * hw + hw,
    y: -vec.y * hh + hh,
  };
}

const GameplayHudSystem: System = {
  group: 'simulation',
  update(state: State) {
    const dt = state.time.deltaTime;
    playTimeSec += dt;

    const heroEid = state.getEntityByName('hero');

    if (heroEid !== null && state.hasComponent(heroEid, Health)) {
      const currentHp = Health.current[heroEid];
      const maxHp = Health.max[heroEid];

      updateHealthBar(heroEid, state);

      if (currentHp < prevPlayerHp && prevPlayerHp > 0) {
        const dmg = Math.round(prevPlayerHp - currentHp);
        if (damageFlashEl) {
          damageFlashEl.style.transition = 'none';
          damageFlashEl.style.opacity = '1';
          void damageFlashEl.offsetHeight;
          damageFlashEl.style.transition = 'opacity 0.2s ease-out';
          damageFlashEl.style.opacity = '0';
        }
        const TransformCmp = state.getComponent('transform');
        if (TransformCmp) {
          const px = TransformCmp.posX[heroEid];
          const py = TransformCmp.posY[heroEid];
          const pz = TransformCmp.posZ[heroEid];
          const projected = project3Dto2D(px, py + 1.5, pz, state);
          if (projected) {
            showDamageNumber(dmg, projected.x, projected.y);
          }
        }
      }

      if (state.time.elapsed < healFlashUntil && healFlashUntil > 0) {
        if (overlayMissionEl) {
          overlayMissionEl.innerHTML =
            `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
            `<span style="opacity:0.95;font-size:13px;color:#88ffaa">${t(state, 'hud.healed')}</span>`;
        }
      }

      prevPlayerHp = currentHp;

      if (isDead(heroEid) && !gameOverShown) {
        gameOverShown = true;
        if (gameOverEl) {
          gameOverEl.style.display = 'flex';
          const waveText = gameOverEl.querySelector('.go-wave');
          if (waveText)
            waveText.textContent = t(state, 'hud.waveReached', {
              wave: String(getWaveNumber()),
            });
          const titleEl = gameOverEl.querySelector('.go-title');
          if (titleEl) titleEl.textContent = t(state, 'hud.gameOver');
          const btnEl = gameOverEl.querySelector('.go-btn');
          if (btnEl) btnEl.textContent = t(state, 'hud.restart');
        }
      }
    }

    const wave = getWaveNumber();
    if (wave > prevWaveNumber && !gameOverShown) {
      prevWaveNumber = wave;
      waveCompleteFlashUntil = state.time.elapsed + 3;
      if (waveCompleteEl) {
        waveCompleteEl.textContent = t(state, 'hud.waveComplete', {
          wave: String(wave - 1),
        });
        waveCompleteEl.style.transition = 'none';
        waveCompleteEl.style.opacity = '1';
        void waveCompleteEl.offsetHeight;
        waveCompleteEl.style.transition = 'opacity 3s ease-out';
        waveCompleteEl.style.opacity = '0';
      }
    }

    if (waveTopEl) {
      const enemies = getEnemiesAlive();
      waveTopEl.textContent = t(state, 'hud.wave', {
        wave: String(wave),
        enemies: String(enemies),
      });
    }

    if (statusFlashKey && state.time.elapsed >= statusFlashUntil) {
      statusFlashKey = '';
    }
    if (
      overlayMissionEl &&
      statusFlashKey &&
      state.time.elapsed < statusFlashUntil
    ) {
      const extra = t(state, statusFlashKey);
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.95;font-size:13px;color:#c8e0ff">${extra}</span>`;
    } else if (overlayMissionEl && state.time.elapsed >= healFlashUntil) {
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.88;font-size:13px">${t(state, 'hud.mission')}</span>`;
    }
    refreshHud(state);

    if (
      heroEid !== null &&
      eidSfxJump >= 0 &&
      state.hasComponent(heroEid, PlayerController)
    ) {
      const jumping = PlayerController.isJumping[heroEid];
      if (jumping === 1 && prevHeroIsJumping === 0) {
        playAudioEmitter(state, eidSfxJump);
      }
      prevHeroIsJumping = jumping;
    }

    if (isKeyDown('KeyL') && !localeDebounce) {
      localeDebounce = true;
      const next = getLocale(state) === 'pt' ? 'en' : 'pt';
      setLocale(state, next);
      if (overlayControlsEl)
        overlayControlsEl.textContent = t(state, 'hud.controls');
      refreshHud(state);
    }
    if (!isKeyDown('KeyL')) localeDebounce = false;

    if (isKeyDown('KeyQ') && !saveDebounce) {
      saveDebounce = true;
      saveToLocalStorage(state, SAVE_KEY);
      if (eidSfxSave >= 0) playAudioEmitter(state, eidSfxSave);
      pushFlash(state, 'hud.saved', 2.5);
    }
    if (!isKeyDown('KeyQ')) saveDebounce = false;

    if (isKeyDown('KeyE') && !loadDebounce) {
      loadDebounce = true;
      const ok = loadFromLocalStorage(state, SAVE_KEY);
      if (ok && eidSfxLoad >= 0) playAudioEmitter(state, eidSfxLoad);
      pushFlash(state, ok ? 'hud.loaded' : 'hud.no-save', 2.5);
    }
    if (!isKeyDown('KeyE')) loadDebounce = false;
  },
};

function createOverlayHud(state: State): void {
  const wrap = document.createElement('div');
  wrap.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:1000;font-family:system-ui,Segoe UI,sans-serif;';

  const topLeft = document.createElement('div');
  topLeft.style.cssText =
    'position:absolute;top:20px;left:20px;max-width:min(420px,92vw);' +
    'display:flex;flex-direction:column;gap:10px;';

  overlayMissionEl = document.createElement('div');
  overlayMissionEl.style.cssText =
    'background:rgba(8,12,28,0.72);color:#e8eef8;padding:12px 18px;' +
    'border-radius:10px;font-size:13px;line-height:1.45;' +
    'border:1px solid rgba(90,120,200,0.22);backdrop-filter:blur(10px);' +
    'box-shadow:0 8px 32px rgba(0,0,0,0.25);';

  const healthBarContainer = document.createElement('div');
  healthBarContainer.style.cssText =
    'background:rgba(8,12,28,0.55);border-radius:10px;padding:10px 14px;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);';

  const healthBarOuter = document.createElement('div');
  healthBarOuter.style.cssText =
    'width:200px;height:22px;background:rgba(60,20,20,0.6);border-radius:4px;' +
    'position:relative;overflow:hidden;';

  healthBarFill = document.createElement('div');
  healthBarFill.style.cssText =
    'width:100%;height:100%;background:linear-gradient(90deg,#2ecc40,#5ee870);' +
    'border-radius:4px;transition:width 0.15s ease-out;';

  healthBarText = document.createElement('span');
  healthBarText.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'color:#fff;font-size:11px;font-weight:600;text-shadow:0 1px 3px rgba(0,0,0,0.6);';
  healthBarText.textContent = t(state, 'hud.hp', { hp: '100', max: '100' });

  healthBarOuter.appendChild(healthBarFill);
  healthBarOuter.appendChild(healthBarText);
  healthBarContainer.appendChild(healthBarOuter);

  overlayStatsEl = document.createElement('div');
  overlayStatsEl.style.cssText =
    'background:rgba(8,12,28,0.55);color:#b8c8e8;padding:10px 16px;' +
    'border-radius:8px;font-size:12px;line-height:1.5;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);';

  topLeft.appendChild(overlayMissionEl);
  topLeft.appendChild(healthBarContainer);
  topLeft.appendChild(overlayStatsEl);

  damageFlashEl = document.createElement('div');
  damageFlashEl.style.cssText =
    'position:fixed;inset:0;pointer-events:none;background:rgba(255,0,0,0.35);opacity:0;z-index:1001;';

  for (let i = 0; i < DAMAGE_NUMBER_POOL_SIZE; i++) {
    const numEl = document.createElement('div');
    numEl.style.cssText =
      'position:absolute;pointer-events:none;color:#ff4444;font-size:18px;' +
      'font-weight:700;text-shadow:0 1px 4px rgba(0,0,0,0.7);opacity:0;z-index:1002;';
    wrap.appendChild(numEl);
    damageNumberPool.push(numEl);
  }

  waveCompleteEl = document.createElement('div');
  waveCompleteEl.style.cssText =
    'position:fixed;top:30%;left:50%;transform:translateX(-50%);' +
    'color:#fff;font-size:28px;font-weight:700;letter-spacing:1px;' +
    'text-shadow:0 2px 12px rgba(0,0,0,0.6);opacity:0;z-index:1003;' +
    'pointer-events:none;white-space:nowrap;';

  gameOverEl = document.createElement('div');
  gameOverEl.style.cssText =
    'position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,0.82);' +
    'display:none;flex-direction:column;align-items:center;justify-content:center;' +
    'pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;';

  const goTitle = document.createElement('div');
  goTitle.className = 'go-title';
  goTitle.style.cssText =
    'color:#fff;font-size:48px;font-weight:800;letter-spacing:2px;margin-bottom:16px;';
  goTitle.textContent = t(state, 'hud.gameOver');

  const goWave = document.createElement('div');
  goWave.className = 'go-wave';
  goWave.style.cssText =
    'color:#b8c8e8;font-size:20px;margin-bottom:32px;';
  goWave.textContent = t(state, 'hud.waveReached', { wave: '1' });

  const goBtn = document.createElement('button');
  goBtn.className = 'go-btn';
  goBtn.style.cssText =
    'background:rgba(90,120,200,0.3);color:#e8eef8;border:1px solid rgba(90,120,200,0.4);' +
    'padding:12px 36px;border-radius:8px;font-size:16px;cursor:pointer;pointer-events:auto;' +
    'font-family:system-ui,Segoe UI,sans-serif;';
  goBtn.textContent = t(state, 'hud.restart');
  goBtn.addEventListener('click', () => location.reload());

  gameOverEl.appendChild(goTitle);
  gameOverEl.appendChild(goWave);
  gameOverEl.appendChild(goBtn);

  waveTopEl = document.createElement('div');
  waveTopEl.style.cssText =
    'position:fixed;top:20px;left:50%;transform:translateX(-50%);' +
    'background:rgba(8,12,28,0.6);color:#dde4f0;padding:8px 20px;' +
    'border-radius:8px;font-size:14px;font-weight:600;letter-spacing:0.3px;' +
    'border:1px solid rgba(90,120,200,0.18);backdrop-filter:blur(8px);' +
    'pointer-events:none;z-index:1000;white-space:nowrap;';

  const bottom = document.createElement('div');
  bottom.style.cssText =
    'position:absolute;bottom:22px;left:50%;transform:translateX(-50%);';

  overlayControlsEl = document.createElement('div');
  overlayControlsEl.style.cssText =
    'background:rgba(8,12,28,0.5);color:#8a9ab8;padding:8px 16px;' +
    'border-radius:8px;font-size:11px;letter-spacing:0.2px;max-width:92vw;text-align:center;' +
    'border:1px solid rgba(90,120,200,0.12);';
  overlayControlsEl.textContent = t(state, 'hud.controls');

  bottom.appendChild(overlayControlsEl);
  wrap.appendChild(topLeft);
  wrap.appendChild(damageFlashEl);
  wrap.appendChild(waveCompleteEl);
  wrap.appendChild(waveTopEl);
  wrap.appendChild(bottom);
  document.body.appendChild(wrap);
  document.body.appendChild(gameOverEl);

  refreshHud(state);
}

function resolveAudioEids(state: State): void {
  eidSfxJump = state.getEntityByName('sfx-jump') ?? -1;
  eidSfxSave = state.getEntityByName('sfx-save') ?? -1;
  eidSfxLoad = state.getEntityByName('sfx-load') ?? -1;
  eidSfxHeal = state.getEntityByName('sfx-heal') ?? -1;
}

async function bootstrap(): Promise<void> {
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withPlugin(CombatPlugin);
  withSystem(GameplayHudSystem);

  configure({ canvas: '#game-canvas' });

  const builder = getBuilder();
  resetBuilder();
  const runtime = await builder.build();
  const state = runtime.getState();

  registerEntityScripts(state, import.meta.glob('./scripts/*.ts'));

  const heroEid = state.getEntityByName('hero');
  if (heroEid !== null) {
    state.addComponent(heroEid, Health);
    Health.current[heroEid] = 100;
    Health.max[heroEid] = 100;
  }

  resolveAudioEids(state);

  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);

  const userLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  setLocale(state, userLang);

  createOverlayHud(state);

  const bgmEid = state.getEntityByName('bgm');
  if (bgmEid !== null && typeof document !== 'undefined') {
    const startBgm = () => {
      resumeAudioContextIfSuspended();
      if (state.exists(bgmEid) && state.hasComponent(bgmEid, AudioSource)) {
        AudioSource.playing[bgmEid] = 1;
      }
      document.removeEventListener('pointerdown', startBgm);
    };
    document.addEventListener('pointerdown', startBgm, { once: true });
  }

  await runtime.start();
}

void bootstrap();
