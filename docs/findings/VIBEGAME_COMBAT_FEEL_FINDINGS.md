# VibeGame Combat Feel Findings

Data: 2026-08-27 · Exemplo: `VibeGame/examples/simple-rpg` · Engine: `VibeGame/src/`

Diagnóstico "combate robótico" e o pacote de técnicas introduzido (hit-stop,
camera shake, knockback, stagger, telegraph, i-frames, slash VFX), com as
armadilhas encontradas durante o QA no browser.

## Ronda 3 — combos procedurais e o mirror que torcia

- Combo pools ganham modos na engine (`setPlayerAttackClip(pool, { mode })`):
  `cycle` (fixo), `random` (sorteio sem repetir o golpe anterior) e
  `alternating` (bases aleatórias + variante `_m` espelhada a cada outro
  golpe). O jogo usa `random`: os clips UAL (sword/sworda/swordb/swordc) já
  cortam de lados naturais misturados, pelo que a sequência lê esquerda↔direita
  sem espelhos. Cada swing ainda re-sorteia o `attackTimeScale` ±8% (aplicado
  pós-impacto para o próximo swing — agendamento engine/jogo fica sincronizado).
- **Bug corrigido**: o `AttackContextSystem` re-registava o pool a cada frame e
  o `setPlayerAttackClip` fazia reset — o combo nunca passava do 1.º clip.
  Agora re-registar a mesma referência é no-op (novo array = nova intenção).
- **Armadilha — anim-mirror torce rigs de retarget**: o `_m`
  (`mirrorAnimationClip`: swap `_l↔_r` + conjugação (w,x,−y,−z)) é
  matematicamente exacto para rigs com rest poses perfeitamente espelhadas
  (Quaternius puro), mas o herói actual é um retarget misto
  (`lowerarm_r` + `RightHandFinger1`) com rests assimétricos — o swing
  espelhado torce o corpo «como pano» (espada atravessa o tronco). Os clips
  `_m` nunca tinham corrido em jogo (o bug do reset escondia-os). Regra:
  **testar o mirror visualmente por rig antes de o pôr num combo**; para rigs
  de retarget, usar variedade de clips reais em vez de espelhos.

## Ronda 2 — skill bar YUIO, grip editor, lança regenerada, vramd PyPI

### Skill bar [Y][U][I][O] (`game/skill-bar.ts`)

- **Y — Machado Brutal**: o clip pesado `axe` deixou de ser swing normal e
  virou skill (cleave frontal 120°, 42+bonus, knockback 1.6 m, hit-stop
  140 ms, clip a 0.85× com impacto a 45%). O swing normal do machado partilha
  o pool da espada (`ATTACK_POOLS.axe = sword…`) — arma diferente, animação
  igual, ritmo do `attackTimeScale`.
- **U — Grito de Guerra**: buff +6 ataque 10 s (`playerStats.buffAttackBonus`,
  foldado no `PlayerStatsSystem`), outline dourado no slot enquanto ativo.
- **I — Redemoinho**: 360°, clip sword a 1.55× (o oposto do heavy read).
- **O — Perfuração**: dash 2.2 m (ray-clamped, padrão do `doDash`) + linha
  estreita (arco 40°, 4 m) com o clip spear.
- Ícones gerados com `text2icon` (`skill_*.png`, prompts de emblema de UI).
- O efeito dispara no frame de impacto do clip (`castSwing` → pending com
  `delay = dur × fraction / timeScale`), e todos os hits passam pelo stack de
  feel (knockback + stagger + hit-stop + shake).

### Grip editor in-game (`game/grip-editor.ts`)

- Debug action **`grip`** (overlay `?` → callAction) ou **Shift+G**. Enquanto
  ativo, o `AttackContextSystem` largar o canal do held item
  (`isGripEditorActive()`).
- Tab pos/rot · setas ajustam · Q/E = Y · 1/2/3 eixo de rotação · Shift fino ·
  N cicla arma · **X exporta** o `held-items.json` completo (console +
  clipboard + botão no painel).
- Propósito: calibrar grips contra o rig do herói actual sem editar o JSON à
  mão — export → colar em `public/data/held-items.json`.

### Lança: voxel merge derrete props finos

- Sintoma: `spear_shape.glb` 743 KB → `spear_clean.glb` 49 KB — o
  morph-close auto (N=0.24) engrossa/colapsa hastes com espessura < voxel.
- Fix: `text3d.morph_close_voxels: 0.08` no manifest (`props-rpg.yaml`) +
  re-clean do shape aprovado (`text3d topology-fix --engine arrays
  --morph-close-voxels 0.08`), re-paint com a imagem do pool, `text3d lod` +
  `finish` + `collision`. Regra prática: **props de haste fina ⇒ morph suave
  (N ≤ 0.1) ou 0**.
