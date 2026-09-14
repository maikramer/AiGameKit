# Quadrúpedes — clips procedurais repensados (galope, attack, death, hit, idle)

Data: 2026-09-14 · Estado: implementado e validado (lobo real + rig sintético)

## Problema (o que estava falso)

O preset `creature` gerava 16 clips, mas os chave eram aliases enganadores:

- **`Animator3D_Death` era `chop_keyframes`** — um golpe de machado como morte;
- **`Animator3D_Hit` era `mine_keyframes`** — um gesto de mineração como dano;
- **`Run` era o walk com amplitudes maiores** — sem galope, sem fase aérea, sem
  flexão de espinha;
- **idle** só respirava (patas 100% congeladas); **attack** animava cabeça/
  espinha mas nenhuma pata;
- o walk usava trot diagonal que funcionava por acidente (dependia de
  `_recover_quadruped_chains` ordenar as patas traseiras-primeiro) — sem
  distinção anatómica frente/trás.

## Desenho

### Anatomia: `_split_front_hind_legs(arm_obj, chains, forward)`
Classifica as patas pela projeção do anexo no eixo forward: dianteiras perto do
pescoço/ombro, traseiras perto da pelvis/cauda. Devolve `(front, hind)` por
lado. Substitui a dependência implícita da ordem do recover.

### Galope: `_gallop_phases(front, hind)` + `_locomotion_cycle` estendido
Fases explícitas por pata (galope transverso): traseiras arrancam em sequência
rápida (0.00 / 0.10), dianteiras a meio ciclo (0.50 / 0.60), suspensão no
último quarto. O motor ganhou:

- `leg_phase_by_leg` — fases por pata (ignora a anti-fase de lado, que o
  trot/alternado usam por omissão);
- `spine_flex_amp` — a espinha é a mola do galope (flexiona na recolha,
  estica no estiramento, 1× ciclo);
- `bob_freq`/`bob_phase` — o trot tem 2 dips/ciclo; o galope tem 1 arco de voo
  por ciclo (pico na suspensão).

**Walk mantém o trot** (é o "walk" legível dos jogos; o 4-beat lateral real
desliza às velocidades de jogo) mas com papel por pata: traseiras ganham drive
(hip ×1.12), dianteiras ganham cushion (joelho ×1.15). Aracnídeos/insetos
(>2 patas/lado) mantêm o gait alternado — `_gallop_phases` devolve None.

### Clips novos
- **`death_keyframes`** (terminal, não cíclico): stagger (cabeça cai, traseiras
  cedem primeiro) → colapso (pelve desce `drop=0.35` no eixo up, corpo rola
  para `side`, patas dobram progressivamente — traseiras antes das dianteiras)
  → bounce amortecido → repouso com micro-respiração em decaimento. A pose
  terminal é estável (últimos frames convergem) — o Viber segura-a
  (`play_death_animation` terminal) e o VibeGame toca `loop: false`.
- **`hit_keyframes`** (one-shot, 16 frames): flinch — recoil no impacto (~t0.25),
  recuperação com overshoot amortecido, pernas cedem (crouch reflexo).
- **`attack_keyframes` v2**: patas passam a participar — traseiras enrolam no
  anticipation e **estendem no strike** (impulso), dianteiras travam/amortecem,
  e o corpo AVANÇA no eixo forward no pico (lunge de 0.15, regressa no settle).
- **idle v2** (`breathe_idle_keyframes`): shift de peso lento (corpo ~3°,
  ancas contrabalançam, joelhos com micro-pressão) + look-around da cabeça
  (noise lento no pescoço) — as patas deixam de estar congeladas.

### Integração
`_PRESETS["creature"]`: `Hit`→`hit_keyframes` (16f), `Death`→`death_keyframes`
(48f) — os aliases `mine`/`chop` desapareceram. O passe IK/limites corre
sobre os clips no game-pack procedural (já existente) e **os geradores são
limits-clean por construção** — o teste `test_generated_clips_are_ik_clean`
corre `enforce_joint_limits` sobre os 6 clips e exige 0 correções.

Nomes mantêm `Animator3D_*`: ambos os runtimes resolvem por normalização
(Viber `normalize_clip_name` → `death`/`hit`; VibeGame aliases em
`resolveClipName`). **Zero mudanças nos engines.**

## Validação

- **Lobo real** (`wolf_rigged.glb` do pool): `game-pack --preset creature
  --force-preset --clips idle,walk,run,attack,hit,death,jump,roar` → 8 clips em
  16.6 s; o passe IK não reportou correções (zero violações no rig real). Nomes
  no GLB: `Animator3D_{Attack,BreatheIdle,Death,Hit,Jump,Roar,Run,Walk}`.
