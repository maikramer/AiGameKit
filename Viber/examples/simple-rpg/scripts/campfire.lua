-- campfire.lua: sentar/descansar na fogueira da praça (cura + toast).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Descansar", "e", 2.5)
end
function on_update(dt)
  if viber.interacted("e") then
    viber.heal_player(25)
    viber.toast("O calor da fogueira restaura 25 de vida.")
  end
end