- Lição 2: `size_m` demasiado fino também "some" na leitura de jogo — a spear
  v1 pedia L=0.08 m para 1.8 m de comprimento (palito sub-pixel); v2 usa
  [0.14, 1.65, 0.14] + prompt "sturdy shaft, readable proportions" (sem
  "not a thick pole").
- Held-item GLBs vivem no pool `meshes/props/` e o exemplo serve-os via plugin
  `sharedAssets` — sem cópias; editar no pool muda o jogo no reload.

### vramd: PyPI 0.3.1 + backends

- Pin: `Vramd/pyproject.toml` → `vramd>=0.3.1` (PyPI; o clone
  `~/GitClones/vramd` é upstream de dev, nunca dependência). Venv actualizado
  com `pip install -U vramd==0.3.1`.
- Armadilha: `vramd start` manual pela CLI **não** injecta
  `VRAMD_TOOLS_ROOT`/`VRAMD_BACKENDS_FILE` — arranca com o registry de
  exemplos (whisper/diffusion) e os jobs caem em `BACKEND_UNKNOWN`. O
  auto-start das tools (`aigamekit_shared.vramd_client`) é que injecta o
  monorepo. Se precisares de start manual: exporta as duas vars; o caminho
  normal é deixar a primeira tool auto-arrancar o supervisor.

## Diagnóstico — porque o combate parecia robótico

1. Inimigos giravam em **snap** (rotação instantânea; o herói tem `dampQ`, os mobs não).
2. Golpes **sem peso**: não existia hit-stop, screen shake, knockback ou FOV kick na engine.
3. Inimigos **nunca eram interrompidos**: a lunge continuava mesmo apanhando golpes.
4. **Sem telegraph**: o windup antes da lunge era invisível (modo ATTACK + clip em loop).
5. Jogador atingido: só número + som — sem vignette, sem shake, sem i-frames (packs = morte em cascata).
6. **Bug de dano duplo**: o motor aplicava 25 flat (`meleeHit`) *e* o jogo 16+bonus no mesmo [J].
7. SFX sempre ao mesmo pitch; morte com pop-out abrupto do corpse.

## Técnicas introduzidas

### Engine (reutilizável)

| API | Onde | Efeito |
|-----|------|--------|
| `hitStop(state, sec, scale)` / `tickHitStop` / `hitStopActive` | `src/extras/game-feel.ts` | Freeze-frame de impacto (`state.time.timeScale`); pedidos empilham (max duração / min escala). |
| `applyCctKnockback` / `tickCctKnockbacks` | `src/extras/game-feel.ts` | Empurrão ease-out para corpos CCT: escreve `Transform`+`Rigidbody`+`poseDirty` (mesmo contrato do lunge dash); suspende o `NavMeshAgent` durante a shove. |
| `addCameraShake(trauma)` / `tickCameraShake` / `cameraShakeSample` | `src/plugins/player-controller/fx.ts` | Trauma model (amplitude = trauma², decay 1.4/s). Integrado no fim do `ThirdPersonCameraSystem`: offset noise + micro-roll; **unscaled time** para tremer durante o hit-stop. O offset nunca contamina o follow suavizado (`current*`). |
| `staggerAi(state, eid, sec)` + `AiStateComponent.staggerTimer` / `lungePhase` | `src/plugins/rpg-ai` | Interrompe a lunge (→ ready, mode ATTACK) e congela a FSM; `lungePhase` (U8) expõe windup/lunge/recovery para telegraphs de apresentação. |
| `grantInvulnerability(eid, sec)` + `Health.invulnTimer` + `CombatInvulnSystem` | `src/plugins/combat` | I-frames: `damageHealth` ignora golpes (sem HP, sem eventos — watchers não leem hits fantasma). |
| `setPlayerMeleeDamage(n)` | `src/plugins/player/gltf-systems.ts` | 0 = desativar o meleeHit flat do motor (o jogo fica dono do dano; harvest/Destructible tem path próprio e não é afetado). |
| `setPlayerAttackTimeScale(s)` / `getPlayerAttackTimeScale()` | idem | Playback do clip de ataque (default **1.4** — Quaternius ~1.5s é lento). Toda a marcação de impacto **divide por este valor**. |
| Preset `slash` | `src/plugins/particles` | Arco one-shot additive (`slash_01.png`, Kenney CC0) para o flash de impacto. |

### Exemplo (simple-rpg)

- `melee.ts`: knockback 0.8/1.3 m (crit) × 0.2 s; hit-stop 70/110/120 ms
  (normal/crit/kill) a `timeScale 0.05`; shake 0.22/0.4/0.5; pitch jitter ±8%;
  **whoosh 120 ms antes do contacto** (`WHOOSH_LEAD`), dano no pico do clip.
