# CONTEXT.md — docs

Dois guias canónicos, consumidos por humanos e agentes:

- **`LUA_API.md`** — toda a superfície Luau exposta a scripts de entidade.
  É a referência a citar quando se escreve ou revê um `.lua` (os ~46 scripts
  de `examples/simple-rpg/scripts/` são os exemplos vivos).
- **`UI.md`** — como construir HUD/menus com a UI declarativa
  (`<UiRoot>`/`<UiStyle>` + `viber.ui.*`).

Não documentar aqui coisas de Rust interno (isso vive nos `AGENTS.md` da
raiz e das pastas) — estes docs são a **superfície de autoria** de mundos:
XML, CSS e Luau.
