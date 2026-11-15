---
description: Validação headless de um mundo `.bsn` com `vael-tools analyze`
argument-hint: [world.bsn] [--strict]
allowed-tools: Bash
---

# Analisar o mundo (headless)

Mundo alvo: `$ARGUMENTS` (se vazio, `vael-tools analyze` procura `world.bsn`
no cwd). Não abre janela e é exit 1 em erro — CI-ready.

```bash
vael-tools analyze <mundo> [--json] [--strict]
```

Interpretar e reportar:

- **Erros**: `.bsn`/includes que não carregam; assets e scripts em falta.
- **Avisos**: nomes duplicados, protótipos `mob-*` sem `<Prototype>`, script
  referido que não existe em `scripts/`.
- **Info**: entidades por tag, assets/scripts contados, ficheiros do mundo
  carregados.
- `--strict` promove avisos/protótipos desconhecidos a falha.

As regras de conteúdo (quests, itens, NPCs, locale) são
`python3 tools/lint_content.py` no repo do jogo. O painel **Vael: Problems**
do Jackdaw usa a mesma lib.
