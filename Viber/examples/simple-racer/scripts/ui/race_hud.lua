-- HUD de corrida: tudo o que aparece vem do snapshot `viber.race.*` /
-- `viber.vehicle.*` (escrito pela engine a cada frame). O script só formata
-- e decide visibilidades — nenhuma lógica de corrida vive aqui.

local st = viber.state()
if not st.ready then
  st.ready = true
end

local function fmt_time(t)
  if not t or t <= 0 then
    return "—"
  end
  local m = math.floor(t / 60)
  local s = t - m * 60
  return string.format("%d:%04.1f", m, s)
end

local banner_ttl = 0
local banner_text = ""

function on_update(dt)
  local state = viber.race.state()
  if not state then
    return
  end

  -- ── speedómetro + nitro ────────────────────────────────────────────
  local speed = viber.vehicle.speed()
  viber.ui.set_text("speed-kmh", string.format("%d", math.floor(speed * 3.6 + 0.5)))
  viber.ui.set_value("nitro-bar", viber.vehicle.nitro())

  -- ── volta / posição / cronómetros ──────────────────────────────────
  local lap, laps = viber.race.lap()
  viber.ui.set_text("race-lap", string.format("%d/%d", math.max(lap, 1), laps))
  local pos, total = viber.race.position()
  viber.ui.set_text("race-pos", string.format("%d/%d", pos, total))
  viber.ui.set_text("race-time", fmt_time(viber.race.time()))
  viber.ui.set_text("race-best", fmt_time(viber.race.best_lap()))

  -- ── banner central ─────────────────────────────────────────────────
  local text, warn = nil, false
  local cd = viber.race.countdown()
  if cd then
    text = tostring(cd)
  elseif state == "countdown" then
    text = "PREPARADOS"
  elseif state == "finished" then
    text = viber.race.message() or "META"
  else
    local msg = viber.race.message()
    if msg then
      text = msg
      banner_ttl = 1.0
      banner_text = msg
    elseif viber.race.wrong_way() then
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
