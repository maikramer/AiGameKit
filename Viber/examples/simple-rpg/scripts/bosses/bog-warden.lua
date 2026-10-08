-- bosses/bog-warden.lua: guardião do pântano. Toda a máquina vive em
-- lib/fsm.lua (viber.load). Especial: GOLPE DE LODO — pancada no chão que
-- envenena quem ficar na zona (o antídoto [2] ganha uso).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.0, chase = 3.0,
  aggro = 13, deaggro = 18,
  range = 2.4, damage = 16, cooldown = 1.6,
  windup = 0.7, recover = 0.6,
  radius = 9,
  boss = "Guardião do Lodo",
  poise = 0.13,
  special = {
    every = 3, windup = 1.1, radius = 4.8, mult = 1.3,
    at = "self", clip = "swordheavy,attack", color = "#6fdc5a",
    on_hit = function() viber.apply_status("venom", 6) end,
  },
  enrage = { at = 0.4, speed = 1.2, cooldown = 0.75,
             toast = "O Guardião ruge — o lodo sobe-lhe pelos braços!" },
  on_chase = function() viber.sound("roar") end,
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
