-- npc/forest-wolves.lua: diálogo 100% Lua de Hald, o do Gado (dador da
-- quest `forest_wolves`). Prova do desacoplamento:
--   * `viber.own_system("dialogue")` — a engine CEDE o [E] deste NPC (o
--     fluxo nativo de quests cala; o prompt/ação continuam arbitrados pelo
--     mesmo InteractionFocus);
--   * `viber.say(...)` escreve no BALÃO nativo do HUD;
--   * `viber.quest_def(id)` lê o MESMO JSON autoral (`quests/*.json`) — o
--     script não duplica títulos nem linhas;
--   * aceitar/entregar usa os comandos `quest_accept`/`quest_turn_in`
--     (estado, recompensas e tracker continuam da engine).
--
-- GOTCHA: o top-level corre ANTES do primeiro snapshot do frame — as defs
-- (e todo o ctx) só estão disponíveis dentro do `on_update`. Por isso o
-- `quest_def` é buscado laziamente.

local QUEST = "forest_wolves"
local def = nil

viber.own_system("dialogue")

local function say_lines(lines, fallback)
  local text = table.concat(lines or {}, "\n")
  if text == "" then
    text = fallback
  end
  viber.say(text)
end

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE
  if not st.ready then
    st.ready = true
    viber.set_interaction("Falar com Hald", "e", 3.5)
  end
  if def == nil then
    def = viber.quest_def(QUEST)
  end
  if not viber.interacted("e") then
    return
  end
  viber.face_player()
  viber.gesture("talk")
  local state = viber.quest_state(QUEST)
  if state == "not_taken" then
    viber.sound("quest_accept")
    say_lines(def and def.lines_intro, "“Lobos ao norte. Paga-se bem por presas.”")
    viber.quest_accept(QUEST)
  elseif state == "active" then
    say_lines(def and def.lines_progress, "“Os lobos ainda andam por aí, forasteiro.”")
  elseif state == "ready" then
    viber.sound("quest_done")
    say_lines(def and def.lines_complete, "“Bom trabalho. O gado respira.”")
    viber.quest_turn_in(QUEST)
  else
    say_lines(def and def.lines_complete, "“Já cumpriste. O rebanho agradece.”")
  end
end
