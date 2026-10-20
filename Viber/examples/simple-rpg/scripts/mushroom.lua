local rules = viber.load("lib/rules.lua")
-- mushroom.lua: colheita de cogumelo (+XP). Sai do mundo ao ser colhido.
--@export xp: int(0..) = 5                  -- XP da colheita
--@export label: string = "Cogumelo"        -- nome no prompt e no toast
--@export key: enum(j, e, f) = j            -- tecla de colheita
--@export range: number(0.5..10) = 2.5      -- alcance (m)

local once = viber.load("lib/once.lua")

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (o top-level é partilhado)
  local key = viber.prop("key")
  if not st.ready then
    st.ready = true
    -- O despawn não fica no XML: um load trazia o cogumelo de volta, com XP.
    if once.taken() then
      viber.despawn_self()
      return
    end
    viber.set_interaction("Colher " .. string.lower(viber.prop("label")), key, viber.prop("range"))
  end
  if viber.interacted(key) and not st.done then
    st.done = true
    once.mark()
    local xp = viber.prop("xp")
    rules.add_xp(xp)
    viber.toast(viber.prop("label") .. " colhido (+" .. xp .. " XP)")
    viber.despawn_self() -- colhido: sai do mundo (o prompt desaparece com ele)
  end
end
