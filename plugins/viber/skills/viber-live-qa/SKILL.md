---
name: viber-live-qa
description: Ponteiro. O QA vivo do Vael (bridge, wait-ready, screenshot, burst, input, profiler) está documentado no repo do jogo — skill vael-tools e docs/TOOLS.md.
---

# QA vivo do Vael

A engine vive em `../bevy_extensions/crates/bevy_ext_viber_engine`; o CLI é
`vael-tools` (`Vael/cli/`). O fluxo e as armadilhas estão no repo do jogo:

- `../Vael/.cursor/skills/vael-tools/SKILL.md`
- `../Vael/docs/TOOLS.md` e `../Vael/docs/DEBUG.md`

O plugin envolve esse CLI (`VAEL_TOOLS_BIN`), por isso as tools MCP
(`viber_status`/`screenshot`/`burst`/`tree`/`logs`/`input`/`lua`) falam com o
bridge na mesma porta (15702, ou `--bridge`/`VAEL_TOOLS_PORT`).
