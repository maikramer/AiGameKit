-- bosses/boss.lua: ogro dos Picos Gelados com gating (loop 6): dorme
-- enquanto houver OUTROS hostis vivos na banda sul. Toda a máquina vive em
-- lib/fsm.lua (viber.load). Chefe final: poise alto, SISMO a cada 3 golpes
-- (zona larga à volta dele) e fúria abaixo de 40 % de vida.
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.2, chase = 3.4,
  aggro = 14, deaggro = 19,
  range = 2.5, damage = 22, cooldown = 1.5,
  windup = 0.75, recover = 0.7,
  radius = 9,
  boss = "Ogro dos Picos",
  poise = 0.16,
  special = {
    every = 3, windup = 1.2, radius = 5.0, mult = 1.6,
    at = "self", clip = "swordheavy,punch,attack", color = "#ff4a2a",
    label = "O Ogro ergue os punhos…",
  },
  enrage = { at = 0.4, speed = 1.2, cooldown = 0.7,
             toast = "O Ogro entra em FÚRIA!" },
  gate = function()
    -- `> 1`: ele próprio é hostil e conta-se — com `> 0` nunca despertava.
    return viber.alive_in_region(2) <= 1
  end,
  awake_toast = "Um rugido ecoa dos Picos Gelados — o ogro despertou!",
}

function on_player_attack(px, pz)
  fsm.alert()
end

function on_update(dt)
  fsm.update(dt)
end
