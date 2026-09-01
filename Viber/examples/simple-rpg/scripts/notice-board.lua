-- notice-board.lua: quadro de avisos da praça — bounties repetíveis da
-- guarda (quest npc == "notice_board" nos JSONs). Cada leitura mostra a
-- próxima bounty disponível: aceita (intro) ou entrega (completa) via
-- hooks `viber.quest_*`.
local BOUNTIES = { "city_wolves", "city_bandits", "city_goblins", "city_wood" }

local st = viber.state()
if not st.ready then
  st.ready = true
  st.index = 0
  viber.set_interaction("Ler o quadro", "e", 3.0)
end

function on_update(dt)
  if not viber.interacted("e") then
    return
  end

  -- procura a próxima bounty em ciclo que possa aceitar ou entregar
  local total = #BOUNTIES
  for step = 1, total do
    local i = ((st.index or 0) + step - 1) % total + 1
    local id = BOUNTIES[i]
    local status = viber.quest_state(id)
    if status == "not_taken" then
      viber.quest_accept(id)
      st.index = i
      viber.toast("Bounty aceita: " .. id)
      return
    elseif status == "ready" then
      viber.quest_turn_in(id)
      st.index = i
      return -- quest_turn_in já emite o toast de recompensa
    end
  end

  viber.toast("Nenhuma bounty nova — termine as que aceitou.")
end
