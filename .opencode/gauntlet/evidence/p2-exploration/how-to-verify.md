# P2 — Exploração (loop BotW): como verificar

Rondas 1–2 · mundo `Viber/examples/simple-rpg/` · engine `Viber/src`.
**Ronda 2 acrescenta:** mapa regional **[M]** com revelação por bioma
(`src/hud/map.rs`) e coluna de fumo korok ×3 (legível a ≥90 m).

## 0. Estado e arranque

```bash
cd Viber
cargo build -p viber --release                    # engine com as mudanças
bash scripts/instance-lock.sh exec -- cargo run -p viber -- run examples/simple-rpg/world.xml --bridge
# noutro terminal (o cliente descobre a porta pela sessão; ou VIBER_BRIDGE_PORT=15702):
./target/release/viber debug probe
```

QA de REPL/screenshot (mesma porta):

```bash
export VIBER_BRIDGE_PORT=15702
./target/release/viber debug lua 'viber.debug.set_clock(600) return viber.debug.player()'
./target/release/viber debug screenshot -o shot.png
```

## 1. Torres de agrimensura (5) — landmark, vantage, teleporte, reveal

IDs das entidades (âncora do teodolito, no topo da plataforma): `atalaia-do-vale`,
`atalaia-do-bosque`, `atalaia-do-ermo`, `atalaia-do-pantano`, `atalaia-dos-picos`.
Estruturas: grupos `tower.<bioma>` (Composition `atalaia-<bioma>-estrutura`).
Catálogo: `<Landmark biome="tower" tower-reveals="...">` em `world/landmarks.xml`.

| Torre | Mundo (x, z) | Reveals |
|-------|--------------|---------|
| atalaia-do-vale | (90, −10) | — (tutorial; bioma `tower`) |
| atalaia-do-bosque | (30, 138) | `dark_forest` |
| atalaia-do-ermo | (130, 60) | `desert` |
| atalaia-do-pantano | (−130, 10) | `swamp` |
| atalaia-dos-picos | (−40, −140) | `frozen_peaks` |

### 1.1 Visível de longe

```bash
# vale: da porta leste da vila, a torre domina a estrada (screenshot 01)
./target/release/viber debug lua 'viber.debug.set_clock(600) viber.debug.tp(56,2) viber.debug.face(90,-10) viber.debug.set_camera{target="player", pitch=5, distance=7} return true'
./target/release/viber debug screenshot -o 01-atalaia-vale-horizonte-vila.png
```

### 1.2 Subida ao topo (verificada ao vivo)

Rampa A (ponta enterrada, 17.5°) → deck de viragem → rampa B (15.3°) → plataforma
(11.2 m). O percurso não tem nenhum degrau vertical; validado a andar de verdade
(`viber.debug.tp` à base + W por ~6 s por rampa, virando no deck):

```bash
./target/release/viber debug lua 'viber.debug.tp(17.5,141.2) viber.debug.face(40,141.2) viber.debug.set_camera{yaw=-90,pitch=22,distance=7} return true'
# pressionar W ~5.5 s (bridge: viber.input.key {key:"KeyW",state:"press"} → release)
./target/release/viber debug lua 'return viber.debug.player()'    # y sobe ~49.9 → ~55.2 (deck)
```

Atalho de QA para o topo (set_pos na plataforma do bosque, local −13.2/11.2/1.4):

```bash
./target/release/viber debug lua 'viber.debug.set_pos(4294966543, 16.4, 61.4, 139.9) return true'
```

### 1.3 Ativação → revelação do bioma

Em cima, junto ao teodolito (< 1.7 m da âncora), **[E] "Sondar a terra"**:

```
[toast] A Atalaia do Bosque acorda: a Nota do bioma entra na tua bússola! (+40 XP)
[log viber::nota] bioma 'dark_forest' revelado por atalaia
```

Verificação de estado:

```bash
./target/release/viber debug lua --json 'return {forest=viber.biome_revealed("dark_forest"), desert=viber.biome_revealed("desert")}'
# → {"forest": true, "desert": false}
```

Re-sondar depois: o prompt passa a "A atalaia já falou" e o [E] responde
"A Atalaia do Bosque já cartografou esta terra." (idempotente).

### 1.4 O que a revelação abre/fecha

- **[F] tranca antes**: junto a um marco de bioma NÃO revelado (ex.: poço da
  encruzilhada `forest-crossroads-well` em (28,192) antes de sondar a atalaia do
  bosque), [F] responde:
  `A terra aqui ainda não foi cartografada — procura a atalaia de Floresta Sombria.`
