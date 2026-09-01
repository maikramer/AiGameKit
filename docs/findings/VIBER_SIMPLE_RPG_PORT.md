# Port simple-rpg → Viber: checklist de sistemas

Data: 2026-08-31 · Estado: live (fila de trabalho do port "tudo para Luau/subsistemas/UI")

Objetivo: **portar TODOS os sistemas que os XMLs do simple-rpg usam** — todos os
scripts (TS → Luau), todos os subsistemas de gameplay, menus e UI — do exemplo
browser (`VibeGame/examples/simple-rpg/`) para a engine nativa (`Viber/`).

Este ficheiro é a FILA: a cada loop pega-se UM sistema da lista, implementa,
valida (testes + bridge in-game) e marca. Inventário-fonte completo:
`VibeGame/examples/simple-rpg/` (main.ts 1.6k linhas, `src/game/` 21 módulos,
`src/scripts/` 45 entity scripts, UI DOM/CSS).

## ✅ Já portado (não refazer)

| Sistema | Onde |
|---------|------|
| Mundo XML completo (38 XMLs, 0 refs `.ts` restantes) | `Viber/examples/simple-rpg/world/` |
| Terreno heightfield + LOD + pads/lakes/rivers/roads/pontes | `src/terrain/` |
| Céu procedural + shader, DayCycle, WorldBorder, Weather (parcial) | `src/sky.rs`, `src/worldsys.rs` |
| Player 3ª pessoa + câmara follow/steer | `src/player.rs`, `src/camera.rs` |
| Melee básico (J/clique/R), morte → animação+XP+toast, espada na mão (grips) | `src/combat.rs` |
| Luau runtime + API `viber.*` v2 (estado/entidade, percepção, actuação, IA primitiva, dano/XP/toast/interação) + 35 scripts portados | `src/luau.rs`, `examples/simple-rpg/scripts/` |
| Congelamento de IA por distância (ScriptActivation 45 m) | `src/luau.rs` |
| Spawners estáticos/dinâmicos determinísticos + avoid-water/road | `src/spawner.rs` |
| Partículas CPU (10 presets) | `src/particles.rs` |
| BGM em camadas por zona (6 MusicLayers, crossfade) + AudioMixer | `src/music.rs` |
| HUD base: HealthBar/XpBar sync, prompt, bússola, minimapa base, balloon 4 s, BossBar/TargetBar estáticos | `src/hud.rs` |
| Animação glTF (clips por nome, CharacterAnimator, walk/idle criaturas) | `src/animation.rs` |
| Debug bridge BRP: screenshot/tree/logs/input sintético | `src/bridge/` |
| glTF meshopt + KTX2 + de-quantização | `src/meshopt.rs` |

## 🎯 Fila (um sistema por loop, por ordem sugerida)

### Loop 1 — Debugging & Profiler — ✅ DONE (2026-09-01, commits c3da0cc5 + 817c60e1)
- [x] `src/profiler.rs`: DiagnosticsStore (FPS, frame time, entity count) +
      janela deslizante própria; overlay bevy_ui togglável (**F3**, visível por
      omissão, canto inferior-direito); método bridge `viber.profiler` +
      cliente `viber debug prof [--json]`; `VIBER_PROF_LOG=1` →
      LogDiagnosticsPlugin.
- [x] `vitals::debug_damage` ligado no main.rs — teclas **H** (−10 HP) /
      **N** (cura) / **K** (+10 XP); `J` saiu (é o ataque do herói).
- [x] Evidência: validação in-game no simple-rpg completo via bridge —
      `viber debug prof` → `{entities: 46752, scripts: {total: 2823, active:
      52}, particle_emitters: 105, terrain_chunks: 15625, fps, frame_ms}` —
      2823 scripts instanciados com só 52 ativos = LOD de IA a funcionar;
      overlay visível em /tmp/viber-prof-before.png, F3 alterna (screenshot
      after 11 KB menor); 385 testes + clippy -D warnings verdes.
      Resíduo conhecido: 1 erro de shader WGSL no log (refactor bindless do
      dono do sky.rs em curso — não é do profiler).

### Loop 2 — Vitals & feedback de combate — ✅ DONE (2026-09-01)
- [x] `src/feedback.rs` (novo): dano flutuante 3D (pool de 14 slots world-
      anchored, sobe+desvanece 0,9 s), hurt vignette (decai 2,2/s),
      i-frames 0,35 s, TargetBar (soft-lock TTL 8 s), BossBar com HP real
      (`name="boss"`), RespawnSystem (HP 0 → `Dying` 2 s → praça/portões
      mais próximos, HP cheio + i-frames), StatusEffects (veneno 1 tick/s,
      `viber.apply_status("venom", secs)` para scripts).
- [x] Path ÚNICO de dano: scripts (`damage_player`) e debug key H vão todos
      por `PlayerHurt` → i-frames/vinheta/número/morte. `ensure_creature_
      vitals` dá `Health` a bosses estáticos (antes estavam inatingíveis).
- [x] Camera shake: adiado (câmara é de outro agente) — follow-up.
- [x] Evidência in-game: HP 100→60 sob H (i-frames a espaçar), morte +
      logs `herói caiu`/`respawn na praça`, HP 100/100 pós-respawn; "-10"
      flutuante capturado (projeção Vec2(640, 278)); vinheta vermelha
      visível no frame; BossBar real renderizada. 392 testes + clippy -D
      warnings verdes. Fix durante o loop: gate de i-frames por `timer > 0`
      (componente persistente bloqueava para sempre após o 1.º golpe).

### Loop 3 — Quests & diálogo real
- [ ] `src/quests.rs`: dados das 21 quests (5 JSONs → embutidos/RON), estado
      ativa/completa/entregue, objetivos kill/collect/visit, recompensas
      gold/xp/items.
