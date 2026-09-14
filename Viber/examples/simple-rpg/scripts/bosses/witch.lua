-- bosses/witch.lua: bruxa do bosque. Toda a máquina vive em
-- lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.4, chase = 3.6,
  aggro = 13, deaggro = 18,
  range = 2.4, damage = 18, cooldown = 1.2,
  radius = 10,
  on_chase = function() viber.sound("roar") end,
}

function on_update(dt)
  fsm.update(dt)
end
