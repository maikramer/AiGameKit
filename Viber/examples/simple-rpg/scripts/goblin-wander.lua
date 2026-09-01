-- goblin-wander.lua: comportamento portado do TS (a engine provê os blocos: percepção,
-- movimento com snap no terreno, máquina wander/chase, dano).
local SPEED_WANDER, SPEED_CHASE = 1.6, 3.2
local AGGRO, DEAGGRO, ATTACK_RANGE = 10, 15, 1.6
local DAMAGE, COOLDOWN = 10, 1.4
local WANDER_RADIUS = 8

local st = viber.state()
st.t = 0
local target = nil

function on_player_attack(px, pz)
  st.aggro = true
end

local function pick_target()
  local tx, tz = viber.wander_target(WANDER_RADIUS)
  target = { tx, tz }
end

function on_update(dt)
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  local dist = math.sqrt((px - x)^2 + (pz - z)^2)
  if st.aggro then dist = 0 end
  st.state = viber.next_state(st.state or "wander", dist, AGGRO, DEAGGRO)

  if st.state == "chase" then
    if dist > ATTACK_RANGE then
      viber.move_towards(px, pz, SPEED_CHASE)
      st.t = 0
    else
      viber.face_player()
      st.t = st.t + dt
      if st.t >= COOLDOWN then
        st.t = 0
        viber.damage_player(DAMAGE)
      end
    end
  else
    if target == nil then pick_target() end
    local td = math.sqrt((target[1] - x)^2 + (target[2] - z)^2)
    if td < 0.8 then
      target = nil
    else
      viber.move_towards(target[1], target[2], SPEED_WANDER)
    end
  end
end
