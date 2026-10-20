-- Diálogo genérico de um dador. O id da quest vem de prop-dialogue_id.
-- Aceita, mostra progresso e entrega. Sem a quest nativa da engine.
--@export dialogue_id: string = ""

local rules = viber.load("lib/rules.lua")

local function quest_id()
  return viber.prop("dialogue_id")
end

local function say(lines, fallback, remaining)
  local text = table.concat(lines or {}, "\n")
  if text == "" then
    text = fallback
  end
  text = text:gsub("{remaining}", tostring(remaining or 0))
  viber.say(text)
end

function on_update(_dt)
  local id = quest_id()
  if not id or id == "" then
    return
  end
  local st = viber.state()
  local def = rules.quest_def(id)
  local title = def and def.title or id
  if not st.ready then
    st.ready = true
    viber.set_interaction(title, "e", 3.5)
  end
  if viber.menu_open() or not viber.interacted("e") then
    return
  end
  viber.face_player()
  viber.gesture("talk")
  local have, need = 0, 1
  if rules.quest_progress then
    have, need = rules.quest_progress(id)
  end
  local remaining = math.max(0, (need or 1) - (have or 0))
  local state = rules.quest_state(id)
  if state == "not_taken" then
    rules.quest_accept(id)
    say(def and def.lines_intro, "Aceite.", remaining)
  elseif state == "ready" then
    rules.quest_turn_in(id)
    say(def and def.lines_complete, "Entregue.", 0)
  elseif state == "active" then
    say(def and def.lines_progress, "Ainda não.", remaining)
  else
    viber.say("Já tratámos disso.")
  end
end
