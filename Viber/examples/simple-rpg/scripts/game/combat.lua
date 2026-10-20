-- Melee do herói [J] e ciclo de alvo [V].
-- As criaturas do spawner não têm nome: find_all("wolf") não via nenhuma.
-- viber.hostiles lista quem tem script em enemies/ ou bosses/.

local REACH = 2.4
local ARC = 0.7
local VERTICAL = 2.2
local DAMAGE = 18
local COOLDOWN = 0.45
local CYCLE_RANGE = 14

local function dot(ax, az, bx, bz)
  return ax * bx + az * bz
end

local function show_target(id, kind, hp, max_hp)
  viber.ui.set("target.name", kind or "")
  if max_hp and max_hp > 0 then
    viber.ui.set("target", math.max(0, hp or 0) / max_hp)
    viber.ui.set("target.alive", (hp or 0) > 0)
  end
end

function on_update(dt)
  local st = viber.state()
  st.cd = math.max(0, (st.cd or 0) - dt)
  if viber.menu_open() then
    return
  end
  local alive, hp = viber.player_hp()
  if alive and hp <= 0 then
    return
  end
  local has, px, py, pz = viber.player_position()
  if not has then
    return
  end

  if viber.input.pressed("v") then
    local list = viber.hostiles(CYCLE_RANGE) or {}
    local ids = {}
    for _, h in ipairs(list) do
      if math.abs((h.y or py) - py) <= VERTICAL then
        ids[#ids + 1] = h
      end
    end
    if #ids == 0 then
      st.target = nil
      viber.ui.set("target.alive", false)
      viber.ui.set("target.name", "")
    else
      local at = 1
      for i, h in ipairs(ids) do
        if h.id == st.target then
          at = i % #ids + 1
          break
        end
      end
      local pick = ids[at]
      st.target = pick.id
      show_target(pick.id, pick.kind, pick.hp, pick.max)
    end
  end

  if not viber.input.pressed("j") and not viber.input.pressed("mouse1") then
    return
  end
  if st.cd > 0 then
    return
  end
  local fx, fz = viber.player_forward()
  st.cd = COOLDOWN
  viber.play_clip("attack", { id = viber.player_id() })
  viber.sound("whoosh")
  local best, best_d
  for _, h in ipairs(viber.hostiles(REACH) or {}) do
    if math.abs((h.y or py) - py) <= VERTICAL then
      local dx, dz = h.x - px, h.z - pz
      local dist = math.sqrt(dx * dx + dz * dz)
      if dist <= REACH and dist > 0.05 then
        local nd = dot(fx, fz, dx / dist, dz / dist)
        if nd >= ARC and (not best_d or dist < best_d) then
          best = h
          best_d = dist
        end
      end
    end
  end
  if best then
    viber.entity_damage(DAMAGE, best.id)
    viber.damage_number("-" .. DAMAGE, { x = best.x, y = best.y + 1.6, z = best.z })
    viber.hit_stop(0.06)
    viber.shake(0.2)
    viber.sound("hit")
    st.target = best.id
    local left = math.max(0, (best.hp or 0) - DAMAGE)
    show_target(best.id, best.kind, left, best.max)
  end
end
