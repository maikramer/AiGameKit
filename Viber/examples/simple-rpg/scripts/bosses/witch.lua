-- bosses/witch.lua: bruxa do bosque. Toda a máquina vive em lib/fsm.lua
-- (viber.load). Especial: MALDIÇÃO — marca o chão onde o herói está; quem
-- não sair da zona leva o golpe amplificado. Frágil (400 HP) e rápida.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.4, chase = 3.6,
  aggro = 13, deaggro = 18,
  range = 2.4, damage = 18, cooldown = 1.2,
  windup = 0.6, recover = 0.5,
  radius = 10,
  boss = "Bruxa do Bosque",
  poise = 0.10,
  special = {
    every = 3, windup = 1.1, radius = 3.2, mult = 1.5,
    at = "player", clip = "attack", color = "#b04cff",
    on_hit = function() viber.apply_status("venom", 5) end,
  },
  enrage = { at = 0.4, speed = 1.15, cooldown = 0.75,
             toast = "A Bruxa grita — o bosque inteiro escurece!" },
  on_chase = function() viber.sound("roar") end,
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
