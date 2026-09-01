-- merchant.lua: loja completa chega com a Fase de inventário; hoje: banter.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Falar com Bram", "e", 3.5)
end
local lines = {
  "“Ferro bom não se negocia barato, forasteiro.”",
  "“Precisa de uma lâmina? Chegou na hora certa.”",
  "“Dizem que há cristais nas ruínas ao leste...”",
}
function on_update(dt)
  if viber.interacted("e") then
    st.i = ((st.i or 0) + 1) % #lines + 1
    viber.toast(lines[st.i])
  end
end
