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

### Loop 3 — Quests & diálogo real — ✅ DONE (2026-09-01)
- [x] `src/quests.rs` (novo): as 21 quests dos JSONs embutidas
      (`examples/simple-rpg/quests/*.json`, mesmo schema do VibeGame);
      `QuestLog` NotTaken→Active→Ready→Done com bounties do quadro
      repetíveis; objetivos **kill** (por tipo de criatura, com alias
      `boss_*` normalizado), **collect** (via `viber.report_collect`) e
      **visit** (proximidade a entidades nomeadas, múltiplos alvos).
- [x] Diálogo [E] nos `<DialogueNPC>`: intro (aceita) → progresso com
      `{remaining}` → entrega com recompensas (XP real; ouro/itens em toast
      até o vault do loop 4). Balão do HUD agora é quest-aware
      (`hud_balloon_update` ficou só com o timer).
- [x] QuestTracker (top-right sob o minimapa, 4 linhas com `x/y`).
- [x] Hooks Luau: `quest_state/quest_accept/quest_turn_in/report_kill/
      report_collect/report_visit`; `notice-board.lua` reescrito sobre os
      hooks (bounties em ciclo); `tree.lua`/`rock.lua` reportam coleta.
- [x] QA: **F7** teleporta ao NPC de quest em ciclo, **F6** ao NPC mais
      próximo, **F8** à criatura hostil mais próxima; toasts espelhados no
      log da bridge (`viber::toast`).
- [x] Evidência in-game: diálogo [E] aceita/progresso com logs
      (`diálogo [E]`, `aceita via diálogo`), 7 abates reportados ao diário
      com "+15 XP" e toasts no log, QuestTracker a vivo com
      "Bram, o ferreiro [0/10]" (screenshot), F6/F7/F8 a funcionar, 0
      panics. Entrega e repetibilidade cobertas por 8 testes unitários
      sobre os JSONs reais (turn-in ao vivo ficou pêndente de navegação
      sintética — teclas cegas não chegam ao NPC com fiabilidade a 1,5 fps).
      400 testes + clippy -D warnings verdes.

### Loop 4 — Economia, inventário & colheita — ✅ DONE (2026-09-01)
- [x] `src/economy.rs` (novo): `Vault` (gold/wood/stone + itens com stack 99,
      `count`/`take` unificados para recursos e itens), **ResourceChips
      vivos** (`chip:gold|wood|stone` mostram o vault real), **hotbar**
      `[1]` poção (cura 50) / `[2]` antídoto (limpa veneno) com contagens e
      cooldown.
- [x] **Collect quests vault-driven**: o progresso de city_wood/city_stone/
      dark-wood/bog-moss lê o INVENTÁRIO (entregar consome os itens) —
      colheita (`report_collect`→vault) alimenta as quests automaticamente.
- [x] Hooks Luau: `vault_get/vault_add/item_add/item_count`;
      `report_collect` agora deposita no vault; `chest.lua` dá loot real
      (+25 ouro, +1 poção); turn-in de quest credita ouro/itens no vault.
- [x] QA: **F9** teleporta ao colhível/interação mais próximo, **F10** dá
      recursos+itens (análogo do `give`/`gold` do VibeGame).
- [x] Evidência in-game: F10 → chips gold 10 / wood 6 / stone 6 (screenshot,
      valores reais), hotbar "[1] Poção x1 / [2] Antídoto x1", poção usada
      com toast "Poção usada (+50 HP)" e consumo do stock, 0 panics. 404
      testes + clippy -D warnings verdes (novo teste: collect lê vault,
      entrega consome).

### Loop 5 — UI & menus — ✅ DONE (2026-09-01)
- [x] `src/menus.rs` (novo): **toasts visuais** (pilha top-center, 5 máx.,
      fade 3 s, espelhados no log), **modal [Q]** com tabs reais — Quests
      (ativas + progresso do QuestLog), Inventário (vault completo), Ajuda
      (controlos/opções) — navegação ←/→/Tab, **loja [K]** perto do
      `name="merchant"`: comprar poção/antídoto/bomba, vender madeira/pedra,
      seleção ↑↓ + [J], auto-close ao afastar; **loading screen**
      "DISCORDIA — a forjar o mundo…" que levanta no arranque; banner da
      fogueira perto da fogueira.