- **[F] abre depois**: o mesmo [F] passa a `Medido e assinado: Poço da
  Encruzilhada (faltam 2 em Floresta Sombria)`.
- **[G] (fogueira da praça)**: lista só marcos assinados E disponíveis.
- **Bússola**: losangos teal na faixa = marcos disponíveis (atalaias e revelados);
  nada de marcos escuros.
- **Minimapa**: losango teal pequeno = marco; losango maior e mais brilhante =
  atalaia. Antes da 1.ª revelação só atalaisas aparecem (screenshot 04); depois,
  os 3 marcos do bioma acendem (screenshot 06).
- **F11 (QA)**: só teleporta a marcos disponíveis.
- **Back-compat**: mundos sem NENHUMA `<Landmark tower-reveals>` nunca trancam
  (testes `test_world_without_towers_never_locks`).
- **Quests survey** (`forest_survey` etc.): objetivos `visit` por nome+raio
  (`src/quests.rs`) — nunca dependeram do [F]/Nota; continuam a funcionar
  exatamente como antes (verificado: assinatura pós-revelação e visitas contam).

### 1.5 Persistência

```
[E] na atalaia → viber.save() (tab Sistema [Q] ou REPL) → viber.load_save()
→ biome_revealed("dark_forest") == true  (save: campo revealed_biomes)
→ korok-seed no vault permanece; marcos assinados permanecem
```

## 2. Sementes de canção (koroks, 22)

Script `scripts/poi/song-seed.lua`; entidades `korok-*` em `world/koroks.xml`.
Indicador de atenção: coluna de FUMO subindo do esconderijo (tese BotW §5.1.2).

| Modo | Exemplo (coords) | Puzzle |
|------|------------------|--------|
| rock | korok-bosque-pedra-2 (44, 42.4, 210) | erguer a pedra [E] |
| odd | korok-bosque-odd-1 (118, 46.5, 228) | a pedra que canta (brilha) |
| ring | korok-bosque-anel-a-1..3 (−70, 200) | 3 cristais = 3 notas [E] |
| hide | korok-bosque-hide-1 (−110, 45.9, 150) | descoberta escondida |

Recompensa: item **korok-seed** (nunca ouro) + contador na bolsa do HUD
(linha SEMENTES, bind `korok-seed`). **A cada 5 sementes: +1 ponto de talento**
(`viber.add_skill_points` → badge "+" do nível acende). Persistência:
`viber.game()` (world_kv) — coletada numa sessão não volta noutra (após save/load).

QA (exemplo, korok odd):

```bash
./target/release/viber debug lua 'viber.debug.tp(117.3,228.1) return true'
# [E] → toast "A pedra que canta guarda uma semente! (N na bolsa)"
./target/release/viber debug lua --json 'return viber.debug.vault().items["korok-seed"]'
```

Prémio dos 5 (verificado ao vivo):

```
[toast] Escondida onde ninguém olha: uma semente de canção! — semente 5/20: +1 PONTO DE TALENTO!
```

## 3. Baús escondidos (11)

Todos `chest.lua` com `prop-*`; ficheiro `world/koroks.xml` (secção BAÚS).

| Baú | Onde (x, y, z) | Loot | Princípio |
|-----|----------------|------|-----------|
| bau-atalaia-vale | (75.9, 52.2, −7.9) | 120 ouro, 60 xp, 2 poções | topo da atalaia (climb reward) |
| bau-atalaia-bosque | (16.0, 61.1, 140.3) | 90 ouro, 55 xp, 2 antídotos | topo da atalaia |
| bau-oasis | (137, 47.5, 86) | 110 ouro, 50 xp, poção | fundo de água rasa (oásis) |
| bau-ermo-ruinas | (304, 47.4, 128) | 95 ouro, 50 xp, 2 bombas | dentro das ruínas |
| bau-pantano-cais | (−266, 29.8, −12) | 85 ouro, 45 xp, 3 antídotos | atrás do cais da palafita |
| bau-ponte-norte | (8, 38.9, 202) | 75 ouro, 45 xp, poção | à sombra da ponte norte |
| bau-cidadela | (−870, 188.6, −290) | 200 ouro, 90 xp, 2 bombas | Cidadela Caída (grande desvio) |
| bau-mina-veio | (−63, 95.8, −213) | 130 ouro, 70 xp, 2 poções | boca da mina (gruta) |
| bau-bosque-encantado | (200, 70.1, 1400) | 160 ouro, 80 xp, 3 poções | fundo do bosque encantado |
| bau-vael | (700, 182.6, 210) | 150 ouro, 75 xp, 2 antídotos | mesa do Alto de Vael |
| bau-porta-ermo | (505, 81.5, −57) | 140 ouro, 70 xp, bomba | escondido na boca do Ermo (gruta) |

