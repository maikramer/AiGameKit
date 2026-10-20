-- Física de kart arcade. Port literal das constantes de vehicle.rs.
-- O diretor (game/race.lua) integra; este módulo não toca no mundo.

local M = {}

M.GRAVITY = 22.0
M.DRAG = 0.0016
M.ROLLING = 1.6
M.STEER_FALLOFF = 26.0
M.DRIFT_MIN_SPEED = 12.0
M.MINI_TURBO_CHARGE = 1.0
M.SUPER_TURBO_CHARGE = 2.1
M.MINI_TURBO_TIME = 0.85
M.SUPER_TURBO_TIME = 1.6
M.OFFROAD_GRIP = 0.42
M.TRACK_SHOULDER = 3.5
M.WALL_RANGE = 30.0
M.KART_RADIUS = 1.4
M.ROCKET_REV = 0.72

M.STATS = {
  top_speed = 52,
  accel = 27,
  brake = 52,
  engine_brake = 7,
  max_steer = 2.7,
  steer_speed = 11,
  grip = 7.5,
  drift_grip = 0.3,
  boost = 2.6,
  boost_recharge = 0.28,
}

function M.forward(heading)
  return math.sin(heading), math.cos(heading)
end

function M.wrap_angle(err)
  local pi = math.pi
  while err > pi do
    err = err - pi * 2
  end
  while err < -pi do
    err = err + pi * 2
  end
  return err
end

-- Empurrão circular entre karts. `bodies` = { {id, x, z, speed}, ... }.
-- Devolve pushes[id] = {x, z} e a lista de ids que bateram forte.
function M.contacts(bodies)
  local pushes = {}
  local bumped = {}
  local min = M.KART_RADIUS * 2
  for i = 1, #bodies do
    for j = i + 1, #bodies do
      local a, b = bodies[i], bodies[j]
      local dx, dz = b.x - a.x, b.z - a.z
      local dist = math.sqrt(dx * dx + dz * dz)
      if dist < min and dist > 1e-4 then
        local scale = ((min - dist) * 0.5) / dist
        local hx, hz = dx * scale, dz * scale
        local pa = pushes[a.id] or { x = 0, z = 0 }
        local pb = pushes[b.id] or { x = 0, z = 0 }
        pa.x, pa.z = pa.x - hx, pa.z - hz
        pb.x, pb.z = pb.x + hx, pb.z + hz
        pushes[a.id], pushes[b.id] = pa, pb
        if math.abs(a.speed - b.speed) > 10 then
          bumped[a.id] = true
          bumped[b.id] = true
        end
      end
    end
  end
  return pushes, bumped
end

