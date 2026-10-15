-- enemies/bandit-archer.lua: bandido de arco — mantém 5–15 m e dispara o
-- `<ProjectileTemplate id="enemy-arrow">` (o arco tensa ~0,7 s antes: sai da
-- linha). Colado ao herói recua; encurralado dá com a adaga.
-- Toda a máquina vive em lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.6, chase = 3.4,
  aggro = 16, deaggro = 22, -- vê de longe
  range = 1.6, damage = 7, cooldown = 1.5, -- adaga: fraca
  windup = 0.45, recover = 0.4,
  radius = 6,
  ranged = { range = 15, min = 5, cooldown = 2.6, windup = 0.7, projectile = "enemy-arrow" },
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
