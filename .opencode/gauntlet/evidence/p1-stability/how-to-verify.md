# p1-stability — como reproduzir o walkthrough limpo (ronda 2)

Máquina de referência: RTX 4050 Laptop (6 GB), Ubuntu + Wayland, monorepo em
`/media/maikeu/b1e73891-ddde-49a0-9382-903accb68b49/GitClones/AiGameKit`. Tudo em
`main`, sem branches. Duração total ≈ 15 min depois do build.

## 0. Gates (antes de abrir o jogo)

```bash
cd Viber
cargo test -p viber                    # 1560 lib + integração, 0 failed
cargo clippy -p viber -- -D warnings   # exit 0
cargo run -q -p viber -- analyze examples/simple-rpg/world.xml   # OK, exit 0
```

(NOTA: `cargo clippy -p viber --all-targets -- -D warnings` tem ~71 erros
PRÉ-EXISTENTES em alvos de teste no main limpo — o gate da peça é o comando
sem `--all-targets`, que está verde.)

## 1. Arrancar o jogo com bridge

`VIBER_DEBUG_KEYS=1` liga os teleportes de QA F6–F11 (gate de env documentado).

```bash
cd Viber
VIBER_DEBUG_KEYS=1 scripts/instance-lock.sh exec -- \
  cargo run -q -p viber --release -- run examples/simple-rpg/world.xml --bridge
```

- Esperar a linha do bridge; o prólogo cinematográfico de 29 s toca de seguida.
- O `viber debug` descobre a porta sozinho pela sessão (`engine.json`).

Cliente (outra shell, a partir de `Viber/` ou da raiz): `~/.local/bin/viber debug …`.

## 2. Smoke-test do arranque

```bash
viber debug probe          # → {"pong":true, world: ".../simple-rpg/world.xml"}
viber debug lua 'local p = viber.debug.player() return {x=p.x, y=p.y, hp=p.hp}'
viber debug logs --limit 1000 | grep -cE "ERROR|panicked"
# → 6 na ronda 2: 2× cursor Wayland (1 por clique sintético) + 4×
#   landmass_rerecast (vendorizado, cresce com tiles de montanha visitados).
#   Zero panics / WGSL / bind-group.
```

## 3. A/B do golden hour com `debug.set_clock` (o fix desta peça)

Congelar o relógio e capturar o trio no MESMO sítio:

```bash
viber debug lua 'viber.debug.set_time_scale(0) viber.debug.tp(0,-30) return true'
viber debug lua 'viber.debug.set_clock(721) return true';  sleep 3   # 12:01
viber debug screenshot -o 17-dia-limpo.png
viber debug lua 'viber.debug.set_clock(1156) return true'; sleep 3   # 19:16
viber debug screenshot -o 18-golden-hour-1916.png
viber debug lua 'viber.debug.set_clock(346) return true';  sleep 3   # 05:46
viber debug screenshot -o 18b-golden-hour-0546.png
```

Esperado (pós-fix `98abe560`): 12:01 dia neutro; 19:16 entardecer DOURADO
(céu quente, tochas, albedo preservado); 05:46 amanhecer frio legível —
NENHUM frame vermelho/magenta (o bug da r1: `18-golden-hour-bug.png`).

Causa-raiz: `ColorGrading::global.temperature` do Bevy é um desvio de
cromaticidade CIE xy (não "stops") — `golden×0.35 + low_sun×0.10` mandava o
verde do frame inteiro para negativo. Fix: braços quentes reescalados
(`GOLDEN_GRADE_TEMPERATURE` 0.035 + `GOLDEN_GRADE_WARMTH` 0.010). Teste de
regressão: `test_golden_grade_warm_side_stays_in_gamut` (fecha a matriz
`balance` real do Bevy e varre o dia inteiro).

## 4. Respawn com streaming prioritário (o 2.º fix)

```bash
viber debug lua 'viber.debug.tp(320,320) return true'; sleep 6   # deserto assente
viber debug lua 'viber.debug.damage(9999) return true'
sleep 2.6   # RESPAWN_DELAY = 2 s
viber debug screenshot -o respawn-0.5s.png   # mundo JÁ visível (boost 2.5 s)
```

Esperado: o frame a +0.5 s do respawn já tem terreno/props/céu (na r1 era um
vazio de névoa). Contraste QA-teleport (SEM boost, por desenho):
`viber.debug.tp(-12,-272)` vindo de longe → névoa a 1 s, mundo completo a ~3 s
(`13c/13d-qa-tp-stream-*.png`).