-- Um passo. `kart` é mutado. Devolve x, y, z, drifting.
-- `road(x, z)` e `sample(x, z)` vêm do diretor (closures sobre viber.*).
function M.step(kart, input, dt, x, y, z, opts)
  local stats = M.STATS
  local racing = opts.racing
  local launch = opts.launch
  local hold = launch and not racing

  if launch and kart.driver == "player" then
    if input.throttle > 0 then
      kart.rev = math.min(1, kart.rev + 0.9 * dt)
    else
      kart.rev = math.max(0, kart.rev - 1.8 * dt)
    end
    if kart.rev >= 1 and kart.wheelspin <= 0 then
      kart.wheelspin = 1.4
    end
  elseif not launch and kart.rev > 0 then
    if kart.rev >= M.ROCKET_REV and kart.wheelspin <= 0 then
      kart.speed = math.max(kart.speed, 6.5)
      kart.turbo = 1.15
      kart.turbo_super = false
    end
    kart.rev = 0
  end

  local fx, fz = M.forward(kart.heading)
  local surface = opts.road(x, z)
  local ground_y = surface and surface.y or y
  local on_track = surface and surface.on
  local lateral = surface and surface.lateral or 0
  kart.boost_cd = math.max(0, kart.boost_cd - dt)
  if surface and opts.boost_at and opts.boost_at(surface.s, lateral) and kart.boost_cd <= 0 then
    kart.boost_cd = 2
    kart.turbo = math.max(kart.turbo, 1.3)
    kart.turbo_super = false
    kart.nitro = math.min(1, kart.nitro + 0.35)
    kart.nitro_ping = true
  end
  local grip_mult = on_track and 1 or M.OFFROAD_GRIP

  local top, accel = stats.top_speed, stats.accel
  if kart.turbo > 0 then
    accel = accel * 1.5
    if kart.turbo_super then
      top = top * 1.18
    end
  end
  local nitro_active = input.nitro and kart.nitro > 0.02
  kart.nitro_active = nitro_active
  if nitro_active then
    kart.nitro = math.max(0, kart.nitro - 0.35 * dt)
    accel = accel * stats.boost
    top = top * 1.15
  elseif racing then
    kart.nitro = math.min(1, kart.nitro + stats.boost_recharge * dt)
  end

  local speed = kart.speed
  local headroom = math.max(0, 1 - speed / top)
  local resist = M.DRAG * speed * speed + M.ROLLING
  local drive = accel * headroom * input.throttle * grip_mult * 0.55 + resist * input.throttle
  if input.brake > 0 then
    if speed > 0.5 then
      drive = drive - stats.brake * input.brake * 0.55
    else
      drive = -20 * input.brake * grip_mult * 0.55 - resist * input.brake * 0.5
    end
  end
  if input.throttle <= 0 and input.brake <= 0 and math.abs(speed) > 0.1 then
    drive = drive - (speed > 0 and 1 or -1) * stats.engine_brake * 0.34
  end
  if hold then
    drive = 0
  end
  if surface then
    local ax, az = x + fx * 4, z + fz * 4
    local bx, bz = x - fx * 4, z - fz * 4
    local ahead = opts.road(ax, az)
    local behind = opts.road(bx, bz)
    local ahead_y = ahead and ahead.y or ground_y
    local behind_y = behind and behind.y or ground_y
    local slope = (ahead_y - behind_y) / 8
    drive = drive - M.GRAVITY * slope * 0.5
  end
  local new_speed = speed + drive * dt
  if math.abs(speed) < 0.15 and input.throttle <= 0 and input.brake <= 0 then
    new_speed = 0
  end
  new_speed = math.max(-12, math.min(top, new_speed))
  if kart.airborne then
    new_speed = speed
  end

  local falloff = M.STEER_FALLOFF / (M.STEER_FALLOFF + math.abs(speed))
  local sign = new_speed >= 0 and 1 or -1
  local yaw_rate = -input.steer * stats.max_steer * falloff * sign
  local steer_lerp = math.max(0, math.min(1, stats.steer_speed * dt))
  kart.steer = kart.steer + (input.steer - kart.steer) * steer_lerp
  local drifting = input.handbrake and math.abs(new_speed) > M.DRIFT_MIN_SPEED and not kart.airborne
  if drifting then
    kart.drift_charge = kart.drift_charge + dt
    local target = -kart.steer * 0.42
    local rate = 2.5 * math.max(0.2, 1 - stats.drift_grip)
    local delta = math.max(-rate * dt, math.min(rate * dt, target - kart.slip))
    kart.slip = math.max(-0.55, math.min(0.55, kart.slip + delta))
  else
    if kart.drift_charge > M.MINI_TURBO_CHARGE then
      if kart.drift_charge >= M.SUPER_TURBO_CHARGE then
        kart.turbo_super = true
        kart.turbo = M.SUPER_TURBO_TIME
      else
        kart.turbo_super = false
        kart.turbo = M.MINI_TURBO_TIME
      end
    end
    kart.drift_charge = 0
    local decay = math.max(0, math.min(1, stats.grip * dt * 2))
    kart.slip = kart.slip * (1 - decay)
  end
  if not kart.airborne then
    kart.heading = kart.heading + yaw_rate * dt
  end
  if kart.turbo > 0 then
    kart.turbo = math.max(0, kart.turbo - dt)
  end
  if kart.spin > 0 then
    kart.spin = math.max(0, kart.spin - dt)
    new_speed = new_speed * 0.9
  end
  if kart.wheelspin > 0 then
    kart.wheelspin = math.max(0, kart.wheelspin - dt)
    new_speed = new_speed * 0.55
  end

  local mx, mz = M.forward(kart.heading - kart.slip)
  local nx, nz = x + mx * new_speed * dt, z + mz * new_speed * dt
  local wall = opts.road(nx, nz)
  if wall then
    local limit = wall.half_width + M.TRACK_SHOULDER
    local dist = math.abs(wall.lateral)
    if dist > limit and dist < limit + M.WALL_RANGE then
      local clamped = math.max(-limit, math.min(limit, wall.lateral))
      local px, pz = -math.cos(wall.heading), math.sin(wall.heading)
      nx = wall.cx + px * clamped
      nz = wall.cz + pz * clamped
      local vx, vz = mx * new_speed, mz * new_speed
      local frontal = math.max(0, -(vx * px + vz * pz) * (wall.lateral > 0 and 1 or -1))
      if frontal > 6 then
        new_speed = new_speed * 0.55
        kart.wall_hit = true
      else
        new_speed = new_speed * 0.995
      end
    end
  end
  local push = opts.push
  if push then
    nx, nz = nx + push.x, nz + push.z
  end
  if opts.bumped then
    new_speed = new_speed * 0.9
  end

  local ramp_here, ramp_next = 0, 0
  if surface and opts.ramp_at then
    ramp_here = opts.ramp_at(surface.s, lateral)
    local next_surf = opts.road(nx, nz)
    if next_surf then
      ramp_next = opts.ramp_at(next_surf.s, next_surf.lateral)
    end
  end
  local next_ground = opts.road(nx, nz)
  local gy = (next_ground and next_ground.y or ground_y) + ramp_next
  if kart.airborne then
    kart.air_vy = kart.air_vy - M.GRAVITY * dt
    local ny = y + kart.air_vy * dt
    if ny <= gy then
      ny = gy
      kart.airborne = false
      if kart.air_vy < -8 then
        new_speed = new_speed * 0.9
      end
      kart.air_vy = 0
    end
    y = ny
  else
    local climb = (gy - (ground_y + ramp_here)) / math.max(dt, 1e-4)
    if climb < -3.5 and new_speed > 8 then
      kart.airborne = true
      kart.air_vy = math.max(0, math.min(14, kart.prev_climb))
    else
      y = gy
    end
    kart.prev_climb = kart.prev_climb * 0.8 + climb * 0.2
  end
  kart.speed = new_speed
  kart.drifting = drifting
  return nx, y, nz, drifting
end

function M.fresh(driver, heading)
  return {
    driver = driver,
    speed = 0,
    slip = 0,
    heading = heading or 0,
    steer = 0,
    nitro = 0.5,
    nitro_active = false,
    turbo = 0,
    turbo_super = false,
    drift_charge = 0,
    airborne = false,
    air_vy = 0,
    prev_climb = 0,
    spin = 0,
    rev = 0,
    wheelspin = 0,
    stuck = 0,
    boost_cd = 0,
    lap = 0,
    total = 0,
    last_s = nil,
    lap_start = 0,
    last_lap = nil,
    best_lap = nil,
    finished = false,
    finish_time = 0,
    position = 1,
    wrong_way = 0,
  }
end

return M
