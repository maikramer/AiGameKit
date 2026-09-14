-- game/hotbar.lua: a HOTBAR é do jogo (Lua). Reclama [1]/[2]
-- (`viber.own_system("hotbar")`) e reimplementa os slots sobre a API
-- genérica: poção (`vault_take` + `heal_player` + número) e antídoto
-- (`status_clear("venom")`), com o mesmo cooldown de 1 s do nativo.

viber.own_system("hotbar")

local POTION_HEAL = 50
local COOLDOWN = 1.0

function on_update(dt)
  local st = viber.state()
  st.cd = math.max(0, (st.cd or 0) - dt)
  -- `viber.ui.is_open` exige o id do modal (não há "algum aberto?" na API).
  if viber.ui.is_open("menu") or viber.ui.is_open("profiler") or st.cd > 0 then
    return
  end
  if viber.input.pressed("1") then
    st.cd = COOLDOWN
    if viber.item_count("potion") > 0 then
      viber.vault_take("potion", 1)
      viber.heal_player(POTION_HEAL)
      viber.damage_number("+" .. POTION_HEAL .. " HP", { color = "#7ef29d" })
      viber.toast("Poção usada (+" .. POTION_HEAL .. " HP)")
    else
      viber.toast("Sem poções.")
      viber.sound("error")
    end
  elseif viber.input.pressed("2") then
    st.cd = COOLDOWN
    if viber.item_count("antidote") > 0 then
      viber.vault_take("antidote", 1)
      viber.status_clear("venom")
      viber.toast("Veneno neutralizado.")
    else
      viber.toast("Sem antídotos.")
      viber.sound("error")
    end
  end
end
