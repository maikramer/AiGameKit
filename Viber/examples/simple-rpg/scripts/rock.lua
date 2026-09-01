-- rock.lua: colhível em 3 golpes ([J]); no último, a entidade cai.
-- (A engine provê interacted/despawn_self; o inventário chega na Fase 3.)
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Minerar", "j", 3.0)
end

function on_update(dt)
  if viber.interacted("j") then
    st.hits = (st.hits or 0) + 1
    if st.hits >= 3 then
      viber.add_xp(30)
      viber.toast("Pedra quebrada! (+30 XP)")
      viber.despawn_self()
    else
      viber.toast("Minerar: " .. st.hits .. "/3")
    end
  end
end
