-- bosses/sand-worm.lua: verme do deserto. Toda a máquina vive em
-- lib/fsm.lua (viber.load). Especial: TREMOR — mergulha e rebenta a areia à
-- volta; o aviso cobre um círculo largo, sair dele a correr ou com dash.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.2, chase = 4.0,
  aggro = 12, deaggro = 17,
  range = 2.6, damage = 20, cooldown = 2.0,
  windup = 0.7, recover = 0.7,
  radius = 8,
  boss = "Verme das Areias",
  poise = 0.14,
  special = {
    every = 3, windup = 1.2, radius = 5.5, mult = 1.4,
    at = "self", clip = "roar,attack", color = "#ff8a2a",
    label = "A areia treme…",
  },
  enrage = { at = 0.35, speed = 1.2, cooldown = 0.7,
             toast = "O Verme enfurece-se — a areia ferve!" },
  on_chase = function() viber.sound("roar") end,
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
