# CONTEXT.md — src/hud

Camada de apresentação HUD herdada do simple-rpg original (portado com
presentation "AAA": font Cinzel, painéis com gradiente e sombra, ícones
vetoriais autorais, seta real de minimapa com dots de quest numerados,
compasso tickado com distâncias por setor).

## Papel pós-migração

Com a chegada da UI declarativa (`src/ui/`), esta pasta deixou de conter
painéis/menus e ficou com os widgets que leem estado do mundo e redesenham a
cada frame — incl. o profiler em janela (F3/P, snapshot também exposto pelo
bridge como `viber.profiler`). Interações com outros sistemas: `travel.rs`
(marcos/waypoint alimentam minimapa e compasso), `quests.rs` (dots do
tracker), `combat.rs`/`vitals.rs` (barras e alvo).

Nametags (pílulas flutuantes nome+distância) foram removidas a pedido do
autor em 2026-09-06 — poluíam a vista e ficavam fora de layout.
