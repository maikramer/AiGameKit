# AGENTS.md — src/xml

Escopo: parsing do world XML — modelo de árvore, expansão de `<Include>` e
parsers tolerantes de valores. **Sem lógica de jogo aqui.** Contrato completo
do formato: `AGENTS.md` da raiz do Viber.

## Ficheiros

| Ficheiro | Responsabilidade |
|----------|------------------|
| `mod.rs` | `XmlNode` (`tag`/`attrs`/`text`/`children`) + parse do documento. Nomes de tag/attr preservados tal como escritos; o matching case-insensitive acontece a jusante (recipes). `text` = conteúdo direto aparado (o que `<UiText>100/100</UiText>` mete entre tags) |
| `include.rs` | expansão `<Include src>`: profundidade máx. **8**, ciclos fail-fast; caminhos com `/` resolvem contra o dir do ficheiro raiz, relativos contra o dir do ficheiro que inclui; fragmentos com raiz `<world>`/`<scene>` contribuem os filhos |
| `values.rs` | parsers tolerantes: vetores `"x y z"` com broadcast de 1 valor (**2 valores = erro**), bools bare + `true/1/yes/on` / `false/0/no/off`, rejeição de `NaN`/`inf` |
| `emit.rs` | serialização da árvore owned (`emit_document`) + API de edição mínima (`XmlNode::new`/`set_attr`/`push_child`/`duplicate`) — fundação do `world.static.xml` do `viber bake`. Comentários não existem no modelo (o parse descarta-os); CDATA entra como `text` |

## Regras

- Atributos ficam **string** até uma recipe os interpretar — não converter
  tipos nesta camada.
- Manter as tolerâncias históricas do formato (bools bare, tags
  case-insensitive): mundos antigos têm de continuar a correr.
- `world`/`scene` aninhados e `<Include>` não-expandido = **erro**.
- Includes podem sair da árvore de pastas (`..`, symlinks) — decisão
  consciente (CLI local, sem sandbox).
- Raiz aceite: `<world>` (ou `<scene>`), attr `clear-color`.

## Verificar

```bash
cd Viber && cargo test
cargo run -- analyze <world.xml>   # warnings de atributos desconhecidos saem aqui
```
