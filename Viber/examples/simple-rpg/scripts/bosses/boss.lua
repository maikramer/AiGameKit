-- bosses/boss.lua: ogro dos Picos Gelados com gating (loop 6): dorme
-- enquanto houver OUTROS hostis vivos na banda sul. Toda a máquina vive em
-- lib/fsm.lua (viber.load).
local fsm = viber.load("lib/fsm.lua").new{
  wander = 1.2, chase = 3.4,
  aggro = 14, deaggro = 19,
  range = 2.5, damage = 22, cooldown = 1.5,
  radius = 9,
  gate = function()
    -- `> 1`: ele próprio é hostil e conta-se — com `> 0` nunca despertava.
    return viber.alive_in_region(2) <= 1
  end,
  awake_toast = "Um rugido ecoa dos Picos Gelados — o ogro despertou!",
}

function on_update(dt)
  fsm.update(dt)
end
