-- enemies/shade.lua: criatura rápida do ermo (sem anti-stuck nem
-- aggro-chain). Toda a máquina vive em lib/fsm.lua.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.4, chase = 4.2,
  aggro = 12, deaggro = 17,
  range = 1.8, damage = 14, cooldown = 2.0,
  radius = 5,
  anti_stuck = false,
}

function on_update(dt)
  fsm.update(dt)
end
