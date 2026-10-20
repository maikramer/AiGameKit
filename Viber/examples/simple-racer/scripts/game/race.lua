-- Diretor de corrida + integração dos karts. Um controller always-active.
-- Estado em viber.game() — o HUD e as item boxes só leem/escrevem aí.
--
--@export laps: int(1..9) = 3
--@export countdown: number(0.5..10) = 3
--@export grid_s: number(-2000..50000) = 35

local phys = viber.load("lib/kart.lua")
local rivals = viber.load("game/rivals.lua")

local RAMPS = {
  { s = 1050, length = 22, height = 2.2, half = 6, lateral = 0 },
  { s = 2950, length = 22, height = 2.6, half = 6, lateral = 0 },
  { s = 4550, length = 22, height = 2.0, half = 6, lateral = 0 },
}
local PADS = {
  { s = 700, length = 10, half = 5, lateral = 0 },
  { s = 2300, length = 10, half = 5, lateral = 0 },
  { s = 3700, length = 10, half = 5, lateral = 0 },
  { s = 4750, length = 10, half = 5, lateral = 0 },
}
local GRID_ROW = 7
local GRID_COL = 2.6

local function ramp_at(s, lateral, length)
  for _, ramp in ipairs(RAMPS) do
    local t = s - ramp.s
    if t < -4 then
      t = t + length
    end
    if t >= 0 and t <= ramp.length and math.abs(lateral - ramp.lateral) <= ramp.half then
      local k = math.max(0, math.min(1, t / ramp.length))
      k = k * k * (3 - 2 * k)
      return ramp.height * k
    end
  end
  return 0
end

local function boost_at(s, lateral, length)
  for _, pad in ipairs(PADS) do
    local t = s - pad.s
    if t < -4 then
      t = t + length
    end
    if t >= 0 and t <= pad.length and math.abs(lateral - pad.lateral) <= pad.half then
      return true
    end
  end
  return false
end

local function lap_for(total, lap_length)
  if total < 0 then
    return 0
  end
  return math.floor(total / math.max(lap_length, 1)) + 1
end

local function down(a, b)
  return viber.input.down(a) or (b and viber.input.down(b))
end

local function ensure(g, id, name)
  local body = g.karts[name]
  if body then
    body.id = id
    return body
  end
  body = phys.fresh(name == "player" and "player" or "ai", 0)
  body.id = id
  body.name = name
  g.karts[name] = body
  return body
end

