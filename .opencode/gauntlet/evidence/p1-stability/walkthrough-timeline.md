# p1-stability — walkthrough ronda 2 (com golden hour fix + respawn boost)

Run de referência: `VIBER_DEBUG_KEYS=1 scripts/instance-lock.sh exec -- cargo run -q
-p viber --release -- run examples/simple-rpg/world.xml --bridge`, binário com os
commits `98abe560` (golden hour), `de0cc415` (respawn streaming) e `e26f577e`
(instrumentação BGM). Hora de jogo arranca a 08:00 (480), ciclo a 1.2 min/s.
Todos os screenshots em `screenshots/` são desta sessão (02:43–03:12).

## A/B do golden hour (o GAP do crítico) — `viber.debug.set_clock`

Mesmo sítio (praça do vale, tp(0,−30)), mesma câmara, `set_time_scale(0)`:

| Hora | set_clock | Screenshot | Resultado |
|------|-----------|------------|-----------|
| 12:01 | `set_clock(721)` | `17-dia-limpo.png` | dia limpo, neutro (referência) |
| 19:16 | `set_clock(1156)` | `18-golden-hour-1916.png` | entardecer DOURADO legível — céu quente, tochas acesas, albedo preservado; **sem o wash vermelho/magenta da r1** |
| 05:46 | `set_clock(346)` | `18b-golden-hour-0546.png` | amanhecer rosa-azulado frio com tochas quentes — legível, em gamut |

Na r1, `18-golden-hour-bug.png` (19:16) era um frame INTEIRO
vermelho/magenta (verde destruído pelo white balance — causa-raiz e fix no
`fixed-list.md` §10).

Média RGB dos frames (160×90 subamostrado): 12:01 → (174,167,157), R/G 1.04
(neutro); 19:16 → (106,79,76), R/G 1.35 (quente, EM GAMUT); 05:46 →
(104,77,77), R/G 1.35. O bug da r1 clampeava o verde a zero (R/G ≫ 2 com G
colapsado).

## Walkthrough (labels completos por linha)

- **[02:50]** S1 arranque — loading levantou, praça do vale; `pos=2.0,-8.0
  hp=100/100`; 0 panics (`01-arranque.png`)
- **[02:56]** S2 combate [J] — goblins no anel sul (`find_all("goblin")` = 2):
  1 morto, herói a 52/100; `xp=15/100` (+15 do kill) (`03-combate.png`)
- **[03:01]** S3 coleta [J] — carvalho `inst-grp-1-0000` a 1.3 m: **madeira +3**
  no vault (`04-coleta.png`). NOTA: o alcance de interação efetivo é
  `BASE_RANGE_M × 0.5` = 1.75 m — a 2.75 m o golpe não conecta (contrato
  `src/interact.rs`, não é bug)
- **[03:02]** S4 loja — vault ANTES da compra: `ouro=32 pocoes=1` → [E] no
  Bram abre o DIÁRIO na tab LOJA → clique "COMPRAR POÇÃO 25" → vault DEPOIS:
  `ouro=7 pocoes=2` (25 gastos, +1 poção) (`05-loja.png`)
- **[03:03]** S5 modal [Q] → tecla 3 (TALENTOS) → clique VITALIDADE I:
  `max_hp=120` (100→120); xp dado via `viber.debug.xp(500)` para ter ponto
  (`06b-modal-skills.png`)
- **[03:04]** S6 fogueira → [G] menu de viagem rápida, Esc fecha
  (`07-viagem.png`)
- **[03:04]** S7 [F11] teleporta ao próximo marco → [F] assina: `heroi@10,-154`
  (Picos Gelados) (`08-marco-picos.png`)
- **[03:05]** S8 biomas + BGM por zona (dumps instrumentados `bgm-*.json`):
  - `=== vila (03:05:01) player@(2,-8)` → layers ativas: **village 0.18**
    (`02b-praca-vila.png`)
  - `=== ermo-deserto (03:05:07) player@(320,320)` → **explore 0.18**,
    village 0.0 (`09-bioma-ermo.png`)
  - `=== floresta (03:05:15) player@(-320,320)` → **explore 0.18**,
    village 0.0 (`10-bioma-floresta.png`)
  - `=== pantano (03:05:21) player@(-320,-320)` → **explore 0.18**
    (`11-bioma-pantano.png`)
  - `=== picos (03:05:28) player@(0,-300)` → **mountain 0.2**, explore 0.0
    (`12-bioma-picos.png`)
- **[03:05]** S9 interior — [E] no portal da capela → fade →
  `cena=interiors/chapel` (`13-interior.png`); saída encostando à porta
  (auto-radius) → `cena=main` na pose exacta (@back). NOTA honesta: as
  MusicLayer morrem com a cena principal — o tab Áudio na capela mostra 0
  layers BGM (o `dungeon` NÃO toca em interiores; observado nesta ronda,
  fix fica para P6)
- **[02:47]** S10 boss banda sul — `tp(-12,-272)` + 6 s de streaming: boss a
  **18 m**, mundo renderizado (boss, totems, pinheiros, montanhas)
  (`14-boss-sul.png`; na r1 este screenshot era um vazio de névoa)
- **[02:46]** S11 morte + respawn — `viber.debug.damage(9999)` → 2 s →
  renasce no portão com HP cheio (`{"hp":100,"x":0,"z":-50}`); **+0.5 s
  depois do respawn o mundo já está lá** (parede, árvores, estrada) — o
  boost de streaming novo (`14c-respawn-mundo-0.5s.png`,
  `14d-respawn-mundo-1.5s.png`, `15-respawn.png`). Na r1 o respawn
  acordava num vazio de névoa
- **[03:12]** S11b timing de QA-teleport (sem boost, por desenho) —
  `tp(-12,-272)` vindo da vila: `13c-qa-tp-stream-1s.png` (névoa aos 1 s) →
  `13d-qa-tp-stream-3s.png` (mundo completo aos 3 s). O respawn de GAMEPLAY
  é que tem prioridade (fix desta ronda); teleportes de QA documentam-se
- **[03:09]** S12 abilities [C] dash, [R] radial, [B] bomba, [L] guard — sem
  panic/erro no log (`06c-modal-sistema.png`)
- **[03:09]** S13 teleports QA — labels por tecla: `f6 → -22,-22`,
  `f7 → -29,-24`, `f8 → -22,-52`, `f9 → -22,-52`, `f11 → -4,-198`
  (F10 dá recursos, não move — contrato)
- **[03:09]** S14 profiler F3 (`15-profiler.png`)
- **[03:10]** S15 save — `viber.ui.action("save","")` →
  `~/.local/share/viber/simple-rpg.save.json` mtime 03:10
- **[03:11]** S16 BGM de combate — golpes no goblin: layer ativa passa a
  **battle 0.22** (hold 8 s pós-aggro) (`03b-combate-battle-bgm.png`,
  `bgm-battle.json`)

## Log final

`log-after.txt` (1000 linhas do bridge). 6 ERRORs, todos ruído conhecido
documentado: 2× `bevy_winit cursor position` (Wayland, 1 por clique
sintético) + 4× `landmass_rerecast` (validador vendorizado vs detail mesh
próprio; a conta cresce com os tiles de montanha visitados — mesma classe
da r1). **Zero panics, zero erros WGSL/WESL, zero erros de bind group.**
