-- building-portal.lua: portas bidireccionais entre a VILA e a BOLSA DE INTERIORES.
--
-- GERADO por tools/gen_interiors.py — a tabela ROOMS do fim é derivada da
-- MESMA tabela de salas que gera world/interiors.xml. Não editar à mão: mudar
-- a bolsa (`POCKET`) ou as salas é mudar o gerador.
--
-- v5 (2026-09-11): a grelha de interiores saiu da área do mapa. Vive numa bolsa
-- declarada por <InteriorScene> a FORA da pegada do heightmap (world-size
-- 4000), onde a engine não gera colunas de terreno nem colliders. Um NPC/portal
-- que a navegue não tem terreno para pisar: o chão é o soalho da sala.
--
-- SENTIDO: decidido pela POSIÇÃO. Dentro da bolsa (muito para lá de POCKET) a
-- entidade é uma SAÍDA interior; fora é uma porta de rua. Nada de tabelas
-- inversas mantidas à mão — o par porta↔saída sai de uma linha só.
--
-- OS Y's NÃO SÃO ADIVINHADOS: dentro da bolsa o destino é o soalho (0.6 m,
-- não há terreno); fora, `viber.ground_below` resolve a cota do adro. A versão
-- anterior teleportava para y≈0.3 na vila e o herói caía 24 m.
local POCKET = { x = 2600.0, z = 2600.0 }
local POCKET_MIN_X = POCKET.x - 200.0   -- tudo acima disto é interiores

local FLOOR_Y = 0.6      -- soalho da sala (a laje está a 0.12)
local INWARD = 6.0       -- quanto o herói entra para dentro ao chegar
local MATCH_R = 3.0      -- raio de casamento porta↔saída

function on_update(dt)
  local st = viber.state()
  local x, y, z = viber.position()
  local inside_pocket = x > POCKET_MIN_X

  if not st.ready then
    st.ready = true
    viber.set_interaction(st.inside and "Sair" or "Entrar", "e", 2.8)
  end
  -- `interacted` não consome o evento duas vezes no mesmo frame entre
  -- entidades: cada portal decide pelo seu próprio cooldown.
  if not viber.interacted("e") then
    st.cd = false
    return
  end
  if st.cd then return end

  if inside_pocket then
    -- Saída interior: casar a minha posição RELATIVA à bolsa com uma sala.
    local rx, rz = x - POCKET.x, z - POCKET.z
    local best, bd = nil, MATCH_R * MATCH_R
    for _, r in ipairs(ROOMS) do
      local dx, dz = r.ox - rx, r.oz + r.exit_dz - rz
      local d = dx * dx + dz * dz
      if d < bd then bd, best = d, r end
    end
    if not best then
      viber.log("building-portal: saída sem sala a <" .. MATCH_R .. "m — grelha e XML fora de sincronia?")
      return
    end
    st.cd = true
    local gy = viber.ground_below(best.door_x, 200.0, best.door_z)
    viber.sound("door_close")
    viber.teleport_player(best.door_x, (gy or 25.0) + 0.25, best.door_z)
    viber.toast("A porta devolve-te à vila.")
  else
    -- Porta de rua: casar com a porta mais próxima.
    local best, bd = nil, MATCH_R * MATCH_R
    for _, r in ipairs(ROOMS) do
      local dx, dz = r.door_x - x, r.door_z - z
      local d = dx * dx + dz * dz
      if d < bd then bd, best = d, r end
    end
    if not best then
      viber.log("building-portal: porta sem sala declarada a <" .. MATCH_R .. "m")
      return
    end
    st.cd = true
    viber.sound("door_open")
    viber.teleport_player(POCKET.x + best.ox, FLOOR_Y, POCKET.z + best.oz + best.exit_dz + INWARD)
    viber.toast("Entras no edifício.")
  end
end

-- ── REGISTO (gerado: { id, porta exterior (x, z), offset da sala (x, z), z local do vão }) ──
local ROOMS = {
  { "chapel",     7.46,    22.46,    60.0,     0.0,  -10.00 },
  { "forge",   -30.47,   -29.06,   120.0,     0.0,   -9.00 },
  { "house_a",    26.35,     8.44,     0.0,     0.0,   -9.00 },
  { "house_b",   -17.44,    22.47,     0.0,    55.0,   -9.00 },
  { "house_c",   -20.47,   -18.44,    60.0,    55.0,   -9.00 },
  { "shepherd",   -22.33,    12.95,   120.0,    55.0,   -8.00 },
  { "barn",   -26.11,    30.00,     0.0,   110.0,  -11.00 },
  { "longhouse",    35.53,   -37.53,    60.0,   110.0,  -11.00 },
  { "market",    10.10,   -15.70,   120.0,   110.0,   -8.00 },
}
