# Viber multi-agente — coordenação de escopos, instâncias e assets

Data: 2026-08-31 · Status: live (coordenação de agentes paralelos no crate `Viber/`)

## Contexto

Vários agentes editam o crate `Viber/` (Bevy 0.19) em paralelo e rodavam
instâncias de teste simultâneas sem coordenação — com `pgrep`/`kill` a
matarem instâncias uns dos outros. Este doc fixa o mapa de escopos
excluívos, as regras de concorrência e o inventário de assets em falta.

## Mapa de escopos EXCLUÍVOS por agente

Cada agente só edita os ficheiros da sua linha. Qualquer alteração fora do
escopo exige coordenação com o orquestrador.

| Domínio | Ficheiros (excluívos) | Notas |
|---------|----------------------|-------|
| **Terrain** | `src/terrain/` (todo o diretório) | heightmap/pads/águas do mundo |
| **Sky** | `src/sky.rs` + `src/sky.wgsl` | dono único; ver binding canónico abaixo |
| **Luau** | `src/luau.rs` | scripting/sandbox — *assumido pelo agente de assets/infra por atribuição direta do orquestrador (2026-08-31, tarde): runtime ligado + API v2 + port dos 28 TS* |
| **IA** | `src/ai.rs` | comportamento de NPCs/inimigos |
| **HUD/Vitals** | `src/hud.rs` + `src/vitals.rs` | overlay e saúde/fome/etc. |
| **Mundo/Personagem** | `src/terrain/`, `src/physics.rs`, `src/animation.rs`, `src/player.rs`, `src/particles.rs`, `src/worldsys.rs`, câmara em `src/recipes/spawn.rs` | terreno, colisão (Rapier), animação glTF, controlo do herói, assentamento no chão, iluminação do ciclo dia/noite |

Infra partilhada (só o agente de coordenação toca, ou com aviso no canal):
`scripts/instance-lock.sh` (lock de instância — uso livre, edição coordenada),
`docs/findings/VIBER_MULTI_AGENT.md` (este doc),
`examples/simple-rpg/shaders/sky.wgsl` (cópia sincronizada de `src/sky.wgsl` —
só o agente Sky edita o conteúdo; ver abaixo).

## Regras de concorrência

1. **NUNCA `pkill`/`kill` processos viber.** Se uma instância de teste está
   a correr, é de outro agente. O `kill` é recusado se vramd estiver busy e,
   aqui, é proibido por norma: mata o trabalho alheio e corre a fila.
2. **Lock de instância para testes — obrigatório.** Antes de rodar
   `viber run` (ou qualquer instância do jogo) num teste/manual, adquira o
   lock `/tmp/viber-instance.lock` via `scripts/instance-lock.sh`:

   ```bash
   # Padrão — script de teste (o trap EXIT liberta sozinho):
   source scripts/instance-lock.sh
   viber_lock_acquire "meu-teste" || exit 1
   cargo run -p viber -- run examples/simple-rpg/world.xml

   # Alternativa — wrapper:
   scripts/instance-lock.sh exec -- cargo run -p viber -- run world.xml

   # Consulta (exit 0 = instância a correr de outro agente):
   scripts/instance-lock.sh is-locked
   ```

   O lock falha (exit 1) se outro processo vivo o detém; lock órfão (PID
   morto) é detectado como stale e assumido. Override de caminho:
   `VIBER_INSTANCE_LOCK=...`.
3. **Builds partilham `target/`** — o cargo serializa com file lock. Se um
   build falhar com `Blocking waiting for file lock` ou erro transitório de
   lock, **reintente** (não limpar `target/`, não mudar de dir de build).
4. **Commits só pelo orquestrador.** Agentes trabalham no `main` sem
   commitar; o orquestrador integra e comita os resultados.
5. **Nada de formatters/linters globais** (`cargo fmt` no crate inteiro,
   etc.) — tocam ficheiros de outros escopos.

## Sky shader — binding canónico

`Viber/src/sky.wgsl` é a FONTE (embutida via `include_str!` em `sky.rs` e
escrita no world dir a cada arranque como `shaders/sky.wgsl`).

