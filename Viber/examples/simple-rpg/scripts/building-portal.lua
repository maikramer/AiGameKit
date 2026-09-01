-- building-portal.lua: portas das salas interiores (interiors.xml) tele-
-- portam de volta para a porta exterior correspondente na vila.
-- v2: mapear destino por nome/posição da entidade quando o runtime expuser
-- `viber.name()`; hoje usa o par known-door mais próximo da posição atual.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Entrar", "e", 2.5)
end

-- portas exteriores na vila (praça) — pares (x, z)
local doors = {
  {  7.46, 22.46 },  -- capela
  { -26.11, 30.00 }, -- celeiro
  { 35.53, -37.53 }, -- casa comprida
  { 10.10, -15.70 }, -- banca 1
}

function on_update(dt)
  if viber.interacted("e") and not st.cd then
    local x, y, z = viber.position()
    -- o portal interior fica em (~857, 217..330); devolve à porta da vila
    local best, bd = doors[1], math.huge
    for _, d in ipairs(doors) do
      local dd = (d[1] - (x % 100)) ^ 2
      if dd < bd then bd, best = dd, d end
    end
    st.cd = true
    viber.teleport_player(best[1], (y or 25) + 0.2, best[2])
    viber.toast("A porta te devolve à vila.")
  elseif st.cd and not viber.interacted("e") then
    st.cd = false
  end
end
