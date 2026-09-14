-- enemies/bandit.lua: FSM wander/chase com aggro-chain e anti-stuck.
-- Toda a máquina vive em lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.8, chase = 3.8,
  aggro = 10, deaggro = 15,
  range = 2.0, damage = 12, cooldown = 1.6,
  radius = 6,
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
