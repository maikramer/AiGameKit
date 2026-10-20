local rules = viber.load("lib/rules.lua")
-- bog-moss.lua: tufo de musgo-do-pântano colhível ([J]) — fonte do
-- item `bog-moss` (objetivo collect ×10 da quest do pântano).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE
  if not st.ready then
    st.ready = true
    -- Sem marca no save, gravar e carregar replantava o musgo e a quest farmava.
    if once.taken() then
      viber.despawn_self()
      return
    end
    viber.set_interaction("Colher musgo", "j", 2.5)
  end
  if viber.interacted("j") and not st.done then
    st.done = true
    once.mark()
    rules.add_xp(8)
    rules.item_add("bog-moss", 1)
    viber.toast("Musgo-do-pântano colhido (+8 XP, +1 bog-moss)")
    viber.topple()
  end
end
