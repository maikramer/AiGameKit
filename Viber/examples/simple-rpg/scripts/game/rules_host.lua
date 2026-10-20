-- Controller sempre ativo: veneno, eventos de morte, binds do HUD.
local rules = viber.load("lib/rules.lua")

function on_update(dt)
  rules.tick(dt)
end