- **Rig sintético** (pytest, bpy real): 17 casos — fases do galope medidas por
  pico de elevação do pé (traseiras Δ<0.3 ciclo, dianteiras ~0.5 depois), os
  6 clips keyam as 4 patas, death desce a pelve e segura, hit ≤16 frames, lunge
  move o corpo, idle tem shift de peso, e **IK-clean = 0 correções em todos**.

## Regressões históricas que isto fecha

1. `Death = chop` e `Hit = mine` (aliases desde a Fase 2.1 dos fallbacks).
2. `Run = walk` amplificado — sem gait assimétrico.
3. Patas congeladas no idle e ausentes no attack.
4. Dependência implícita da ordem de `_recover_quadruped_chains` para o trot
   diagonal — agora a frente/trás é anatómica e explícita.

## Regeneração do pool (assets publicados)

Os assets vivem em `Viber/examples/shared-assets/public/assets/meshes/characters/`
(gitignored). Depois de mudar geradores, regenerar **pelo pipeline** (não à mão):

1. Manifesto temporário com só os assets alvo (o `resume` não tem `--only`):
   copiar o cabeçalho de `manifests/characters.yaml` + os blocos `- id: …`
   desejados para `manifests/_regen.yaml`.
2. Mover (backup, não apagar) os artefactos que se quer refazer:
   `_intermediate/{id}_rigged_animated.glb` e `{id}_lod{0,1,2}.glb`.
3. `cd Viber/examples/shared-assets && gameassets resume --profile game.yaml
   --manifest manifests/_regen.yaml --no-vramd` (o animate é bpy/CPU; `--no-vramd`
   evita tocar na GPU). O pipeline re-corre animate → lod e re-valida.
4. Apagar o manifesto temporário.

**Regenerado (2026-09-14):** `wolf` (8 clips), `scorpion` (8), `sand_worm` (7) —
todos com death a colapsar (pelve −0.349) em vez do golpe.

### Verificação (o que checar sempre)

- **Conteúdo**: pelve Z no primeiro vs último frame do Death — antigo = 0.000
  (golpe rotativo, corpo imóvel), novo ≈ `-drop` (colapso real).
- **Orientação**: bbox em **espaço de cena** (compor translation/rotation/scale
  dos nós até ao mesh — ler `accessor.min/max` local engana: os LODs do pool têm
  o bake de yaw no nó raiz). Canónico = eixo comprido em **Z**.
- **Consistência visual ↔ colisão ↔ precompute**: `{id}_collision.glb` e o
  `aabb` do `{id}_precompute.json` têm de partilhar a mesma orientação do
  `{id}_lod0.glb` (a cápsula vertical é insensível ao yaw; o AABB não).

### Casos encontrados nesta regeneração

- `sand_worm` (Sep 8, pré-canonicalização) estava **de lado** (X-longo) e a
  regeneração corrigiu-o para Z — mas a colisão/precompute ficaram de lado:
  re-aplicado o bake documentado (**−90° yaw** = `[0,−√½,0,√½]` xyzw no nó raiz
  `Mesh`, só chunk JSON) e rodados os cantos do AABB pelo mesmo quaternion.
  **Cuidado com a ordem do quaternion**: glTF usa xyzw; a matemática (Hamilton)
  usa wxyz — trocar a ordem dá uma rotação 180° errada.
- `wolf`/`scorpion` regeneraram já canónicos (o `canonicalize_creature_facing`
  do game-pack roda o mesh nos dados, não o nó) — colisão/precompute intactos ✔.
- **Quirk pré-existente do gate**: o `check glb` de `animated.yaml` falha nos
  intermediates de todos estes assets (`armatures: 0`, `actions: 0`,
  `texture_format: jpeg`) — **antes e depois** da regeneração (o parser binário
  do lab não reconhece estes rigs e os intermediates são JPEG por design; o KTX2
  vive no LOD entregável). O `resume` reporta falha do gate mas os entregáveis
  `lod{0,1,2}` passam as regras próprias — validar o LOD0 com
  `aigamekit-lab check glb {id}_lod0.glb .../rules/lod0.yaml`.

## Fora do âmbito (futuro)

IK de pés com contacto real no chão (foot-planting por pata), 4-beat lateral
walk, claw-swipe anatómico (se o rig tiver braços), knockback dedicado para
criaturas (UAL2 `knockback` só existe no retarget humanoide).
