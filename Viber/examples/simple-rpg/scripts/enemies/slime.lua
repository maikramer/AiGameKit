-- enemies/slime.lua: a criatura tutorial — lenta e inofensiva.
-- Toda a máquina vive em lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 0.7, chase = 2.0,
  aggro = 6, deaggro = 11,
  range = 1.4, damage = 6, cooldown = 2.0,
  radius = 3,
  anti_stuck = false,
  on_first = function() viber.log("slime ativo: FSM wander/chase rodando") end,
}

function on_update(dt)
  fsm.update(dt)
end
