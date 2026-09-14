-- enemies/bogling.lua: criatura lenta do pântano (sem anti-stuck nem
-- aggro-chain — wander curto). Toda a máquina vive em lib/fsm.lua.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.0, chase = 2.8,
  aggro = 8, deaggro = 13,
  range = 1.5, damage = 8, cooldown = 1.5,
  radius = 4,
  anti_stuck = false,
}

function on_update(dt)
  fsm.update(dt)
end
