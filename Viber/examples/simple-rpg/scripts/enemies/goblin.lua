-- enemies/goblin.lua: FSM wander/chase com aggro-chain e anti-stuck.
-- Toda a máquina vive em lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.6, chase = 3.2,
  aggro = 10, deaggro = 15,
  range = 1.6, damage = 10, cooldown = 1.4,
  radius = 8,
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
