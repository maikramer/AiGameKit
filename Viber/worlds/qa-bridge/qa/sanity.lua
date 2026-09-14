-- sanity.lua — cenário `viber debug test` sobre worlds/qa-bridge.xml.
-- Corre na REPL do bridge (globals persistem): arrange + asserts contra o
-- snapshot. Regras de um cenário ROBUSTO a engine viva:
--   * nada de posições absolutas do herói (a simulação corre e ele deriva
--     em encostas — o smoke apanhou-os a 5 m do spawn);
--   * `find`/leituras veem o snapshot do INÍCIO do chunk: uma entidade
--     spawnada aqui só se verifica num chunk SEGUINTE;
--   * a bancada NÃO tem quests (sem quests_dir no config) — não assertar.

local p = viber.debug.player()
expect(p ~= nil, "player existe")
local t = viber.debug.terrain(p.x, p.z)
expect(t ~= nil, "terreno ao vivo no ponto do player")
expect(t.in_field, "player dentro do campo do terreno")

local nav = viber.debug.nav()
expect(nav ~= nil and nav.enabled, "<NavMesh> ativa")
expect(nav.tile_generations >= 1, "navmesh já cozeu pelo menos um tile")

expect(#viber.debug.waypoints().landmarks == 12, "12 marcos da Nota")
expect(viber.debug.find("lanterna") ~= nil, "lanterna existe")
expect(viber.debug.seeds().terrain_seed == 77, "seed do mundo (XML)")

-- Só leituras no fim: o spawn é uma escrita (visível no chunk seguinte).
viber.debug.spawn("box:1,1,1", 10, 0, 10, { color = "#ff8800" })

return "sanity ok"
