-- ui/hud.lua: o HUD é alimentado POR LUA (viber.ui.set_text) — não há binds
-- engine (`health`, `gold`, …) porque este mundo não tem vitais nem vault.
local g = viber.game()

function on_update(dt)
  local score = viber.game().score or 0
  local total = viber.game().total or 0
  viber.ui.set_text("score", score .. " / " .. total)
end
