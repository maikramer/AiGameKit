-- rock.lua: colhível em 3 golpes ([J]); no último, a entidade cai e a
-- coleta reporta ao diário de quests (`viber.report_collect`).
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
      viber.report_collect("stone", 1)
      viber.toast("Pedra quebrada! (+30 XP, +1 pedra)")
      viber.topple()
    else
      viber.toast("Minerar: " .. st.hits .. "/3")
    end
  end
end
