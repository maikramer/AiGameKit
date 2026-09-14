-- lib/fsm.lua: máquina wander↔chase partilhada dos inimigos/bosses.
-- Fatorada dos ~11 scripts que tinham o MESMO bloco copiado (steering,
-- anti-stuck, aggro-window, cadência de ataque) — a engine provê os blocos
-- (percepção, movimento com snap no terreno, `next_state`), a composição
-- vive aqui e cada criatura só passa os seus números.
--
-- Uso no script da criatura:
--
--   local fsm = viber.load("lib/fsm.lua").new{
--     wander = 2.2, chase = 4.6,      -- m/s
--     aggro = 11, deaggro = 16,       -- histerese da FSM (m)
--     range = 1.6,                    -- alcance do ataque (m)
--     damage = 9, cooldown = 1.2,     -- golpe
--     radius = 7,                     -- raio do wander (m)
--     turn_rate = 9.0,                -- opcional: pivô rápido do rig
--     steering = true,                -- opcional: alvos alinhados com o rumo
--     anti_stuck = true,              -- opcional (default true): repick após 6 s preso
--     on_chase = function() viber.sound("growl") end,  -- opcional: transição
--     on_attack = function() viber.apply_status("venom", 4) end, -- após o golpe
--     on_first = function() viber.log("ativo") end,    -- 1.ª vez por entidade
--     gate = function() return true end,  -- opcional: false = dorme (boss gating)
--     awake_toast = "…",              -- opcional: toast à 1.ª passagem do gate
--   }
--   function on_player_attack(px, pz) fsm.alert() end
--   function on_update(dt) fsm.update(dt) end
--
-- O estado é POR ENTIDADE (`viber.state()`); o objeto devolvido por `new`
-- partilha a CONFIG entre instâncias do mesmo script (top-level corre 1×).

local M = {}

function M.new(cfg)
  cfg.wander = cfg.wander or 1.6
  cfg.chase = cfg.chase or 3.2
  cfg.aggro = cfg.aggro or 10
  cfg.deaggro = cfg.deaggro or 15
  cfg.range = cfg.range or 1.6
  cfg.damage = cfg.damage or 10
  cfg.cooldown = cfg.cooldown or 1.4
  cfg.radius = cfg.radius or 8
  if cfg.anti_stuck == nil then cfg.anti_stuck = true end

  local self = { cfg = cfg }

  -- aggro-chain (loop 6): o herói acertou um aliado — força chase 10 s
  -- (desarma sozinho; o ataque usa a distância REAL, não a janela).
  function self.alert()
    viber.state().aggro_until = viber.time() + 10
  end

  local function pick_target(st)
    if not cfg.steering then
      local tx, tz = viber.wander_target(cfg.radius)
      st.target = { tx, tz }
      return
    end
    -- Steering: entre vários alvos válidos, escolhe o mais alinhado com o
    -- rumo atual — menos meia-volta, menos "andar de caranguejo".
    local x, _, z = viber.position()
    local hx, hz = st.hx or 0.0, st.hz or 1.0
    local best, best_dot
    for _ = 1, 6 do
      local tx, tz = viber.wander_target(cfg.radius)
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

  function self.update(dt)
    local st = viber.state() -- POR ENTIDADE (no top-level partilhava entre instâncias)
    if cfg.gate and not st.awake then
      if not cfg.gate() then
        return
      end
      st.awake = true
      if cfg.awake_toast then
        viber.toast(cfg.awake_toast)
      end
    end
    if cfg.on_first and not st.first_done then
      st.first_done = true
      cfg.on_first()
    end
    local has, px, _, pz = viber.player_position()
    if not has then return end
    local x, y, z = viber.position()
    -- Nominais + taxa de viragem do rig (1.ª vez só; desliga auto-calibração).
    if cfg.turn_rate and not st.loco then
      st.loco = true
      viber.set_locomotion(cfg.wander, cfg.chase, cfg.turn_rate)
    end
    -- Rumo atual (para o steering do próximo alvo).
    if cfg.steering then
      if st.px then
        local dx, dz = x - st.px, z - st.pz
        local len = math.sqrt(dx * dx + dz * dz)
        if len > 1e-3 then st.hx, st.hz = dx / len, dz / len end
      end
      st.px, st.pz = x, z
    end
    local dist = math.sqrt((px - x)^2 + (pz - z)^2)
    -- A janela de aggro força só a FSM a chase (damage_player não tem range
    -- check — dist=0 mordia de qualquer sítio).
    local fsm_dist = dist
    if (st.aggro_until or 0) > viber.time() then fsm_dist = 0 end
    st.state = viber.next_state(st.state or "wander", fsm_dist, cfg.aggro, cfg.deaggro)

    -- Hook na TRANSIÇÃO wander → chase (não repete por tick).
    if st.state == "chase" and st.pstate ~= "chase" and cfg.on_chase then
      cfg.on_chase()
    end
    st.pstate = st.state

    if st.state == "chase" then
      if dist > cfg.range then
        viber.move_towards(px, pz, cfg.chase)
        st.t = 0
      else
        viber.face_player()
        st.t = (st.t or 0) + dt
        if st.t >= cfg.cooldown then
          st.t = 0
          viber.damage_player(cfg.damage)
          if cfg.on_attack then
            cfg.on_attack()
          end
        end
      end
    else
      if st.target == nil then pick_target(st) end
      local td = math.sqrt((st.target[1] - x)^2 + (st.target[2] - z)^2)
      if td < 0.8 then
        st.target = nil
        st.stuck, st.last_td = 0, nil -- chegou: limpa o anti-stuck
      elseif cfg.anti_stuck then
        -- anti-stuck: td sem diminuir = preso num collider; repick após ~6 s
        if st.last_td ~= nil and td >= st.last_td then
          st.stuck = (st.stuck or 0) + dt
          if st.stuck > 6 then
            pick_target(st)
            st.stuck, st.last_td = 0, nil
          end
        else
          st.stuck = 0
        end
        st.last_td = td
        viber.move_towards(st.target[1], st.target[2], cfg.wander)
      else
        viber.move_towards(st.target[1], st.target[2], cfg.wander)
      end
    end
  end

  return self
end

return M
