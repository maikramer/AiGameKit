-- townsfolk.lua: moradores passeiam perto de casa e olham o player passar.
local st = viber.state()
local SPEED = 1.1
local RADIUS = 4
local target = nil

function on_update(dt)
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  local dist = math.sqrt((px - x)^2 + (pz - z)^2)
  if dist < 4.5 then
    viber.face_player()
    return
  end
  if target == nil then
    local tx, tz = viber.wander_target(RADIUS)
    target = { tx, tz }
  end
  local td = math.sqrt((target[1] - x)^2 + (target[2] - z)^2)
  if td < 0.6 then
    target = nil
  else
    viber.move_towards(target[1], target[2], SPEED)
  end
end
