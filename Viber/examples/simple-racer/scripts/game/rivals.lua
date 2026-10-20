-- Rivais: pure-pursuit sobre a linha (`viber.road_at`). Escreve o input
-- do kart; a física é a mesma do jogador.

local kart = viber.load("lib/kart.lua")

local M = {}

local LOOK_BASE = 8
local LOOK_PER_SPEED = 0.35
local LAT_ACCEL = 20
local RUBBER_NEAR = 0.92
local RUBBER_FAR = 1.12
local STUCK_SPEED = 1.5
local STUCK_TIME = 3
local RECOVER_TIME = 1.2

local function skill_of(name)
  if name:find("vega") then return 0.94 end
  if name:find("koda") then return 0.87 end
  if name:find("mira") then return 0.8 end
  if name:find("rook") then return 0.74 end
  return 0.8
end

local function line_of(name)
  if name:find("vega") then return -1.4 end
  if name:find("koda") then return 0.8 end
  if name:find("mira") then return -0.6 end
  if name:find("rook") then return 1.6 end
  return 0
end

local function curvature_ahead(s, dist)
  local worst = 0
  local step = 4
  local prev = viber.road_at(s)
  if not prev then
    return 0
  end
  local walked = 0
  while walked < dist do
    walked = walked + step
    local here = viber.road_at(s + walked)
    if not here then
      break
    end
    local dh = math.abs(kart.wrap_angle(here.heading - prev.heading))
    worst = math.max(worst, dh / step)
    prev = here
  end
  return math.min(worst, 1)
end

function M.drive(name, body, racing, launch, countdown, player_total, elapsed)
  local input = { throttle = 0, brake = 0, steer = 0, handbrake = false, nitro = false }
  if name == "player" then
    return input
  end
  local skill = skill_of(name)
  if not racing then
    if launch and countdown and countdown < 1.8 + (1 - skill) then
      input.throttle = 1
    end
    return input
  end
  if body.speed < STUCK_SPEED and body.input_brake <= 0 then
    body.stuck = body.stuck + (body._dt or 0)
  elseif math.abs(body.speed) > STUCK_SPEED then
    body.stuck = 0
  end
  if body.stuck > STUCK_TIME then
    if body.stuck > STUCK_TIME + RECOVER_TIME then
      body.stuck = 0
      return input
    end
    input.brake = 1
    input.steer = -(body.steer or 0)
    return input
  end
  local s = body.last_s or 0
  local look = math.max(8, math.min(30, LOOK_BASE + body.speed * LOOK_PER_SPEED))
  local ahead = viber.road_at(s + look)
  if not ahead then
    return input
  end
  local h = ahead.heading
  local px, pz = -math.cos(h), math.sin(h)
  local off = line_of(name)
  local tx, tz = ahead.cx + px * off, ahead.cz + pz * off
  local desired = math.atan2(tx - body.x, tz - body.z)
  local err = kart.wrap_angle(desired - body.heading)
  local steer = math.max(-1, math.min(1, -err * 2.2))
  local brake_dist = math.max(15, math.min(90, body.speed * body.speed / (2 * kart.STATS.brake) + 10))
  local kappa = curvature_ahead(s + 5, brake_dist)
  local v_corner = kart.STATS.top_speed
  if kappa > 1e-4 then
    v_corner = math.sqrt(LAT_ACCEL * (0.75 + 0.25 * skill) / kappa)
  end
  local gap = player_total - body.total
  local rubber = math.max(RUBBER_NEAR, math.min(RUBBER_FAR, 1 + gap * 0.004))
  local target = math.min(kart.STATS.top_speed * skill * rubber, v_corner)
  if body.speed < target - 0.5 then
    input.throttle = 1
  end
  if body.speed > target + 1.5 then
    input.brake = 1
  end
  local sharp = curvature_ahead(s + 3, 15)
  input.handbrake = sharp > 0.02 and body.speed > kart.DRIFT_MIN_SPEED and body.speed < 26
  local straight = curvature_ahead(s + 5, 40) < 0.004
  input.nitro = straight and body.nitro > 0.6 and kappa < 0.004
  if skill < 0.85 and sharp > 0.01 and (elapsed * 0.37 + body.total * 0.01) % 1 < 0.04 then
    steer = steer * 0.6
  end
  input.steer = steer
  return input
end

return M
