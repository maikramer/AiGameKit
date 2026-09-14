-- enemies/scorpion.lua: steering + pivô rápido + veneno no golpe (dá uso ao
-- antídoto [2]). Toda a máquina vive em lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.2, chase = 3.4,
  aggro = 9, deaggro = 14,
  range = 1.7, damage = 11, cooldown = 1.8,
  radius = 5,
  turn_rate = 9.0, -- sem deslize lateral nas viragens (gait de 4 patas)
  steering = true,
  anti_stuck = false,
  on_attack = function() viber.apply_status("venom", 4) end, -- veneno 4 s
}

function on_update(dt)
  fsm.update(dt)
end