```bash
./target/release/viber debug lua 'viber.debug.tp(302.8,127.2) return true'   # ruínas
# [E] → "O baú range e cede... +50 XP, +95 ouro, +2 bombas"
```

## 4. Legibilidade (triangle rule / gravity)

- **Maior triângulo**: atalaisas 13.6 m com rampa diagonal e farol emissivo —
  silhueta única por bioma (cor da pedra/madeira/cristal por bioma), visíveis na
  bússola/minimapa SEMPRE (estrutura da exploração).
- **Retângulos escondem**: koroks `hide` atrás de relevos (outpost hill), baús
  sob pontes/cais/bocas de gruta (17-poi-escondido-boca-ermo.png).
- **Fumo = korok** (tese: "indicated by a smoke plume going up"): screenshots
  08/09 mostram as colunas de fumo na floresta.
- **Gravity no caminho**: as atalaisas pousam nas entradas dos biomas, à vista
  das estradas (01: vale da porta leste; 19: ermo da estrada do deserto;
  18: picos no corredor sul).

## 5. Perf (gate ≤ 5 % frame time)

A/B **interleaved no mesmo processo** (janela visível, relógio preso 600,
herói na vila): medir `viber debug prof --samples 10 --json` → depois
`viber.debug.despawn` dos 5 grupos `tower.*` ao vivo → medir de novo:

```
A (com conteúdo): 48.18 / 48.10 / 48.20 ms
B (mesmo processo, atalaisas removidas): 48.73 / 48.72 / 51.02 ms
```

Delta dentro do ruído da sessão (o braço B chegou a medir MAIS) — custo do
conteúdo não-atribuível, ≤ ~2 % no pior caso, bem abaixo do gate de 5 %.
Entre boots o ambiente variou 45–48 ms com e sem conteúdo (confounds de
streaming/compositor) — por isso o A/B de um processo só é o que vale.

## 6. Gates

```bash
cd Viber
cargo test -p viber                      # 1685 passed, 0 failed (r2)
cargo clippy -p viber -- -D warnings     # limpo nos ficheiros da peça
                                         # (na sessão r2, WIP paralelo de
                                         # outro agente em src/racing/* +
                                         # src/recipes/spawn.rs deixava
                                         # warnings alheios no tree)
cargo run -p viber -- analyze examples/simple-rpg/world.xml   # OK
cargo run -p viber -- analyze examples/simple-rpg/qa-map.xml  # OK (mundo QA r2)
```

---

# Ronda 2 — Mapa regional [M] + fumo korok ×3

## 7. Mapa regional [M] (`src/hud/map.rs`, preset RPG) — RESULTADOS DA SESSÃO

Mundo QA dedicado (save próprio, não toca no save do `world.xml`):

```bash
cd Viber
viber session up --world examples/simple-rpg/qa-map.xml   # binário INSTALADO!
viber session claim --owner p2-r2
./target/release/viber debug probe       # descobre a porta pela sessão
```

> ⚠ `session up` corre o binário INSTALADO com `--no-cargo` — depois de
> editar a engine: `cargo build -p viber --release && cp target/release/viber
> ~/.local/bin/viber` ANTES do `session up` (sintoma de binário velho:
> `viber.reveal_biome` nil na REPL).

Resultados verificados ao vivo (screenshots em `screenshots/`):

| Passo | Resultado | Evidência |
|-------|-----------|-----------|
| Save fresco → [M] | vale aceso, 4 biomas sob névoa, pins de 5 atalaisas + praça + herói, rodapé `0/17 · 0/4 · … [??]` | `r2-mapa-01-vila-so.png` |
| `viber.reveal_biome("dark_forest")` (o MESMO `ScriptCommand` da torre) | cunha sul acende EM VIVO (~0.5 s), marcos do bioma entram como pins, rodapé 1/4 `[OK]` | `r2-mapa-02-bosque-revelado.png` |
| Restantes 3 biomas | mapa-múndi inteiro: topografia + água + estradas + neve | `r2-mapa-03-todos-revelados.png` |
| Cursor sobre pin | rótulo "Atalaia do Ermo (por assinar)" | `r2-mapa-04-hover-pin.png` |
| `viber.save()` → `viber.load_save()` → [M] | 4/4 `[OK]` mantidos (estado no `revealed_biomes` do save; zero estado novo no mapa) | `r2-mapa-05-persistencia.png` (ver artefacto cosmético nas notas) |
| [M]/Esc/[L] | fecham (Esc verificado via BRP: `Visibility: Hidden`) | — |
| Perf da abertura | frame 16.67 ms (fechado) → **16.66 ms** (aberto) — Δ0.01 ms, gate ≤50 ms com folga | `viber debug prof --samples 4` |