- [ ] DialogueNPC: flow real [E] (intro → em progresso → completa), linhas por
      quest-id; balão com as linhas certas.
- [ ] QuestTracker UI (canto, max 4), QuestsTab no modal, jingle de conclusão.
- [ ] Notice-board: bounties repetíveis (aceitar/entregar).
- [ ] Hooks para scripts Luau: `viber.quest_state(id)`, `viber.complete_objective(...)`.

### Loop 4 — Economia, inventário & colheita
- [ ] Vault (gold/wood/stone) real; ResourceChips com valores vivos; ícones
      `hud_*.png` no HUD.
- [ ] Colheita: tree/rock/mushroom → loot + "+1 Wood/Stone" flutuante + SFX +
      destrutível (hits/break-style) — port do DestructiblePlugin mínimo.
- [ ] Item defs (poção/antídoto/bomba/anel/espada-upgrade, maxStack) + hotbar
      consumível [1] poção / [2] antídoto com contagens.
- [ ] Chest (loot real), merchant banter melhorado (loja fica no Loop 5).

### Loop 5 — UI & menus (bevy_ui)
- [ ] TabbedModal [Q] com tabs REAIS: Inventory, Skills, Options (volumes +
      Save/Load), Wiki, Quests.
- [ ] Loja do mercador [K]: painel comprar/vender navegável por teclado, pause do
      sim quando aberto.
- [ ] Painel da fogueira: descanso + menu de viagem (W/S/J).
- [ ] Loading screen + controls hint (barra de teclas).
- [ ] Toasts empilhados com fade (hoje: 1 toast por ScriptToast?).

### Loop 6 — Travel, Nota, wayfinding & IA de grupo
- [ ] 12 marcos da Nota (nota-landmarks) + [F] "Medir e assinar" (crédito quests
      visit) + snapshot p/ save.
- [ ] Viagem rápida (fogueira → marcos) + respawn points (nearestRespawn).
- [ ] Minimap blips por categoria + waypoints + WaypointArrow screen-space +
      pips no compass; watch-guard fixa os 4 lookouts.
- [ ] Enemy registry (aliveInBiome) + gating do boss final (só spawna com bioma
      limpo); aggro-chain (dano alerta aliados próximos).

### Loop 7 — Save/load & opções
- [ ] Save/load em disco (JSON/RON) com serializers por módulo (progress: XP/
      skills/anel/espada; nota: marcos; vault; quests) — botões na tab Options.
- [ ] Volumes master/music/sfx no menu (AudioMixerSettings já existe).
- [ ] i18n PT/EN mínimo (strings de UI) — se barato.

### Loop 8 — Skills, abilities & combate avançado
- [ ] Abilities: [C] dash (i-frames), [E] cura, [R] golpe forte radial — barra com
      cooldown sweep.
- [ ] Skill bar passivas (8 skills, requires) + playerStats (attackBonus, ring
      +15% speed, swordLevel, buffs, guard).
- [ ] Melee depth: combo ×3 + finisher, crit 15%/backstab ×2, guard/parry [L] +
      riposte, hit-stop, knockback, execute <15%, lunge/soft-lock.
- [ ] Bombas: [B] drop/arremesso com arco, pavio 1,5 s, explosão 90 dano raio 6;
      craft na bigorna (2 pedra + 1 madeira).

### Loop 9 — Mundo vivo (polish)
- [ ] BiomeRegion: tint/fog/bgm/exposure por região (dark-forest/desert/swamp/
      frozen-peaks) — port do VibeGame.
- [ ] Orçamento de PointLights (acende as 12 tochas mais próximas — 69 luzes no
      mundo).
- [ ] SFX espaciais: whoosh/hit (pico ~35%), harvest, UI, loops ambient-water com
      histerese (8 águas); bank de sons ~45 clips → subset.
- [ ] NPC idle gestures (call/talk/foldarms) p/ townsfolk/merchant/healer/guard.
- [ ] AdaptiveQuality (DPR por target FPS) — se aplicável ao render Bevy.

### Loop 10 — Física Fase 3 & extras
- [ ] Knockback físico (impulsos Rapier) em hits/bombas.
- [ ] Destrutíveis com queda física (break-style: fall) — opcional.
- [ ] Projectiles (legado archer — só se houver uso real).
- [ ] NavMesh (recast) — provavelmente desnecessário: IA já anda por sample.

## Contratos que o port deve respeitar

- `name="player"` / `name="boss"` / `name="merchant"` (mundo refere por nome).
- `dialogue-id` == id do quest JSON (17 DialogueNPCs, 21 quests).
- `SpawnExclusion` r=52 = zona da vila (registos de spawn/village zones).
- Portais cardeais ±50 (`lookout:` waypoints).
- Teclas autorais: **J** atacar, **E** interagir/falar, **F** ler/abrir, **K**
  loja/bigorna, **H** fogueira, **Q** modal, **C/E/R** abilities, **1/2/B**
  consumíveis/bomba, **L** guard, **F3** profiler (novo, análogo ao [P] browser).
- XP: kill +15 (combat.rs); quest rewards pelos JSONs.
- Mundo: world-size 4000, heightmap `.ahgt` (fallback procedural no Viber).

## Validação por loop (gate)

1. `cargo test -p viber` + `cargo clippy -p viber -- -D warnings` (só ficheiros
   próprios; não fmt global).
2. In-game via bridge: `scripts/instance-lock.sh exec -- cargo run -p viber -- run
   examples/simple-rpg/world.xml --bridge` → `viber debug screenshot/logs/tree`.
3. Marcar a secção aqui com DONE + evidência (path de screenshot/log).