- [x] Hotbar [1]/[2] não consome enquanto modal/loja abertos (`MenusOpen`).
- [x] SkillsTab completa fica para o loop 8 (a tab Ajuda cobre o resto).
- [x] Evidência in-game: loading capturado no arranque; toast "QA: +10
      ouro…" visível (screenshot); modal aberto com tabs e corpo
      (screenshot); loja aberta junto ao mercador com catálogo, venda de
      pedra ×3 (+15 ouro) e compra da poção (−25) ao vivo — ouro terminou
      em 0 como calculado (screenshots). 409 testes + clippy -D warnings
      verdes.

### Loop 6 — Travel, Nota, wayfinding & IA de grupo — ✅ DONE (2026-09-01, commit afac7291)
- [x] `src/travel.rs` (novo): catálogo dos **12 marcos da Nota** (3/bioma,
      labels PT, raios de anotação por bioma — espelha nota-landmarks.ts);
      **[F] "Medido e assinado"** (NotaLog + toast; 3 do bioma → "BIOMA
      ASSINADO"); **viagem rápida [G]** perto da fogueira (lista marcos
      assinados, ↑↓ + [J] teleporta); **Waypoint HUD** (topo do ecrã com
      rumo N/NE/… e distância ao último marco); **EnemyRegistry** (hostis
      vivos por banda centro/norte/sul/este/oeste, snapshot 1 Hz para
      scripts via `viber.alive_in_region(idx)`); **gating do boss final**
      no boss.lua (dorme até a banda sul estar limpa, toast ao despertar);
      **aggro-chain**: `AttackAlert` → `on_player_attack(px, pz)` nos
      scripts a 15 m do alvo atingido (wolf/bandit/goblin forçam chase);
      QA **F11** teleporta ao próximo marco por assinar. Respawn points
      por proximidade já feitos no loop 2.
- [x] Fix no loop: `TravelMenuState` sem `init_resource` (panic no arranque);
      tap sintético da bridge não sobrevive a `keys.pressed` (press+release
      no mesmo lote) — [F] usa `just_pressed`.
- [x] Evidência in-game: "Medido e assinado: Primeiro Mojão (faltam 2 em
      Picos Gelados)" + "marco assinado" nos logs; F11 avançou para o
      próximo marco não assinado; menu de viagem aberto na fogueira (estado
      vazio pré-assinatura, screenshot); 0 panics pós-fix. 415 testes +
      clippy -D warnings verdes.

### Loop 7 — Save/load & opções — ✅ DONE (2026-09-01)
- [x] `src/save.rs` (novo): `SaveGame` em JSON (`~/.local/share/viber/
      simple-rpg.save.json`) com XP, HP, posição, vault completo, estados
      das quests (progresso + marcos visitados), quest_done, marcos da
      Nota e volumes — captura/aplicação puras e testadas (roundtrip JSON).
- [x] **Tab Opções** no modal [Q]: 5 linhas (master/música/sfx/gravar/
      carregar) com seleção ↑↓, volumes ←→ ±10 % (AudioMixerSettings ao
      vivo), **[J] grava** e **[L] carrega** com toasts de resultado.
- [x] Setas ←/→ deixam de trocar de tab na página Opções (ajustam volumes);
      Tab continua a ciclar.
- [x] i18n PT/EN: adiado (todo o jogo está em PT; baixo valor até ter
      jogadores EN).
- [x] Evidência in-game: F10+dano → save com gold 10 / hp 80/100 (ficheiro
      JSON verificado), mais dano → load → HP de volta a 80/100
      (screenshot) + toasts "Jogo gravado"/"Jogo carregado"; 0 panics.
      417 testes + clippy -D warnings verdes.

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
