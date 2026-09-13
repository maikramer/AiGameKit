-- enemies/scorpion.lua: comportamento portado do TS (a engine provê os blocos: percepção,
-- movimento com snap no terreno, máquina wander/chase, dano).
local SPEED_WANDER, SPEED_CHASE = 1.2, 3.4
local AGGRO, DEAGGRO, ATTACK_RANGE = 9, 14, 1.7
local DAMAGE, COOLDOWN = 11, 1.8
local WANDER_RADIUS = 5
-- Pivô rápido: sem o deslize lateral nas viragens do wander (o gait de 4
-- patas expõe o corpo a apontar para fora do rumo durante a viragem).
local TURN_RATE = 9.0

local function pick_target(st)
  -- Steering: alvo do wander mais alinhado com o rumo atual — menos
  -- meia-volta, menos "andar de caranguejo".
  local x, _, z = viber.position()
  local hx, hz = st.hx or 0.0, st.hz or 1.0
  local best, best_dot
  for _ = 1, 6 do
    local tx, tz = viber.wander_target(WANDER_RADIUS)
    local dx, dz = tx - x, tz - z
    local len = math.sqrt(dx * dx + dz * dz)
    if len > 0.5 then
      local dot = (dx * hx + dz * hz) / len
      if not best_dot or dot > best_dot then
        best, best_dot = { tx, tz }, dot
      end
    end
  end
  st.target = best
end

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE: no top-level partilhava entre instâncias
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  -- Nominais + taxa de viragem do rig (1.ª vez só; desliga auto-calibração).
  if not st.loco then
    st.loco = true
    viber.set_locomotion(SPEED_WANDER, SPEED_CHASE, TURN_RATE)
  end
  -- Rumo atual (para o steering do próximo alvo).
  if st.px then
    local dx, dz = x - st.px, z - st.pz
    local len = math.sqrt(dx * dx + dz * dz)
    if len > 1e-3 then st.hx, st.hz = dx / len, dz / len end
  end
  st.px, st.pz = x, z
  local dist = math.sqrt((px - x)^2 + (pz - z)^2)
  st.state = viber.next_state(st.state or "wander", dist, AGGRO, DEAGGRO)

  if st.state == "chase" then
    if dist > ATTACK_RANGE then
      viber.move_towards(px, pz, SPEED_CHASE)
      st.t = 0
    else
      viber.face_player()
      st.t = (st.t or 0) + dt
      if st.t >= COOLDOWN then
        st.t = 0
        viber.damage_player(DAMAGE)
        viber.apply_status("venom", 4) -- veneno 4 s (dá uso ao antídoto [2])
      end
    end
  else
    if st.target == nil then pick_target(st) end
    local td = math.sqrt((st.target[1] - x)^2 + (st.target[2] - z)^2)
    if td < 0.8 then
      st.target = nil
    else
      viber.move_towards(st.target[1], st.target[2], SPEED_WANDER)
    end
  end
end
