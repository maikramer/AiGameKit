-- HUD de corrida. O diretor escreve viber.game().player; este script formata.

local banner_ttl = 0
local banner_text = ""

local function fmt_time(t)
  if not t or t <= 0 then
    return "—"
  end
  local m = math.floor(t / 60)
  local s = t - m * 60
  return string.format("%d:%04.1f", m, s)
end

function on_update(dt)
  local player = viber.game().player
  if not player then
    return
  end

  viber.ui.set_text("speed-kmh", string.format("%d", math.floor(player.speed * 3.6 + 0.5)))
  viber.ui.set_value("nitro-bar", player.nitro or 0)

  viber.ui.set_text("race-lap", string.format("%d/%d", player.lap or 1, player.laps or 1))
  viber.ui.set_text("race-pos", string.format("%d/%d", player.position or 1, player.total or 1))
  viber.ui.set_text("race-time", fmt_time(player.time))
  viber.ui.set_text("race-best", fmt_time(player.best_lap))

  local text, warn = nil, false
  if player.countdown then
    text = tostring(player.countdown)
  elseif player.state == "countdown" then
    text = "PREPARADOS"
  elseif player.state == "finished" then
    text = player.message or "META"
  else
    local msg = player.message
    if msg then
      text = msg
      banner_ttl = 1.0
      banner_text = msg
    elseif player.wrong_way then
      text, warn = "SENTIDO ERRADO", true
    elseif banner_ttl > 0 then
      banner_ttl = banner_ttl - dt
      text = banner_text
    end
  end
  viber.ui.set_visible("banner", text ~= nil)
  if text then
    viber.ui.set_text("banner-text", text)
    viber.ui.toggle_class("banner", "warn", warn)
  end
end
