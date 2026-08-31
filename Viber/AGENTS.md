# AGENTS.md — Viber

Engine de jogo NATIVA em Rust/Bevy 0.19 que corre mundos declarativos XML do
AiGameKit — sem browser, sem three.js. Estado: **Fase 0** (parse → IR → spawn).
Nomenclatura segue **Bevy** (`translation`, `euler`, `half-size`, `base-color`),
não Unity/three.js.

## WHERE TO LOOK

| Tarefa | Ficheiro(s) | Notas |
|--------|-------------|-------|
| CLI (`run` / `analyze`) | `src/main.rs` | `analyze` é headless, exit 1 em erro |
| XML: parse, includes, valores | `src/xml/` | `include.rs` (expansão), `values.rs` (parsers tolerantes) |
| IR de entidades + spawn Bevy | `src/recipes/` | `mod.rs` (IR), `spawn.rs`, `transform.rs` (euler→quat) |

## COMANDOS

```bash
cd Viber && cargo run -- analyze <world.xml>   # valida headless (exit 1 em erro)
cd Viber && cargo run -- run <world.xml>       # janela Bevy
cd Viber && cargo test                          # testes headless
make test-viber                                 # atalho monorepo
```

## CONTRATO XML (Fase 0)

Raiz: `<world>` (ou `<scene>`), attr `clear-color` (`#rgb`/`#rrggbb`/`0x…`/nome).

| Tag | Atributos próprios |
|-----|--------------------|
| `Entity` / `Group` | contentor transform-only (hierarquia via filhos) |
| `Cuboid` | `half-size` (vec3) |
| `Sphere` | `radius` |
| `Cylinder` | `radius`, `half-height` |
| `Plane` | `half-size` (vec2, plano XZ) |
| `Capsule` | `radius`, `half-height` |
| `PointLight` | `color`, `intensity` (default 1200 lm), `radius`, `shadows` |
| `AmbientLight` | `color`, `brightness` — aplicado como recurso, não entidade |
| `OrbitCamera` | `target` (nome de entidade), `distance`, `height`, `pitch` (graus; quando presente sobrepõe `height` via `height = distance·tan(pitch)`) |

Primitivas aceitam material: `base-color`, `metallic`, `roughness`.
Atributos universais: `name`, `tag`, `script`, `translation`, `euler` (graus XYZ),
`rotation` (quat `x y z w`, ganha sobre `euler`), `scale`.
Sem câmara no mundo → auto-orbit lenta na origem.

**Regras:**
- Tags case-insensitive; vetores `"x y z"` com broadcast de 1 valor; **2 valores = erro**.
- Bools tolerantes: bare (`<PointLight shadows>`) e `true/1/yes/on` / `false/0/no/off`.
- `<Include src>`: profundidade máx. 8, ciclos fail-fast; caminhos com `/` resolvem
  contra o dir do ficheiro raiz, relativos contra o dir do ficheiro que inclui;
  fragmentos com raiz `<world>`/`<scene>` contribuem os filhos.
- Atributos desconhecidos = **warning** (impresso no `analyze`); tags desconhecidas = **erro**.
- `world`/`scene` aninhados e `<Include>` não-expandido = erro.
- Números não finitos (`NaN`/`inf`) são rejeitados; includes podem sair da árvore
  de pastas (`..`, symlinks) — CLI local, sem sandbox (decisão consciente).

## ROADMAP

- **Fase 0 (✅):** parse/validate, includes, primitivas, luzes, `OrbitCamera`, `run`/`analyze`.
- **Fase 1:** glTF (`GltfScene`) + terreno + player/movimento.
- **Fase 2:** Luau/mlua (hooks `on_add`/`on_update`/`on_remove` + hot-reload).
- **Fase 3:** física avian (`RigidBody`/`Collider`) + simple-rpg atualizado em `Viber/examples/simple-rpg/`.

**Nota:** `script="ficheiro.lua"` já é aceite no XML e registado na IR, mas ainda
**não executa** (chega na Fase 2).
