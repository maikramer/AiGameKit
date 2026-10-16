# Viber simple-racer — port do VibeGame (2026-10-15/16)

Port do `VibeGame/examples/simple-racer` (Sunset Ridge, kart racer) para a
engine Viber, no modelo do simple-rpg (projeto multi-cena, autoloads, mundo
estático bakado). Commits: roads 3D `50b2bd57`, racing `907d0527`, exemplo
`fc7a0985`, HUD `d22637b4`.

## O que ficou pronto (v1 "core de corrida")

- **Traçado fiel**: 39 nós de `src/track.ts` (~5,1 km medidos no resample),
  canais autorados `y`/`widths` por nó, `viaduct-clearance="6"` — o vale sob
  o viaduto não é carvado (a floresta/lago ficam por baixo) e a descida
  cruza por baixo do tabuleiro. Largura uniforme por nó (16–22 m); sem
  peralte/kerbs (v2).
- **Kart arcade cinemático** (`Viber/src/racing/vehicle.rs`): heading+speed+slip,
  aceleração com compensação de resistências (teto exato = `top-speed`),
  drift com carga (mini-turbo 1,0 s / super 2,1 s), nitro, arranque foguete
  (hold W, rev ≥ 0,72; overrev a 100% = wheelspin), paredes lógicas do
  traçado, contactos kart-vs-kart, lançamento em cristas, respawn [R].
  Sem rigidbody — o modelo do VibeGame ("deliberately no rigidbody") mantido.
- **Diretor de corrida** (`race.rs`): grelha 2×N, countdown 3 s, voltas por
  progresso ACUMULADO wrap-aware (anti-cheat; a pista cruza-se a si própria
  sob o viaduto), posições ao vivo, cronómetros, wrong-way, resultados.
- **4 rivais AI** (`ai.rs`): pure-pursuit + velocidade-alvo da curvatura à
  frente + rubber-band + recuperação de atasco — escrevem os MESMOS inputs
  do jogador (modelo físico único). Skills do roster (vega 0,94 … rook 0,74).
- **Som de motor** (`audio.rs`): loop kira com playback-rate por velocidade
  (0,55→1,7) + skid em drift; clipes novos `Crash/Nitro/Skid` no SfxClip.
- **HUD declarativo** (`examples/simple-racer/world/hud.xml`): volta/posição/
  cronómetros, speedómetro km/h, nitro, banners — tudo via `viber.race.*`/
  `viber.vehicle.*` (snapshot `RaceSnapshot` publicado no `ScriptCtx` do
  Luau a cada frame, sem tocar no teto de 16 params do `luau_update`).
- FOV sobe com velocidade (+8°) e turbo/nitro (+5°), por cima do base+kick
  do `fov_kick_system`.

## Gotchas descobertos no port

1. **`<Scatter>` é formato de SAÍDA do bake** — autoria legada usa
   `<StaticSpawner>`; com Scatter o bake reportava "0 groups / 0 instances"
   em silêncio (o parse devolve `Ok(None)`).
2. **Re-bake depois de um bake**: os includes ficam `static="true"` e o
   bake descarta alterações neles ("já é estático (partilhado)"). Para
   re-autorar: repor os includes em forma legada + apagar `world.data/` +
   UM bake limpo.
3. **Paredes lógicas fora da pista**: o clamp via `road_surface` só valia
   NA ribbon (None fora) — uma saída grande ficava livre. Fix: projetar
   SEMPRE no traçado (`TrackInfo.road`) com faixa de 30 m (`WALL_RANGE`);
   longe da pista o mundo continua explorável.
4. **`total_positions`** do snapshot Lua contava só karts do jogador
   (posição "5/1" no HUD) — contagem numa query separada de `RaceProgress`.
5. **`max_curvature_ahead`**: o início da janela tinha `min(n-1)` em vez de
   `% n` — janelas junto ao fim da volta colapsavam na última estação.
6. **Heightmap 256² a 1400 m** (5,5 m/texel): o carve aguenta (perfil
   suavizado), mas a fidelidade do declive é grossa fora do corredor.
7. **O lago da bacia**: resolvido com `water-level` negativo (bacia do
   heightmap ~0 m) — cosmetico, ler water-offset se importar.
8. **QA com teclas sintéticas**: o bridge só imprime o 1.º valor de retorno
   Lua; para segurar teclas usar `viber.input.key` com `state: "press"` /
   `"release"` por HTTP (BRP jsonrpc 2.0), não `viber debug key` (tap).

## Pipeline de construção (replicável)

```bash
cd Viber/examples/simple-racer
# 1. autorar (world.xml legado + includes; ver git history da Fase 3)
../../target/release/viber analyze world.xml          # gate 1
../../target/release/viber bake world.xml             # carve → world.data/
../../target/release/viber scene lint --strict        # gate 2
../../target/release/viber run world.xml --bridge 15714 --no-cargo
../../target/release/viber debug lua 'return viber.race.state()' --port 15714
```

## Ficou para v2

Itens (baús/roleta, fireball/óleo/escudo), atmosferas (wet/night/storm),
ghost do PB + Time Trial, acrobacias aéreas (barrel roll/flip), minimapa
`mode="track"` (frame do circuito inteiro com dots), peralte/kerbs,
gamepad analógico, "Weekend" campeonato multi-cena.