- `creature.ts`: stagger no HP-drop (default 0.32 s; **bosses têm poise** —
  `cfg.hitStaggerSec ?? (isBoss ? 0 : 0.32)`); telegraph glow âmbar→vermelho
  no windup (reutiliza `collectFlashMats`); rotação com damping exponencial
  (tau 0.09 attack / 0.14 chase via `shortestAngleDelta`); corpse **sink**
  (últimos 0.5 s afundam em vez de pop-out); `isBossCreature(eid)` export.
- `main.ts`: `setPlayerMeleeDamage(0)` no boot (fix dano duplo);
  `CombatFeedbackSystem` isHero → vignette vermelha + shake + i-frames 0.35 s;
  `GameFeelSystem` (late, after `PauseSystem`) tica hit-stop + knockbacks.
- `hurt-vignette.ts`: overlay DOM radial-gradient (o stack de pós-processamento
  fica intocado — banda subtil do exemplo mantida).
- Timing do ataque: clip 1.5 s × 1.4 = swing de ~1.07 s; impacto a
  `dur × 0.35 / 1.4` ≈ 0.375 s. HP dos mobs subiu ~×1.6, bosses ~×1.15.

## Armadilhas (QA no browser)

0. **Held items desapareceram com a migração do pool** (dupla causa, ambas
   silenciosas): (a) `HELD_MODEL` apontava para `/assets/meshes/*.glb` (raiz)
   mas as armas vivem em `/assets/meshes/props/` no pool partilhado — o Vite
   serve HTML de fallback com status 200, o parse falha e a mão fica vazia;
   (b) o herói do pool usa esqueleto Mixamo-ish (`hand_r`) e a engine
   procurava `RightHand` hard-coded — o `applyHeldItem` saía antes de sequer
   pedir o load. Fixes: caminhos `props/` no exemplo e
   `findRightHandBone()` na engine (candidatos `RightHand`/`hand_r`/`Hand_R`/
   `right_hand` + fuzzy, com cache por nome). Convenção: held GLBs que falhem
   agora logam `[player-gltf] held-item load failed` uma vez por url.
1. **`PauseSystem` apagava o hit-stop**: o pause coordinator re-afirma
   `timeScale = ps.timeScale` no grupo `late` sempre que diverge — limpava o
   freeze antes de o scheduler o ver. Fix: `tickHitStop` re-aplica a escala
   ativa, e o `GameFeelSystem` corre em `late` **after `PauseSystem`**; a
   expiração restaura o valor de antes. Pausa dura (`timeScale 0`) segura o
   stop pendente sem lutar com o pause.
2. **Firefox em background não corre rAF** → o loop morre e o mundo congela a
   meio do boot. QA sem foco: usar `__VIBEGAME__.step(1/60)` em lotes
   (~45-95 steps para assentar spawn/física) e despachar `FocusEvent('focusin')`
   sintético no canvas antes dos `KeyboardEvent` — o input plugin exige
   `focusedCanvas`.
3. **Nomes de componentes no debug bridge**: `vg.state.getComponent()` usa os
   nomes canónicos kebab (`'ai-state'`), não os símbolos TS
   (`'AiStateComponent'` devolve undefined); `vg.query()` normaliza ambos.
   `vg.entity()` recebe **nome**, não eid; para SOA usar `vg.state`.
4. **Dano duplo motor+jogo** era invisível no código do jogo — o `meleeHit` do
   motor (25 flat, com `ATTACK_IMPACT_FRACTION` próprio) dispara no mesmo
   mapping `primaryAction → KeyJ`. Qualquer jogo com swing próprio deve chamar
   `setPlayerMeleeDamage(0)` no boot.
5. Knockback e steering/navmesh lutam entre si — o helper suspende o
   `NavMeshAgent` durante a shove e o stagger congela a FSM; usar os dois
   juntos (knockback + stagger) é o que evita a disputa de escrita XZ.

## Contratos mantidos

- `ATTACK_IMPACT_FRACTION = 0.35` (contrato do GDD) — intocado; apenas dividido
  pelo `attackTimeScale` para seguir o clip acelerado.
- Regra dos 3 canais de feedback (número + som posicional + partículas) —
  reforçada com hit-stop/shake/knockback por cima.
- One-shots curtos + pitch jitter ±8% (swing/hurt/death) — nada de caudas.

## Verificação

- Unit (bun): `tests/unit/game-feel/{hit-stop,cct-knockback}.test.ts`,
  `tests/unit/player-controller/camera-shake.test.ts`,
  `tests/unit/rpg-ai/stagger.test.ts`, `tests/unit/combat/invuln.test.ts`.
- Runtime (browser, via `__VIBEGAME__.step`): dano único 65→49 (16) ao
  frame ~23 (0.38 s = pico exato), `timeScale 0.05` durante ~70 ms,
  knockback +0.67 m ease-out, stagger 0.3 s.
