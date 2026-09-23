# CONTEXT.md — examples/simple-rpg

O mundo que valida a engine de ponta a ponta: port do exemplo
`VibeGame/examples/simple-rpg` (convertido por `Viber/scripts/migrate_from_vibegame.py`).

## Estado

**Jogável de ponta a ponta** — combate melee + skills + talentos, 21 quests
com diálogo, economia (vault, colheita, loja, hotbar), travel (A Nota, 12
marcos, viagem rápida), save/load, mundo vivo (céu procedural, dia/noite,
clima, BGM por zona, partículas) e física (knockback, destrutíveis).
Os ~38 scripts Luau correm com "LOD de IA" (fora do `activation-radius`, 45 m
por omissão, o `on_update` nem corre).

O `analyze` imprime o relatório de cobertura de tags — é o roteiro do que
falta à engine (hoje: nada — as 5 tags `EngineConfig` já têm consumidor). Qualquer nova
feature da engine deve manter este mundo a 100 % de cobertura.

Detalhes completos (estrutura, assets, comandos): [`README.md`](README.md).
