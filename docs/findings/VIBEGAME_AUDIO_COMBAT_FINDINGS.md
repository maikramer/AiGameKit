# VibeGame — áudio espacial, profiler Audio, melee impact

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md). Engine guide: [`VibeGame/docs/AUDIO.md`](../../VibeGame/docs/AUDIO.md).

---

## Spatial cull + caches

- `playSoundAt` / follow: se distância ao listener > `maxDistance` (default **40**), **skip** — sem Howl play (evita SFX “fantasma” longe).
- Caches Howl **2D ≠ spatial**: preload 2D não serve para spatial depois.
- Listener pose: `AudioSystem` → bank; bridge `__VIBEGAME__.audio.getListenerPos()`.

## Autoplay: não criar Howl no boot

Sintoma: Firefox/Chrome — *“AudioContext was prevented from starting automatically”* (howler) no load, antes de qualquer clique.

Causa: `preloadSounds` / `new Howl` no bootstrap cria `AudioContext` suspenso.

Fix engine:

1. `preloadSounds` **enfileira** no browser até `allowSoundPreload()`.
2. `<Scene resume-audio-on-user-gesture>` → `resumeAudioContextOnFirstUserGesture` no gesto: flush preload (+ WAV silencioso se `Howler.ctx` ainda null) → `resume()`.
3. BGM / gameplay: esperar `Howler.ctx.state === 'running'` (simple-rpg `BgmSystem`).

Docs: [`VibeGame/docs/AUDIO.md`](../../VibeGame/docs/AUDIO.md#autoplay--preload-diferido).

## Profiler Audio tab

| URL | Efeito |
|-----|--------|
| `?profiler=audio` | Tab Audio + stack capture |
| `?profilerTab=audio` | Com `profiler=1` |

- Log: `origin` / `originEid` / `originName`; `topOrigins`
- `kind: preload` = warm silencioso — painel esconde do gameplay count
- Bridge: `__VIBEGAME__.audio.snapshot()` / `clearLog({ keepPreload: true })`
- Playwright: `VibeGame/tests/playwright/audio-spatial.spec.ts`

## SFX longos (~20–30 s)

Sintoma: um `swing` / hurt soa a combate infinito.

Causa: Text2Sound / handoff deixou cauda longa (não loop lógico).

Mitigação:

1. Regenerar com duração curta (~0.5–1.2 s)
2. Hard-trim ffmpeg (simple-rpg: backups `*.ogg.pretrim.bak`; `regen_sounds.py` com guarda pós-generate)
3. Trim silêncio **inicial** — latência perceptível mesmo com timing de anim OK

Ver também [`IMAGE_SKY_SOUND_FINDINGS.md`](IMAGE_SKY_SOUND_FINDINGS.md) (Text2Sound).

## Melee / harvest impact fraction

| Errado | Certo (packs Quaternius ~1.5 s) |
|--------|----------------------------------|
| SFX no key-edge (J) | Agendar no impacto do clip |
| `0.7 × duration` (~1.05 s) | **`0.35 × duration`** (~0.5 s) — pico de motion ~27% |

Medido em `hero_lod2.glb` clips `sword`/`attack`: energia do swing pico ~0.4 s; 0.7 cai no settle.

Constantes alinhadas:

- `ATTACK_IMPACT_FRACTION = 0.35` — `VibeGame/src/plugins/player/gltf-systems.ts`
- `Destructible.impactFraction` default **0.35**
- simple-rpg `SWING_IMPACT_FRACTION = 0.35` — `examples/simple-rpg/src/game/melee.ts`

Usar `getPlayerAttackClip()` para medir a **mesma** duração que `setPlayerAttackClip` (sword/axe/chop/mine), não só o primeiro clip `*attack*`.

## Creature death / hit-flash

- Limpar flash no death; não re-adopt corpse (`xmlWaitFrames` / skip após morte); `destroyEntity` após timer curto.
- SFX death/hurt: one-shot curto + `originEid` — evita “repetir” por ficheiro longo.

## DevTools noise (related)

| Sintoma | Causa | Mitigação |
|---------|-------|-----------|
| `unsupported GPOS/GSUB table LookupType` | Typr (troika) ao parsear fontes do FloatingText world | `silenceTyprOpentypeNoise` em `vibegame()` |
| `URL constructor: is not a valid URL` + `wasm:…@pmndrs_uikit` / `sourceMappingURL: null` | Vite prebundle yoga WASM | `optimizeDeps.exclude`: `@pmndrs/uikit`, `yoga-layout` (plugin + exemplos); limpar `node_modules/.vite` |
| `Framebuffer not complete` / `DEPTH_ATTACHMENT: Attachment has no width or height` | EffectComposer / shadow map a 0×0 durante shader warmup | `syncComposerSize` + warmup size guard; `shadowMapSize` clamp; boot light `castShadow=false` |

Ver [`VibeGame/src/vite/context.md`](../../VibeGame/src/vite/context.md), [`rendering/context.md`](../../VibeGame/src/plugins/rendering/context.md).

## Changelog

| Data | Nota |
|------|------|
| 2026-07-25 | WebGL DEPTH_ATTACHMENT 0×0 (composer/warmup/shadow) |
| 2026-07-24 | Preload diferido + gesto; Typr/uikit DevTools noise |
| 2026-07-24 | Cull spatial; Audio tab; impact 0.35; trim SFX longos; melee delay no pico |
