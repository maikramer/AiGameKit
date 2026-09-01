-- mushroom.lua: colheita de cogumelo.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Colher cogumelo", "j", 2.5)
end
function on_update(dt)
  if viber.interacted("j") and not st.done then
    st.done = true
    viber.add_xp(5)
    viber.toast("Cogumelo colhido (+5 XP)")
  end
end
