-- healer.lua: cura completa quando o player interage.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Ser curado", "e", 3.5)
end
function on_update(dt)
  if viber.interacted("e") then
    viber.heal_player(999)
    viber.toast("“Que as graças te acompanhem.” — cura completa")
  end
end
