# Text2Icon (Português)

CLI text-to-icon com **Sana Sprint 0.6B** ([NVlabs/Sana](https://github.com/NVlabs/Sana)) — gera ícones de UI para jogos em 1–4 passos (~<1s por imagem em GPU moderna). Suporta fundo transparente (RGBA via `rembg`).

Veja [`README.md`](README.md) para a documentação completa (em inglês) e [`../AGENTS.md`](../AGENTS.md) para o guia do monorepo.

## Resumo

```bash
# Ícone opaco
text2icon generate "poção de vida vermelha, jogo de fantasia" -o vida.png

# Ícone transparente
text2icon generate "ícone de espada, RPG medieval" -o espada.png --transparent

# Batch
text2icon batch icones.txt -d icones/ --transparent
```

## Integração com GameAssets

Adiciona um bloco `text2icon:` ao `game.yaml`:

```yaml
text2icon:
  prompts:
    - "ícone de poção de vida"
    - "ícone de espada"
  transparent: true
```

`gameassets batch` gera os ícones; `gameassets handoff` copia-os para `public/assets/icons/`.
