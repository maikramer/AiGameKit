local rules = viber.load("lib/rules.lua")
-- notice-board.lua: quadro de avisos da praça — bounties repetíveis da
-- guarda (quest npc == "notice_board" nos JSONs). Cada leitura mostra a
-- próxima bounty disponível: aceita (intro) ou entrega (completa) via
-- hooks `viber.quest_*`.
local BOUNTIES = { "city_wolves", "city_bandits", "city_goblins", "city_wood" }

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.index = 0
    viber.set_interaction("Ler o quadro", "e", 3.0)
  end
  if not viber.interacted("e") then
    return
  end

  -- Entregar ganha a aceitar: com um cartaz pronto e outro por aceitar, o
  -- quadro pagava a nova e deixava o ouro da concluída à espera de outra volta.
  local total = #BOUNTIES
  local function find(status)
    for step = 1, total do
      local i = ((st.index or 0) + step - 1) % total + 1
      local id = BOUNTIES[i]
      if rules.quest_state(id) == status then
        return i, id
      end
    end
    return nil, nil
  end
  local i, id = find("ready")
  if id then
    rules.quest_turn_in(id)
    st.index = i
    return
  end
  i, id = find("not_taken")
  if id then
    rules.quest_accept(id)
    st.index = i
    return
  end

  viber.toast("Nenhuma bounty nova — termine as que aceitou.")
end
