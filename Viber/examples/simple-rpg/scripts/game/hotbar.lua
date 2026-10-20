local rules = viber.load("lib/rules.lua")
-- game/hotbar.lua: a HOTBAR é do jogo (Lua). Reclama [1]/[2]
-- (`viber.own_system("hotbar")`) e reimplementa os slots sobre a API
-- genérica: poção (`vault_take` + `heal_player` + número) e antídoto
-- (`status_clear("venom")`), com o mesmo cooldown de 1 s do nativo.

viber.own_system("hotbar")

local POTION_HEAL = 50
local COOLDOWN = 1.0

-- Ctrl/Alt+dígito são atalhos (o `<PostFxDebugToggle>` vive em Ctrl+1..6).
local function modifier_held()
  return viber.input.down("lctrl") or viber.input.down("rctrl")
    or viber.input.down("lalt") or viber.input.down("ralt")
end

function on_update(dt)
  local st = viber.state()
  st.cd = math.max(0, (st.cd or 0) - dt)
  -- O profiler é `profiler-win`: `is_open("profiler")` era sempre falso e a
  -- poção saía com o painel aberto. Mapa e viagem entram no mesmo portão.
  if viber.menu_open() or st.cd > 0 or modifier_held() then
    return
  end
  if viber.input.pressed("1") then
    local _, hp, max_hp = viber.player_hp()
    local healed = math.min(POTION_HEAL, math.max(0, (max_hp or 0) - (hp or 0)))
    if rules.item_count("potion") > 0 and healed < 1 then
      -- Vida cheia: a poção fica no cinto e o [2] não fica bloqueado 1 s.
      viber.toast("Vida já está cheia.")
    elseif rules.item_count("potion") > 0 then
      st.cd = COOLDOWN
      rules.vault_take("potion", 1)
      rules.heal_player(healed)
      -- O número nasce SOBRE O HERÓI (sem x/y/z saía sobre o controller).
      local _, px, py, pz = viber.player_position()
      viber.damage_number("+" .. math.floor(healed) .. " HP", { color = "#7ef29d", x = px, y = py + 2.0, z = pz })
      viber.sound("heal")
      viber.toast("Poção usada (+" .. math.floor(healed) .. " HP)")
    else
      viber.toast("Sem poções.")
      viber.sound("error")
    end
  elseif viber.input.pressed("2") then
    if rules.item_count("antidote") > 0 then
      -- `status.venom` é o bind do chip ENVENEADO (texto vazio = sem veneno).
      -- Antes o antídoto era gasto e o toast dizia "neutralizado" mesmo são.
      if not rules.poisoned() then
        viber.toast("Não estás envenenado.")
      else
        st.cd = COOLDOWN
        rules.vault_take("antidote", 1)
        rules.status_clear("venom")
        viber.toast("Veneno neutralizado.")
      end
    else
      viber.toast("Sem antídotos.")
      viber.sound("error")
    end
  end
end