Fechar: **[M]/Esc/[L]** (`./target/release/viber debug key m` outra vez).

### 7.2 Revelação por bioma (o GAP da ronda 1)

Revelar com o fluxo REAL da torre ([E] no teodolito da atalaia do bosque) ou
pela mesma rota de runtime via REPL (`viber.reveal_biome` é o mesmo
`ScriptCommand::RevealBiome` que o script da torre enfileira):

```bash
# via torre (topo do bosque; ver §1.2/1.3) ou:
./target/release/viber debug lua 'viber.reveal_biome("dark_forest") return true'
./target/release/viber debug key m
./target/release/viber debug screenshot -o screenshots/r2-mapa-02-bosque-revelado.png
```

### 7.3 Pins e hover

- **Marcos assinados** → pin a OURO cheio; por assinar → contorno teal
  (assinável via [F] no mundo para ver a troca).
- **Faróis de missão** ([`QuestGuide`] — NPC por aceitar/entregar) → pontos
  vermelhos.
- **Hover**: mover o cursor sobre um pin (`viber debug move X Y`) → rótulo
  com o nome ("Atalaia do Bosque", "Poço da Encruzilhada (por assinar)").
- **Guard back-compat**: num mundo SEM atalaias (ex. `qa-cenarios.xml`) o
  mapa não gera névoa nenhuma — tudo visível (testes
  `test_region_hidden_gating` + `test_world_without_towers_never_locks`).

### 7.4 Persistência (reusa `RevealedBiomes` — zero estado novo)

```bash
./target/release/viber debug lua 'viber.reveal_biome("desert") return true'
./target/release/viber debug lua 'viber.save() return true'      # grava
./target/release/viber debug lua 'viber.load_save() return true' # carrega
./target/release/viber debug key m
# → deserto continua revelado no mapa (o estado vive no save, campo
#   revealed_biomes — ronda 1; o mapa só o deriva)
```

### 7.5 Perf da abertura (gate: hitch ≤ 50 ms)

A cozinha cartográfica (1024², 2 passagens de 8 frames) corre EM FUNDO desde
o arranque — abrir [M] é um flip de `Visibility` + posicionamento de ~25 nós.
Medido ao vivo (release, `viber debug prof --samples 4`):

```
fechado:  frame média 16.67 ms
aberto:   frame média 16.66 ms     ← primeiro frame pós-[M]
```

## 8. Fumo korok ×3 (legível a ≥90 m) — MEDIDO

Presets em `world/koroks.xml` (24 emissores idênticos):

| Campo | r1 | r2 (×3 na coluna) |
|-------|----|----|
| emission-rate | 3 | **9** |
| start-size | 0.28–0.62 | **0.84–1.86** |
| start-life | 2.6–4.4 | **5.5–8.0** (¹) |
| start-speed | 0.25–0.55 | 0.2–0.45 |

(¹) vida NÃO foi ×3: o preset `smoke` tem flutuabilidade +0.6 m/s² e a
coluna cresce `v0·t + 0,3·t²` — vida ×3 dava um pilar de ~50 m. 5.5–8.0 s
mantém a coluna a **10–23 m** com puffs que crescem ×2.4 até ~4.5 m no topo.

Verificação à distância (korok `korok-ermo-pedra-1` em (128, 84)):

```bash
./target/release/viber debug --world qa-map lua 'viber.debug.tp(215,55) viber.debug.face(128,84) return true'
./target/release/viber debug --world qa-map lua 'local x,y,z=viber.debug.pos(4294965849) local p=viber.debug.player() return math.sqrt((x-p.x)^2+(z-p.z)^2)'
# → 91.7 m
./target/release/viber debug --world qa-map screenshot -o screenshots/r2-fumo-92m.png
```

- **91.7 m**: coluna nítida contra a encosta — `r2-fumo-92m.png` (+ zoom
  `r2-fumo-92m-zoom.png`). **O barro "≥90 m na prática": cumprido.**
- **~147 m**: ainda distinguishável (fio branco, `r2-fumo-147m.png`) —
  o limite honesto de leitura fica acima do pedido.
- Nota de terreno: a 94 m do lado norte a coluna é ocultada por uma crista
  (oclusão de mundo, não do emissor) — o tiro válido é com linha de visão
  (lado este do Ermo).
