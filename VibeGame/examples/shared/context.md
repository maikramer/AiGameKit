# examples/shared — kit compartilhado dos exemplos

<!-- LLM:OVERVIEW -->

Glue DOM/engine helpers que os exemplos shipped (simple-rpg, simple-racer)
reutilizam. São helpers genéricos demais para viverem no motor (tocam DOM de
página / convenções de exemplo) e idênticos o suficiente entre os jogos para
não valer a pena duplicar.

## Regras

- Importam APENAS o barrel público `vibegame` — o gate de CI
  (tests/unit/api-surface.test.ts) proíbe deep-imports de `src/`.
- Um módulo por responsabilidade (ui, physics, resources, hmr).
- Sem estado de jogo específico: o acesso ao estado/entidade é injetado
  (ex.: `createResourceAdapter(kind, { state, hero })`).

## Módulos

| Módulo      | Export                                            | Substitui                          |
| ----------- | ------------------------------------------------- | ---------------------------------- |
| `ui.ts`     | `showToast(message, opts?)`                       | toasts duplicados do RPG (mystic/chest/portal) |
| `physics.ts`| `teleportEntity(state, eid, x, y, z)`             | pose-write manual em 3 lugares do RPG |
| `resources.ts` | `createResourceAdapter(kind, access)`          | adapters gold/wood/stone copy-paste |
| `hmr.ts`    | `setupHmrGuard(cleanup)`                          | bloco HMR decline/dispose do RPG (o racer agora também tem) |

## Uso

Imports relativos (profundidade do chamador!):

- `src/main.ts` → `../../shared/src/...`
- `src/game/*.ts` / `src/scripts/*.ts` → `../../../shared/src/...`

## Verificação

`bun run check` (type-checks os exemplos, incluindo os módulos compartilhados)
e `bun test tests/unit` (gate de deep-import cobre `examples/` inteiro).
