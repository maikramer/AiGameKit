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
local AUTO_EXIT_R = 1.6  -- encostar à porta POR DENTRO sai sem tecla

-- ── REGISTO (gerado) ──
-- ANTES da lógica de propósito: em Luau um `local` declarado DEPOIS de uma
-- função não entra no escopo dela — a tabela no fim do ficheiro chegava ao
-- `on_update` como global nil e o [E] das portas morria em
-- `ipairs(nil)` (repro do utilizador 2026-09-12: "na porta da igreja aperto
-- E e não faz nada"). Campos por NOME: a lógica lê `r.door_x`/`r.ox`/…,
-- e as linhas posicionais antigas davam nil em todos eles.
local ROOMS = {
  { id = "chapel", door_x =     7.46, door_z =    22.46, ox =    60.0, oz =     0.0, exit_dz =  -10.00 },
  { id = "forge", door_x =   -30.47, door_z =   -29.06, ox =   120.0, oz =     0.0, exit_dz =   -9.00 },
  { id = "house_a", door_x =    26.35, door_z =     8.44, ox =     0.0, oz =     0.0, exit_dz =   -9.00 },
  { id = "house_b", door_x =   -17.44, door_z =    22.47, ox =     0.0, oz =    55.0, exit_dz =   -9.00 },
  { id = "house_c", door_x =   -20.47, door_z =   -18.44, ox =    60.0, oz =    55.0, exit_dz =   -9.00 },
  { id = "shepherd", door_x =   -22.33, door_z =    12.95, ox =   120.0, oz =    55.0, exit_dz =   -8.00 },
  { id = "barn", door_x =   -26.11, door_z =    30.00, ox =     0.0, oz =   110.0, exit_dz =  -11.00 },
  { id = "longhouse", door_x =    35.53, door_z =   -37.53, ox =    60.0, oz =   110.0, exit_dz =  -11.00 },
  { id = "market", door_x =    10.10, door_z =   -15.70, ox =   120.0, oz =   110.0, exit_dz =   -8.00 },
}

function on_update(dt)
  local st = viber.state()
  local x, y, z = viber.position()
  local inside_pocket = x > POCKET_MIN_X

  if not st.ready then
    st.ready = true
    viber.set_interaction(st.inside and "Sair" or "Entrar", "e", 2.8)
  end
  -- SAÍDA AUTOMÁTICA (interiores): a porta de uma sala é um vão aberto sobre
  -- o vazio — encostar a ela tem de tirar o herói dali, não pedir uma tecla
  -- (pedido do utilizador 2026-09-13: "bloquear e imediatamente me fazer
  -- sair"). O piso duro da bolsa trata da queda; este gatilho trata da
  -- saída. Só vale de DENTRO: uma porta de rua continua a exigir [E], senão
  -- passar à frente de uma casa sugava o jogador.
  local auto = inside_pocket and viber.distance_to_player() <= AUTO_EXIT_R
  -- `interacted` não consome o evento duas vezes no mesmo frame entre
  -- entidades: cada portal decide pelo seu próprio cooldown.
  if not (auto or viber.interacted("e")) then
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