function on_update(dt)
  viber.own_system("locomotion")
  local g = viber.game()
  g.karts = g.karts or {}
  g.race = g.race or {
    state = "grid",
    countdown = nil,
    time = 0,
    laps = viber.prop("laps"),
    message = nil,
    message_ttl = 0,
    placed = false,
  }
  local race = g.race
  local ids = viber.group("kart") or {}
  if #ids == 0 then
    return
  end
  local probe = viber.road_at(0)
  if not probe or probe.length < 50 then
    return
  end
  local length = probe.length

  if race.message_ttl > 0 then
    race.message_ttl = race.message_ttl - dt
    if race.message_ttl <= 0 then
      race.message = nil
    end
  end
  if viber.input.pressed("r") and race.state ~= "grid" then
    race.state = "grid"
    race.time = 0
    race.placed = false
    race.message = "REINÍCIO"
    race.message_ttl = 1.4
    for _, body in pairs(g.karts) do
      body.finished = false
      body.last_s = nil
      body.speed = 0
      body.rev = 0
    end
  end

  if race.state == "grid" and not race.placed then
    table.sort(ids, function(a, b)
      local na, nb = viber.entity_name(a) or "", viber.entity_name(b) or ""
      if na == "player" then return true end
      if nb == "player" then return false end
      return na < nb
    end)
    local grid_s = viber.prop("grid_s")
    for row, id in ipairs(ids) do
      local name = viber.entity_name(id) or ("kart" .. row)
      local body = ensure(g, id, name)
      local s = grid_s - (row - 1) * GRID_ROW
      local col = (row % 2 == 1) and -GRID_COL or GRID_COL
      local at = viber.road_at(s)
      if at then
        local h = at.heading
        local px, pz = -math.cos(h), math.sin(h)
        local x, z = at.cx + px * col, at.cz + pz * col
        local surf = viber.road(x, z)
        local y = surf and surf.y or at.y
        body.heading = h
        body.speed = 0
        body.total = s
        body.lap = 0
        body.last_s = nil
        body.x, body.y, body.z = x, y, z
        viber.set_pose(x, y, z, h, id)
      end
    end
    race.placed = true
    race.state = "countdown"
    race.countdown = viber.prop("countdown")
    race.message = "PREPARADOS"
    race.message_ttl = 1.5
  end

  local launch = race.state == "grid" or race.state == "countdown"
  local racing = race.state == "racing"
  if race.state == "countdown" then
    local prev = math.ceil(race.countdown)
    race.countdown = race.countdown - dt
    if race.countdown <= 0 then
      race.state = "racing"
      race.countdown = nil
      race.time = 0
      race.message = "GO!"
      race.message_ttl = 1.2
      racing = true
      launch = false
    else
      local n = math.ceil(race.countdown)
      if n ~= prev then
        race.message = tostring(n)
        race.message_ttl = 0.9
      end
    end
  elseif racing then
    race.time = race.time + dt
  end

  local menus = viber.ui and viber.ui.is_open and viber.ui.is_open()
  local player_total = 0
  local player_body = g.karts.player
  if player_body then
    player_total = player_body.total or 0
  end

  local bodies = {}
  for _, id in ipairs(ids) do
    local name = viber.entity_name(id)
    if name then
      local body = ensure(g, id, name)
      local ok, x, y, z = viber.entity_position(id)
      if ok then
        body.x, body.y, body.z = x, y, z
      end
      body._dt = dt
      local input
      if name == "player" then
        local alive = body.spin <= 0
        input = { throttle = 0, brake = 0, steer = 0, handbrake = false, nitro = false }
        if not menus and alive then
          if down("w", "up") then input.throttle = 1 end
          if down("s", "down") then input.brake = 1 end
          local steer = 0
          if down("d", "right") then steer = steer + 1 end
          if down("a", "left") then steer = steer - 1 end
          input.steer = steer
          input.handbrake = viber.input.down("space")
          input.nitro = viber.input.down("lshift") or viber.input.down("rshift")
        end
        if g.pickup then
          body.nitro = math.min(1, body.nitro + (g.pickup.nitro or 0))
          body.turbo = math.max(body.turbo, g.pickup.turbo or 0)
          g.pickup = nil
          body.nitro_ping = true
        end
      else
        input = rivals.drive(name, body, racing, launch, race.countdown, player_total, viber.time())
      end
      body.input_brake = input.brake
      bodies[#bodies + 1] = {
        id = id,
        name = name,
        x = body.x or 0,
        z = body.z or 0,
        speed = body.speed,
        input = input,
        body = body,
      }
    end
  end

  local snaps = {}
  for _, b in ipairs(bodies) do
    snaps[#snaps + 1] = { id = b.id, x = b.x, z = b.z, speed = b.speed }
  end
  local pushes, bumped = phys.contacts(snaps)
  local crashed = false

  for _, b in ipairs(bodies) do
    local body = b.body
    body.wall_hit = false
    body.nitro_ping = false
    local nx, ny, nz, drifting = phys.step(body, b.input, dt, b.x, body.y or 0, b.z, {
      racing = racing,
      launch = launch,
      road = function(x, z) return viber.road(x, z) end,
      boost_at = function(s, lateral) return boost_at(s, lateral, length) end,
      ramp_at = function(s, lateral) return ramp_at(s, lateral, length) end,
      push = pushes[b.id],
      bumped = bumped[b.id],
    })
    body.x, body.y, body.z = nx, ny, nz
    body.drifting = drifting
    viber.set_pose(nx, ny, nz, body.heading, b.id)
    if body.wall_hit or bumped[b.id] then
      crashed = true
    end
    if racing then
      local surf = viber.road(nx, nz)
      if surf then
        local s = surf.s
        if body.last_s then
          local ds = s - body.last_s
          local half = length * 0.5
          if ds < -half then
            ds = ds + length
          elseif ds > half then
            ds = ds - length
          end
          if ds > 0 then
            body.total = body.total + math.min(ds, length * 0.25)
            body.wrong_way = math.max(0, body.wrong_way - dt * 2)
          elseif ds < -0.5 then
            body.wrong_way = body.wrong_way + dt
          end
          local lap = lap_for(body.total, length)
          if lap > body.lap then
            local lap_time = race.time - body.lap_start
            if body.lap >= 1 then
              body.last_lap = lap_time
              if not body.best_lap or lap_time < body.best_lap then
                body.best_lap = lap_time
              end
            end
            body.lap_start = race.time
            body.lap = lap
            if body.driver == "player" then
              if lap == race.laps then
                race.message = "VOLTA FINAL"
                race.message_ttl = 2.5
              elseif lap < race.laps then
                race.message = "VOLTA " .. lap
                race.message_ttl = 1.8
              end
            end
          end
        else
          body.lap_start = race.time
        end
        body.last_s = s
        if not body.finished and body.lap > race.laps then
          body.finished = true
          body.finish_time = race.time
          if body.driver == "player" then
            race.state = "finished"
            race.message = "META — P" .. (body.position or 1)
            race.message_ttl = 5
          end
        end
      end
    end
    if drifting and body.drift_charge > 0 and math.abs(body.speed) > 10 then
      body._smoke = (body._smoke or 0) - dt
      if body._smoke <= 0 then
        body._smoke = 0.08
        local fx, fz = phys.forward(body.heading)
        viber.burst("smoke", nx - fx * 1.2, ny + 0.3, nz - fz * 1.2, 6)
      end
    end
  end

  local rank = {}
  for _, b in ipairs(bodies) do
    rank[#rank + 1] = b.body
  end
  table.sort(rank, function(a, b)
    if a.finished ~= b.finished then
      return a.finished
    end
    if a.finished then
      return a.finish_time < b.finish_time
    end
    return a.total > b.total
  end)
  for i, body in ipairs(rank) do
    body.position = i
  end

  local player = g.karts.player
  if player then
    local ratio = math.max(0, math.min(1, math.abs(player.speed) / phys.STATS.top_speed))
    local pitch = 0.55 + (1.7 - 0.55) * ratio
    local vol = 0.32 * (0.55 + 0.45 * ratio)
    viber.sound_loop("engine", "vehicles/engine_rev.ogg", { pitch = pitch, volume = vol })
    local skid = (player.drifting and math.abs(player.speed) > 10) and 0.22 or 0
    viber.sound_loop("skid", "vehicles/skid.ogg", { volume = skid })
    g.player = {
      speed = player.speed,
      top_speed = phys.STATS.top_speed,
      nitro = player.nitro,
      drifting = player.drifting and true or false,
      lap = math.max(player.lap, 1),
      laps = race.laps,
      position = player.position or 1,
      total = #rank,
      time = race.time,
      best_lap = player.best_lap,
      wrong_way = player.wrong_way > 0.7,
      state = race.state,
      countdown = race.state == "countdown" and math.ceil(race.countdown) or nil,
      message = race.message,
    }
  end
  if crashed then
    viber.sound("crash")
  end
end
