# lua-demo — um jogo que não é um RPG

Vitrina do desacoplamento da engine: `config.yaml` declara **`gameplay: none`**,
o que boota o Viber **sem os plugins de domínio** (combate [J], skills
[C/R/B/L], economia/hotbar, quests, travel, save, colheita). O que fica de pé
é a engine — terreno volumétrico, céu, player, câmara, UI declarativa, física e
a API Luau genérica — e o jogo inteiro são ~80 linhas em `scripts/`:

| Ficheiro | Papel |
|---|---|
| `scripts/game.lua` | controller: semeia 6 cristais via `viber.spawn_prototype` e guarda o placar em `viber.game()` |
| `scripts/crystal.lua` | colado ao `<Prototype id="crystal">`: `[E]` recolhe (estado + som + despawn) e o cristal flutua no Y |
| `scripts/ui/hud.lua` | HUD por Lua puro (`viber.ui.set_text("score", …)`) — sem binds engine |

Correr e validar:

```bash
cargo run -- analyze worlds/lua-demo/world.xml   # validação headless
cargo run -- run worlds/lua-demo/world.xml       # joga
```

API usada: `spawn_prototype`, `game()`, `set_interaction`/`interacted`,
`position`/`set_position`, `ui.set_text`, `toast`, `sound`, `after`, `events`.
Notas: o `set_position` do bob é LEGADO deliberado (o cristal não tem collider
nem locomoção de IA — o equivalente moderno é `move_towards`, que assenta no
terreno e brigaria com a flutuação); o progresso PERSISTE entre sessões — o
`game.lua` pede `viber.load_save()` no arranque e cada cristal pede `viber.save()`
(`world_kv` no save do mundo, em qualquer preset).
