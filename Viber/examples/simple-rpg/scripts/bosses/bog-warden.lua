-- bosses/bog-warden.lua: guardião do pântano. Toda a máquina vive em
-- lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.0, chase = 3.0,
  aggro = 13, deaggro = 18,
  range = 2.4, damage = 16, cooldown = 1.6,
  radius = 9,
  on_chase = function() viber.sound("roar") end,
}

function on_update(dt)
  fsm.update(dt)
end
