# CONTEXT.md — src/recipes

Camada central do pipeline (Fase 0 ✅): converte a árvore XML de `src/xml` na
IR de entidades e spawna-a em Bevy. Tudo o que existe num mundo passa por aqui
— primitivas, luzes, câmaras, `GltfScene`, spawners, terreno/água/estradas,
`PlayerGLTF`, HUD e UI declarativa.

## Design

- **Tolerante por contrato:** mundos escritos contra um vocabulário maior (o
  do VibeGame) têm de continuar a correr — desconhecidos degradam a warning /
  skip no-op, e o `analyze` imprime a cobertura. As tags `EngineConfig`
  ficam em `EngineConfigs` (attrs crus) e cada plugin lê a sua.
- A IR desacopla o parse do spawn: `spawn.rs` consome specs já validados
  (terreno, água, estradas, física) em vez de re-parsing de strings.
- Tags de HUD (`HealthBar`, `Minimap`, …) entram como IR genérica
  `HudElement` (tag + attrs crus) — a UI real vive em `src/hud/` e `src/ui/`.