O binding (2,0) **tem de ser** `var<storage, read> sky: SkyUniform;` — o
derive `AsBindGroup` da Bevy 0.19 gera um layout Storage-LOAD para o bloco;
com `var<uniform>` a validação falha em runtime ("doesn't match the shader
Uniform"). O ficheiro já foi regenerado com a variante uniform por outro
agente; a variante storage/read está agora fixada no ficheiro fonte (com
comentário explicativo) e na cópia do exemplo.

**RESOLVIDO (2026-08-31, agente de assets/infra — coordenação: o dono do Sky
estava a iterar noutra frente e o xadrez bloqueava o exemplo inteiro):** o
xadrez azul/branco NÃO era o shader nem o binding — era **winding do mesh do
domo**. Em `sky_dome_mesh()` o split do quad era `[a,e,d, a,e,c]`: o cálculo
vetorial mostra `[a,e,d]` com normal **para fora** (culled visto de dentro →
aparecia o clear-color) e `[a,e,c]` para dentro — um triângulo sim, um não,
exatamente o xadrez. Fix (1 linha): `indices.extend([a, d, e, a, e, c]);`
Verificado: mundo `bare` (céu sem nuvens/sol) passou de checker completo para
gradiente suave ([sky-bare.png](file:///tmp/viber-shots/sky-bare.png) vs
[sky-bare-fixed.png](file:///tmp/viber-shots/sky-bare-fixed.png)). Bissecção
que isolou: full→checker, `cloud-density=0`+`sun-intensity=0.1`→checker
puro (matou teoria de nuvens/FBM), sem `<Sky>`→azul chapado, só-terreno→sem
checker.

Ao editar `src/sky.wgsl`, sincronizar sempre a cópia:

```bash
cp Viber/src/sky.wgsl Viber/examples/simple-rpg/shaders/sky.wgsl
```

## Assets em falta — `*_collision.glb` (65 ficheiros) — RESOLVIDO 2026-08-31

**Atualização (agente de assets/infra):** os 65 `_collision.glb` **existem no
pool** (`Viber/examples/shared-assets/public/assets/meshes/**` — o pool foi movido
para dentro de Viber em 2026-08-31) — o doc
original confundiu o pool com o espelho do exemplo. Nunca apareceram no
espelho porque os paths só ocorrem **dentro do valor** de attrs `collider="…;
mesh-url: …"` (o scanner só lia attrs cujo *nome* termina em `url`).
`scripts/sync_assets.py` foi corrigido: (1) attrs de asset sem sufixo url
(`texture=`, `terrain-texture=`, `icon=`) agora são recolhidos; (2) para cada
GLB visual espelhado, o irmão `<base>_collision.glb` do pool entra na fila.
Resultado: 108 ficheiros novos no espelho (vale_grass.png incluído via
`texture=`), **zero** `Path not found` em runtime (verificado). A tempestade
de 404 e o terreno invisível (material não-preparado — ver WIP de
`terrain/runtime.rs`) tinham esta mesma causa.

Pendência menor: `/assets/icons/hud_*.png` (5 urls) não existem em lado
nenhum — o pool não tem pasta `icons/`. Cosmético (HUD usa texto).

## Achados do agente de assets/infra (2026-08-31)

1. **`main.rs` — bridge perdida na delegação**: `viber run world.xml
   --bridge PORT` dentro do checkout delega em `cargo run -- run <world>`
   **sem propagar `--bridge`** → a bridge nunca ligava quando o agente usava
   o binário do checkout. Corrigido (`delegate_run_to_cargo` recebe e passa
   `bridge`).
2. **Cidade renderiza** depois do sync de assets + WIP dos agentes de
   terreno/mundo: muralha, portão, casas, mercado, bancos e NPCs visíveis e
   nas posições certas (y≈24.6 = heightfield real). O invisível anterior era
   ausência de assets + parse GLB lento em build debug — não é bug de
   `GltfScene`.
3. **Grid de terreno 2× (para o agente de Terrain):** a árvore live mostra
   **15 625 chunks = 125×125**, `chunk 0-0` em `(-3968, 0, -3968)`, espaçamento
   64 m → cobertura **±4000 m** para um `world-size="4000"` (deveria ser
   ±2000). Os índices parecem centrados em 0 (`-62..+62` = 125 valores) em vez
   de `0..count` com offset `−world/2`. Efeito: anel de chunks fora do
   heightfield (amostragem out-of-bounds) e o dobro da memória/draw. O resto
   (plaza branca = base-color sem textura de estrada) é provavelmente o mesmo
   caminho de material que o WIP do runtime já cobre.
4. **Xadrez azul/branco no céu — RESOLVIDO:** era **winding do mesh do domo**
   (ver secção *Sky shader — binding canónico* abaixo): o split `[a,e,d]`
   tinha normal para fora e era culled de dentro — um triângulo sim, um não.
   Fix em `sky_dome_mesh()`: `[a, d, e, a, e, c]`. Bissecção que isolou
   (mundos mínimos, cópias /tmp com assets symlinkado): full→checker,
   `cloud-density=0`+`sun-intensity=0.1`→checker puro (matou a teoria
   nuvens/FBM), sem `<Sky>`→azul chapado, só-terreno→sem checker.
   **Estado pós-fix (mundo completo):** vila inteira renderiza (muralha,
   portão, casas, mercado, fogueira, bancos), terreno verde, sombras, HUD,
   céu limpo — screenshot `final-shot-2/3.png`. Falta vs referência VibeGame:
   praça/estradas com textura cobblestone (hoje base-color branco — road
   texture não aplica, escopo Terrain) e minimapa sem blips.

## Praça branca + estradas listradas — RESOLVIDO (2026-08-31, rodada 2)

1. **Mancha branca da praça NÃO era terreno.** Mundo de identificação
   (terrain `base-color="#ff0000"`, cópia /tmp): o terreno ficou vermelho e a
   praça continuou branca → malha separada. Culpado: o decal
   `<Plane half-size="10 10">` em `world/cities/discordia/roads.xml` — a
   migração derrubou o `texture-url` original e sobrou o material default
   branco. **Fix:** primitivas agora aceitam `texture=`/`texture-url=` +
   `texture-tile-size=` (UVs reescaladas de 0..1 para metros/tile; suporte em
   `recipes/mod.rs` + `recipes/spawn.rs`) e o decal recebeu
   `texture="…/cobblestone_road.png" texture-tile-size="4"`.
2. **Listras verde/branco nas artérias:** `RIBBON_LIFT` 0.06 m era sub-pixel
   a médias distâncias (far plane do domo 4000 m) → z-fighting ribbon ×
   terreno. Bump para 0.2 m (`terrain/roads.rs`). Restou aliasing de
   minificação da textura em ângulo raso (precisa anisotropic filtering —
   follow-up Bevy, não bloqueia).
3. Pool movido pelo utilizador para `Viber/examples/shared-assets/public`
   (o de `VibeGame/examples/` foi removido); default do `sync_assets.py`
   atualizado (com fallback para o caminho antigo). Ícones do HUD
   (`hud_health/gold/wood/stone.png`) agora existem no pool e foram
   espelhados — os avisos "sem fonte no pool" acabaram.

### Opções para um passo futuro (NÃO executado agora)

1. **Gerar colisores low-poly** a partir dos GLBs visuais do pool
   (`village_forge_lod0.glb` etc.) — ex. `text3d collision` / decimate +
   export do LOD base, mantendo o sufixo `_collision.glb`. Custo: passo de
   pipeline + espaço no pool; benefício: colisão trimesh fiel onde ela é
   realmente usada.
2. **Ignorar** — estes colisores eram do esquema precompute; a física do
   jogo usa primitives (cápsula/cilindro/AABB via `fitColliderFromAabb` e
   `PrecomputePlugin`), e o mesh-collider só faz fetch para
   TriMesh/ConvexHull. Migrar as entidades para `shape: precompute` ou
   primitives equivaleria, na prática, a não precisar dos GLB.

Referência do desenho precompute:
`docs/findings/PRECOMPUTE_COLLIDERS_FINDINGS.md`.

## Luau Fase 2 — runtime ligado + API v2 + port dos 28 TS (2026-08-31, tarde)

**Decisão de arquitetura (orquestrador):** a engine NÃO trava a IA de inimigo
em Rust — ela provê os BLOCOS PRIMITIVOS e o Luau compõe qualquer inimigo.

Estado anterior: `luau.rs` existia (874 linhas, 5 funções) mas o plugin **não
estava adicionado no `main.rs`** e nenhum `.lua` referenciado existia no disco
— a camada de scripting estava 100% dormente.

### O que mudou

1. **Wiring**: `LuauScriptPlugin { scripts_dir: world_dir/scripts }` no
   `main.rs`; `spawn_entity` insere `LuaScriptRef` para `spec.script`;
   `<Creature script="…">` → `StaticSpawnerSpec::template_script` → instâncias
   dinâmicas nascem com `LuaScriptRef` (sem script no template, cai na FSM
   Rust `EnemyCreature` como fallback).
2. **API v2 (`viber.*`)** — blocos primitivos:
   - Estado por ENTIDADE (chunks são partilhados): `state()` → tabela
     persistente keyed por entity bits; `home()` = posição de spawn.
   - Percepção: `player_position()` → `(has, x, y, z)`, `distance_to_player()`.
   - Actuação: `move_towards(x, z, speed)` / `move_by(dx, dz)` (snap no
     terreno na aplicação), `face_towards(x, z)` / `face_player()`,
     `set_position` legado mantido.
   - IA: `next_state(cur, dist, aggro, deaggro)` (máquina wander↔chase da
     engine, exposta), `wander_target(radius)` (determinístico, seeded).
   - Combate/progressão: `damage_player`, `heal_player`, `add_xp`,
     `teleport_player`.
   - UI/interação: `toast(msg)` (evento `ScriptToast` para o HUD + log),
     `set_interaction(label, key, range?)` (componente `ScriptInteraction`),
     `interacted(key)` (tecla just-pressed E player a ≤3.5 m).
3. **34 scripts portados** (`examples/simple-rpg/scripts/`): inimigos
   (wolf/bandit/shade/scorpion/bogling/slime/goblin-wander) e 4 bosses com FSM
   wander/chase/attack + cooldown de dano — todos paramétricos no topo do
   ficheiro; POIs ×7 (toast + XP único); colheita (tree/rock/mushroom);
   interações (well/healer/merchant/anvil/notice-board/campfire/chest/
   crystal-shrine/stone-pillar/watch-guard); townsfolk (wander + face player);
   building-portal (teleporte; v2 mapeia destino por `viber.name()`);
   ambient-water (no-op reservado). XML: refs `.ts` → `.lua` (0 restantes).

### Gotchas (para o próximo que tocar)

- `viber.player_position()` devolve **4 valores** `(has, x, y, z)` — limitação
  `IntoLuaMulti` de Option; scripts usam `local has, px, py, pz = …`.
- Top-level do chunk roda UMA vez por path (não por entidade): setup por
  entidade vai no `on_update` com guard `if not st.ready then`.
- Bevy 0.19: eventos bufferizados são `Messages`/`MessageWriter`/`add_message`
  (não Event*). `Mut<T>` em pattern: binding sem `mut`/`ref mut`.
- Bash heredoc + aspas tipográficas come bytes UTF-8 — validar sintaxe dos
  .lua subindo a instância e greppando `luau script .* error` nos logs da
  bridge (warn-once; silêncio = todos carregaram).
- Suite: 376 testes verdes; validação in-game via bridge: `slime ativo` no
  ring de logs, 0 erros de script.

## Congelamento de IA + animação de criaturas + melee (2026-08-31, noite)

Pedido do utilizador: inimigos perseguiam de longe demais (todos convergiam
para a cidade); nada tinha animação; combate não existia.

1. **Congelamento ("LOD de IA")** — `luau::ScriptActivation { radius }`
   (default 45 m, autorável via `activation-radius` no spawner): além do raio
   do player o `on_update` NEM RODA (lógica zero) e o driver de animação
   também para. Estático e dinâmico cobertos. Validado in-game: slimes a
   130+ m deixaram de logar (antes: 1+ log; depois: 0).
2. **Aggro afinado ~10 m por tipo** nos scripts (slime 6, wolf 11, bosses
   12–14; deaggro +5). "De perto vai pra cima do player" = aggro por script.
3. **Animação de criaturas**: spawn dinâmico insere `AnimatedScene` (GLBs dos
   inimigos TÊM clips: attack/death/hit/idle/walk); `bind_animations` (que já
   era genérico) liga `CharacterAnimator`; novo driver
   `combat::drive_creature_animation` (walk/idle por velocidade real,
   respeitando o congelamento).
4. **Melee do herói** (`src/combat.rs`, ficheiro NOVO, sem dono): clique
   esquerdo (ou R) — alvo com script mais próximo a ≤2.8 m no cone frontal,
   25 de dano a cada 0.55 s; morte → remove script, anima `death` (clip por
   NOME — `AnimState` não tem Death), cadáver some em 1.4 s, +15 XP com toast.
   Spawn dinâmico insere `Health` nas criaturas. `ensure_player_vitals`
   garante Health/Xp no herói (o `damage_player` dos scripts dependia disso).

Follow-ups de combate (referência VibeGame main.ts): weapon trail, skills,
arco/projecteis, números de dano, partículas de hit, loot.

## Arma na mão + tecla J (2026-08-31, noite — round 3)

- **Ataque**: agora em **J** (além de clique esquerdo e R) em `combat.rs`.
- **Arma na mão**: `combat::attach_hero_weapon` anexa
  `props/sword_hero_lod0.glb` ao osso da mão do herói — grips copiados do
  VibeGame (`dist/data/held-items.json`): sword pos `[0.12, 0.04, 0.04]`, rot
  `[-1.33, 12.71, 0.96]` rad XYZ, scale 1; bone candidates
  `hand_r`/`RightHand`/… (herói atual usa `hand_r`). Attach via
  `GltfScenePending` como FILHO do osso (cena segue a animação do braço).
  Verificado visualmente pela bridge. Axe/spear/bomb: mesmos grips no JSON
  quando a troca de armas chegar. Grips editor: `VibeGame src/game/grip-editor.ts`.
- **Clip de ataque do herói**: `hero_lod0.glb` tem clip `attack`; tocado por
  NOME no swing (sem mexer em `animator.state`, que o driver de movimento
  re-afirma quando difere); `reset_hero_attack_clip` devolve o controle após
  o cooldown.

## Combate validado ponta a ponta (2026-08-31, noite — round 4)

- **Bug do "J não ataca"**: o golpe só tocava feedback se houvesse alvo no
  alcance — sem inimigo perto, nada acontecia. Agora o swing acontece SEMPRE
  (whiff incluído); dano só quando acerta. E **não havia inimigos perto**: os
  spawners dinâmicos nascem a 130–330 m (fora do congelamento) e a
  `SpawnExclusion r=52` impede spawns na cidade — slimes movidos para
  z −50..−115 (tutorial no portão norte).
- **Bug do "inimigos não atacam"**: atacavam, mas o dano não aterrissava em
  DUAS camadas: (1) player sem `Health` (corrido em ensure_player_vitals);
  (2) **HUD estático** — `hud_health_sync`/`hud_xp_sync` existiam no hud.rs
  ("WIRED-BY-ORCHESTRATOR") mas não estavam registrados no main.rs → a barra
  mostrava sempre 100/100. Registrados. Validação: 44 logs de ataque de
  goblins colados no herói, hp 100→0 (morte), HUD espelhando.
- **Faixa vermelha fullscreen no topo com hp 0** (screenshot fight-8.png):
  bug da camada HUD (agente de HUD) — aparece com o sync de HP ativo e hp=0.
- Concorrentes: instância X11 (`WINIT_UNIX_BACKEND=x11`) sobrevive melhor que
  Wayland (janelas fecham sozinhas); profiler.rs de outro agente estava
  quebrado a meio de edit — polling resolveu; 1 falha restante é teste DELE.

## Colheita + troca de armas + primeira skill (2026-08-31 → 09-01 madrugada)

- **J contextual**: `combat::player_melee_attack` consulta `ScriptInteraction`
  (tecla J) em alcance — perto de árvore/pedra o golpe vai para a COLHEITA
  (o script do alvo cuida); senão, golpe de espada.
- **`viber.despawn_self()`** (API nova): árvore cai no 3º corte, pedra quebra
  no 3º — tree.lua/rock.lua reescritos (3 hits, toasts de progresso, +30 XP).
- **Troca de armas no [V]**: `combat::cycle_weapon` + `HeldWeapon` resource +
  `WEAPON_TABLE` — sword/axe/spear com os grips oficiais do held-items.json
  (axe rot [2.98, 12.71, 1.5708], spear idem sword com pos [0.2,0.01,0.04]).
  Bone reutilizado via resource (busca 1x). GLBs já no espelho (props/).
- **Skill bola de fogo (botão direito)**: `cast_fireball` + `fireball_step` —
  projétil emissivo 18 m/s, 40 de dano em área 2.5 m, +XP por abate, cooldown
  1.2 s. (Skills do HUD C/E/R: mapear teclas aos casters quando houver mais.)
- Validação noturna limitada: a sessão gráfica bloqueada faz o compositor
  fechar as janelas da engine ("No windows are open, exiting") — testes 385
  verdes cobrem a lógica; validação visual pendente de sessão ativa.
