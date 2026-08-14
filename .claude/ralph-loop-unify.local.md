---
active: true
iteration: 9
session_id: sess_7254b35d-22f9-40c5-99cc-1c28a2a611ef
max_iterations: 0
completion_promise: null
started_at: "2026-08-08T22:10:00-03:00"
loop: unify-rpg-racer
---

Loop de unificação simple-rpg ↔ simple-racer: compartilhar o máximo de código,
com sistemas unificados suficientes para qualquer situação. Iterações
verificadas com `bun run check` + `bun test tests/unit` (10169 verdes).

Iterações 1-8 (feitas): BGM driver, kit shared, pause unificado, persistência
(gzip), rename hero→player, profiler/debug/spawner/terrain-gate, floating
text/toasts/restart/checkpoint-arrow, loading screen, creature data-driven.

Iteração 9 (feita) — dedup interno restante do RPG:
- game/player-query.ts: findPlayer unificado (query [PlayerController] + cache
  re-validado) — substituiu as cópias em chest, mystic, merchant e
  building-portal (corpos idênticos) + imports órfãos limpos.
- game/hud-slot.ts: createHudSlot (slot vidro + keyBadge + ícone img/emoji) —
  o buildBar (abilities) e buildHotbar (consumables) compartilhavam ~70% do
  código; cada um mantém só os extras (count badge / cooldown cover+secs).
- Pause guard e mystic wrappers: já unificados/finos (nada a fazer).
- Totais 8+9 no RPG: −552 linhas líquidas (846 removidas / 294 adicionadas).

Território do outro agente preservado: track-props.ts, track-spawn.ts,
terrain/systems.ts, racing/, spawner.

Iteração 10 (próxima): reuso cruzado restante, features ainda separadas dos 2
jogos, ou revisão geral do que sobrou.



