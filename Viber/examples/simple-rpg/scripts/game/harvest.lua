-- Colheita. Um controller lê `viber.destructibles` (dados do attr) e
-- decide o golpe. A engine não minera.

local rules = viber.load("lib/rules.lua")

local SWING = 0.45

local function hits_key(id)
  return "chop." .. tostring(id)
end

function on_update(dt)
  local st = viber.state()
  st.cd = math.max(0, (st.cd or 0) - dt)
  if viber.menu_open() then
    return
  end
  if not viber.input.pressed("j") and not viber.input.pressed("mouse1") then
    return
  end
  if st.cd > 0 then
    return
  end
  local list = viber.destructibles(4) or {}
  local target
  for _, row in ipairs(list) do
    if row.distance <= (row.range or 3.5) then
      target = row
      break
    end
  end
  if not target then
    return
  end
  st.cd = SWING
  viber.game().harvest_swing = viber.time()
  local clip = target.style == "fall" and "chop" or "mine"
  viber.play_clip(clip, { id = viber.player_id(), speed = 1.4 })
  viber.sound(target.style == "fall" and "chop_hit" or "mine_hit")
  viber.burst("sparks", target.x, target.y + 1, target.z, 8)
  local game = viber.game()
  local key = hits_key(target.id)
  local taken = (game[key] or 0) + 1
  game[key] = taken
  local need = target.hits_left or 1
  if taken < need then
    return
  end
  game[key] = nil
  if target.kind and target.kind ~= "" then
    rules.vault_add(target.kind, target.amount or 1)
  end
  rules.add_xp(30)
  if target.popup and target.popup ~= "" then
    viber.damage_number(target.popup, { x = target.x, y = target.y + 1.8, z = target.z, color = "#d6c07a" })
  end
  viber.sound(target.style == "fall" and "chop_break" or "mine_break")
  viber.burst(target.style == "shatter" and "sparks" or "leaves", target.x, target.y + 1, target.z, 16)
  if target.style == "fall" then
    viber.topple(target.id)
  else
    viber.entity_despawn(target.id)
  end
end
