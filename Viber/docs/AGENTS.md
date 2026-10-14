# AGENTS.md — docs

Documentação canónica do Viber para autores de mundo e scripts.

| Doc | Conteúdo | Fonte da verdade |
|-----|----------|------------------|
| `LUA_API.md` | referência completa de `viber.*`, `viber.debug.*` e `viber.ui.*`: runtime de scripts (top-level 1× por path, `viber.state()`, hooks `on_update(dt)`/`on_player_attack`, LOD de IA, hot-reload ao gravar), percepção, movimento, combate, quests, vault, UI | `src/luau/` (`host.rs`: `install_viber_api`) + `src/ui/script.rs` |
| `UI.md` | guia da UI declarativa: elementos, `<UiStyle>`/CSS, bindings, `UiList`, modais, exemplos do simple-rpg | `src/ui/` |

## Regras

- **Atualizar o doc no mesmo cambio** que alterar a API ou os attrs da UI —
  doc dessincronizado é bug (regra de casa do projeto).
- Manter o estilo do repo: pt-PT, tabelas "onde está o quê", exemplos vivos
  apontando para `examples/simple-rpg/`.
- Ao adicionar API nova, incluir: assinatura, semântica de enfileiramento
  (setters aplicam pós-frame) e um exemplo mínimo.

## Verificar

Os exemplos de código dos docs devem compilar semanticamente contra
`docs/LUA_API.md` ↔ `src/luau/`; validação rápida de mundo:
`cargo run -- analyze examples/simple-rpg/world.static.xml`.
