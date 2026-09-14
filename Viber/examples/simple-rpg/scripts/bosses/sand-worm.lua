-- bosses/sand-worm.lua: verme do deserto. Toda a máquina vive em
-- lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.2, chase = 4.0,
  aggro = 12, deaggro = 17,
  range = 2.6, damage = 20, cooldown = 2.0,
  radius = 8,
  on_chase = function() viber.sound("roar") end,
}

function on_update(dt)
  fsm.update(dt)
end
