# Plugin viber (MCP)

QA vivo e análise do jogo Vael para agentes ZCode. O servidor MCP envolve o CLI
`vael-tools` (`VAEL_TOOLS_BIN`, default `vael-tools` no PATH) — não reimplementa
o cliente BRP.

- CLI + bridge: `../Vael/cli/` e `../Vael/docs/TOOLS.md`
- Jogo: `../Vael` (editor oficial: `jd open .`)
- Engine: `../bevy_extensions` (`bevy_ext_viber_engine` + `bevy_ext_bridge_*`)
- Skills de trabalho: `Vael/.cursor/skills/` (`vael`, `vael-tools`, `vael-editor`, `asset-gen`)

Tools: `viber_status`, `viber_screenshot`, `viber_burst`, `viber_tree`,
`viber_logs`, `viber_input`, `viber_lua`. Os CLIs de geração continuam neste
repo; o jogo chama-os a partir de `Vael/assets`.
