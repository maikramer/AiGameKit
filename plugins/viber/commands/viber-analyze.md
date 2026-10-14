---
description: Validação headless CI-ready de um mundo Viber (analyze + interpretação do relatório)
argument-hint: [world.xml] [--strict]
allowed-tools: Bash
---

# Analisar um mundo Viber (headless)

Mundo alvo: `$ARGUMENTS` (se vazio: `viber analyze` procura `world.static.xml` →
`world.xml` → `worlds/*.xml` no cwd). `analyze` NUNCA delega a cargo e é exit 1
em erro — CI-ready. Corre da pasta do mundo ou passa o caminho.

Executar:

```bash
viber analyze <mundo>            # (sair do checkout do Viber evita delegação de run; analyze nunca delega)
```

Interpretar e reportar por severidade (skill **viber-worlds**, references/xml-contract.md):

- **Erros** (exit 1): XML inválido, `world`/`scene` aninhados, `<Include>` não
  expandido, `<Road>` sem `path`, `<Arch>` sem `at` nem `path`, Prototype
  desconhecido em `--strict`.
- **✗ assets** (erro em `--strict`): GLB/texturas/heightmaps/BGM/scripts/estilos
  ausentes — resolver caminhos contra as asset roots do `config.yaml` (a pasta do
  jogo 1.ª, roots extra depois). GLB Draco/Basis não são lidos (meshopt sim).
- **⚠ conflitos**: estrada × lago/rio (contorno REAL do lago, ±45%)/cliff com
  meia-largura do ribbon; `profile="bridge"` sobre água = travessia quebrada
  (usar `<Bridge>` volumétrico ou `<Composition>`); cut × água; lago sobre
  `Plateau`.
- **⚠ quests** (`audit_quests`): dador sem `<DialogueNPC dialogue-id=…>`, marco
  `visit` inexistente, alvo `kill` sem script de criatura que o produza. Um
  `biome` em falta derruba o FICHEIRO inteiro.
- **ℹ / no-ops**: tags desconhecidas (mundos VibeGame degradam com garcia —
  decidir se é intenção), glTF sem collider, estrada × cut.
- **Avisos de mundo estático**: `<StaticSpawner>`/`place` num `world.static.xml`
  (correr a ferramenta/bake em vez disso).

Se o mundo é procedural com estático já cozido, o analyze abre o ESTÁTICO por
ordem de preferência — para auditar o gerador, apontar o `world.xml` explícito.
Terminar com veredicto: LIMPO / avisos (lista) / erros (lista + correção
sugerida). Em CI: `--strict`.
