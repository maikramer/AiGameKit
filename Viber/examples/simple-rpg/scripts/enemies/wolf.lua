-- enemies/wolf.lua: matilha com aggro-chain, steering e pivô rápido.
-- Toda a máquina vive em lib/fsm.lua (viber.load) — aqui ficam só os números.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 2.2, chase = 4.6,
  aggro = 11, deaggro = 16,
  range = 1.6, damage = 9, cooldown = 1.2,
  radius = 7,
  turn_rate = 9.0, -- sem deslize lateral nas viragens (gait de 4 patas)
  steering = true,
  on_chase = function() viber.sound("growl") end,
}

-- aggro-chain (loop 6): um golpe do herói num lobo da matilha acorda os
-- outros a 15 m — forçam perseguição mesmo fora do raio de aggro.
function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