## 5. Walkthrough (secções do log-after/timeline)

Todas as posições são mundo; `viber.debug.tp(x, z)` senta o Y no terreno.

| # | Secção | Comandos + leitura |
|---|--------|--------------------|
| 1 | Praça | `tp(2,-8)` → screenshot `01-arranque.png` |
| 2 | Combate [J] | `tp(0,-120)` → `find_all("goblin")` → `tp(x-3.2,z)` ao 1.º → `key j` ×8 → `player().xp` sobe (+15/kill) |
| 3 | Coleta [J] | `tp(47.9,74.3)` + `face(48.8,75.0)` (carvalho `inst-grp-1-0000` a 1.3 m — o alcance de interação é 1.75 m!) → `key j` ×6 → `vault().wood` +3 |
| 4 | Loja | `tp(15.4,-13.5)` → `vault()` ANTES (ouro 32) → **[E]** (abre o DIÁRIO na tab LOJA) → `click 640 336` ("COMPRAR POÇÃO 25") → `vault()` DEPOIS (ouro 7, poções +1) |
| 5 | Modal [Q] + talento | `xp(500)` → [Q] → `key 3` (TALENTOS) → `click 640 225` (VITALIDADE I) → `max_hp` 100→120 |
| 6 | Fogueira + viagem [G] | `tp(0,-30)` → banner → `key g` → Esc |
| 7 | Marco [F] + F11 | `key f11` → `key f` → toast "Medido e assinado" (Picos, `10,-154`) |
| 8 | Biomas + BGM | `tp(320,320)` Ermo, `(-320,320)` floresta, `(-320,-320)` pântano, `(0,-300)` Picos → esperar 5 s (fade) → `viber debug prof --tab audio --json` → a layer ATIVA da zona lê ~base, as outras 0 (`bgm-*.json`) |
| 9 | Interior | `tp(7.4,21.4)` → **[E]** no portal → ~5 s → `viber.scene_name()` = `interiors/chapel`; sair com [W] até encostar à porta (auto-radius) → `main` na pose exacta. NOTA: BGM `dungeon` NÃO toca em interiores (layers morrem com a cena — P6) |
| 10 | Boss sul | `tp(-12,-272)` + 6 s → `distance(player, find("boss"))` ≈ 18 m, mundo renderizado |
| 11 | Morte + respawn | ver §4 |
| 12 | Abilities | [C] dash, [R] radial, [B] bomba, [L] guard |
| 13 | QA teleports | `key f6/f7/f8/f9/f10/f11` — F10 dá recursos (não move) |
| 14 | Profiler | `key f3` (overlay) ou `P` (painel declarativo) |
| 15 | Save/load | `viber.ui.action("save","")` (DOIS argumentos) → `~/.local/share/viber/simple-rpg.save.json` muda de mtime |
| 16 | BGM de combate | golpes [J] com aggro → tab áudio mostra **battle 0.22** (hold 8 s) |

## 6. Log final

```bash
viber debug logs --limit 1000 > .opencode/gauntlet/evidence/p1-stability/log-after.txt
```

Ruído conhecido aceitável (fixed-list §9): cursor Wayland por clique
sintético, `landmass_rerecast` por tile de montanha, 1× prepass-normals por
boot, 1× `sctk_adwaita`, 1× `viber.gesture … sem CharacterAnimator` por fala.
**Zero** panic / WGSL / bind-group.

## 7. Encerrar

Matar o processo do jogo que TU arrancaste
(`pkill -f "target/release/viber run"`; o lock do `instance-lock exec`
liberta-se no EXIT). Nunca matar engines de outros agentes.

## Notas de QA (validado na ronda 2)

- **`viber.debug.find(nome)` devolve um ID NUMÉRICO** (não tabela) — posição
  com `local x, y, z = viber.debug.pos(id)` (retorna TRÊS valores).
- **Alcance de interação efetivo = 1.75 m** (`BASE_RANGE_M` × escala 0.5) —
  coleta/loja/diálogo só conectam perto; a 2.75 m o [J] não apanha o prop.
- **set_clock + leitura no MESMO call**: as leituras da REPL refletem o INÍCIO
  da chamada — verificar com um segundo call.
- **F6–F11** exigem `VIBER_DEBUG_KEYS=1` no ambiente da engine.
- **Timing**: o walkthrough cabe num dia de jogo (08:00, 1.2 min/s); com o fix
  do golden hour TODAS as horas renderizam legíveis — já não é preciso fugir
  da janela 05:30–06:00/19:00–19:40.
