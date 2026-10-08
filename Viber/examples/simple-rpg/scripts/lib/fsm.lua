-- lib/fsm.lua: máquina wander↔chase partilhada dos inimigos/bosses.
-- Fatorada dos ~11 scripts que tinham o MESMO bloco copiado (steering,
-- anti-stuck, aggro-window, cadência de ataque) — a engine provê os blocos
-- (percepção, movimento com snap no terreno, `next_state`), a composição
-- vive aqui e cada criatura só passa os seus números.
--
-- COMBATE v2 (ataques LEGÍVEIS): o golpe deixou de ser dano instantâneo no
-- fim do cooldown. Agora é uma sequência com leitura e resposta:
--
--   1. WINDUP   — a criatura trava, vira-se, toca o clip `attack` e pulsa um
--                 anel de aviso no chão durante `windup` s;
--   2. IMPACTO  — o dano só entra se o herói AINDA estiver ao alcance
--                 (`range + reach`, e |Δy| curto). Recuar, dar dash [C] ou
--                 aparar [L] no instante certo resolvem o golpe;
--   3. RECOVER  — `recover` s parada depois do golpe: a janela de punição.
--
-- Atingir a criatura faz FLINCH (clip `hit`) e cancela o windup — o herói
-- que ataca primeiro ganha a troca. Chefes têm POISE (`poise`): só
-- vacilam quando o dano acumulado passa essa fração do HP máximo. Ser
-- atingido também acorda a criatura (aggro 10 s), mesmo fora do raio.
--
-- Uso no script da criatura:
--
--   local fsm = viber.load("lib/fsm.lua").new{
--     wander = 2.2, chase = 4.6,      -- m/s
--     aggro = 11, deaggro = 16,       -- histerese da FSM (m)
--     range = 1.6,                    -- alcance do ataque (m)
--     damage = 9, cooldown = 1.2,     -- golpe
--     windup = 0.4,                   -- opcional: aviso antes do impacto (s)
--     recover = 0.35,                 -- opcional: janela de punição (s)
--     radius = 7,                     -- raio do wander (m)
--     turn_rate = 9.0,                -- opcional: pivô rápido do rig
--     steering = true,                -- opcional: alvos alinhados com o rumo
--     anti_stuck = true,              -- opcional (default true): repick após 6 s preso
--     on_chase = function() viber.sound("growl") end,  -- opcional: transição
--     on_attack = function() viber.apply_status("venom", 4) end, -- golpe que ACERTA
--     on_first = function() viber.log("ativo") end,    -- 1.ª vez por entidade
--     gate = function() return true end,  -- opcional: false = dorme (boss gating)
--     awake_toast = "…",              -- opcional: toast à 1.ª passagem do gate
--     -- Chefes:
--     boss = "Bruxa do Bosque",       -- nome na barra de chefe do HUD
--     poise = 0.12,                   -- fração do HP que faz vacilar
--     special = { every = 3, windup = 1.0, radius = 4.5, mult = 1.6,
--                 at = "self" | "player", clip = "roar", color = "#ff4a2a",
--                 label = "…", on_hit = function() … end },
--     enrage = { at = 0.4, toast = "…", speed = 1.2, cooldown = 0.7 },
--   }
--   function on_player_attack(px, pz) fsm.alert() end
--   function on_update(dt) fsm.update(dt) end
--
-- O estado é POR ENTIDADE (`viber.state()`); o objeto devolvido por `new`
-- partilha a CONFIG entre instâncias do mesmo script (top-level corre 1×).

local M = {}

-- Folga de alcance no impacto (m): o golpe apanha quem estava colado e deu
-- meio passo; quem recua a sério ou dá dash sai.
local REACH_SLACK = 0.7
-- Diferença de altura máxima para o golpe (m) — sem mordidas através de
-- pisos, pontes ou encostas a pique.
local REACH_VERTICAL = 2.2
-- Cadência do anel de aviso (s): o anel expande em ~0.42 s, pulsar a 0.3 s
-- lê como "zona a encher".
local TELEGRAPH_PULSE = 0.3
-- Cor do aviso dos golpes normais (âmbar) — o especial dos chefes é vermelho.
local TELEGRAPH_COLOR = "#ffb347"
-- Stagger (s) de um flinch normal / com windup cancelado / de chefe.
local STAGGER = 0.3
local STAGGER_INTERRUPT = 0.5
local STAGGER_BOSS = 0.7
-- A barra de chefe do HUD apaga-se este tempo após o último sinal (s).
M.BOSS_BAR_TTL = 1.5

local function dist2(ax, az, bx, bz)
  return math.sqrt((ax - bx) ^ 2 + (az - bz) ^ 2)
end

function M.new(cfg)
  cfg.wander = cfg.wander or 1.6
  cfg.chase = cfg.chase or 3.2
  cfg.aggro = cfg.aggro or 10
  cfg.deaggro = cfg.deaggro or 15
  cfg.range = cfg.range or 1.6
  cfg.damage = cfg.damage or 10
  cfg.cooldown = cfg.cooldown or 1.4
  cfg.windup = cfg.windup or 0.45
  cfg.recover = cfg.recover or 0.35
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

  -- Números efetivos (o enrage acelera o chefe).
  local function chase_speed(st)
    return cfg.chase * ((st.enraged and cfg.enrage and cfg.enrage.speed) or 1.0)
  end
  local function cooldown(st)
    return cfg.cooldown * ((st.enraged and cfg.enrage and cfg.enrage.cooldown) or 1.0)
  end

  -- Dano recebido desde o último frame → flinch / poise / aggro.
  local function react_to_damage(st, hp, max)
    local lost = (st.hp or hp) - hp
    st.hp = hp
    if lost <= 0 then
      return
    end
    self.alert()
    local stagger
    if cfg.poise then
      st.poise_dmg = (st.poise_dmg or 0) + lost
      if st.poise_dmg >= cfg.poise * max then
        st.poise_dmg = 0
        stagger = STAGGER_BOSS
      end
    else
      stagger = (st.phase == "windup") and STAGGER_INTERRUPT or STAGGER
    end
    if stagger then
      st.phase, st.pt = "stagger", stagger
      viber.play_clip("hit")
    end
  end

  -- Enrage: uma vez, abaixo da fração de HP.
  local function check_enrage(st, hp, max)
    local e = cfg.enrage
    if not e or st.enraged or max <= 0 or hp / max > e.at then
      return
    end
    st.enraged = true
    viber.play_clip("roar")
    viber.sound("roar")
    viber.shake(0.35)
    local x, y, z = viber.position()
    viber.burst("fire", x, y + 1.2, z, 24)
    viber.ring(x, z, 6, "#ff3b1f")
    if e.toast then viber.toast(e.toast) end
  end

  -- Barra de chefe: sinal com TTL (o hud.lua apaga-a quando o sinal pára —
  -- um chefe morto deixa de correr o script e a barra desvanece sozinha).
  local function boss_bar(st, hp, max)
    if not cfg.boss then return end
    viber.ui.set("boss.active", true)
    viber.ui.set("boss.name", cfg.boss)
    viber.ui.set("boss", max > 0 and hp / max or 0)
    viber.ui.set("boss.enraged", st.enraged == true)
    viber.game().boss_bar_t = viber.time()
  end

  -- Começa um golpe: normal ou, a cada `special.every`, o especial.
  local function begin_attack(st, px, pz)
    st.swings = (st.swings or 0) + 1
    local sp = cfg.special
    if sp and st.swings % (sp.every or 3) == 0 then
      st.phase, st.pt, st.kind = "windup", sp.windup or 1.0, "special"
      local x, _, z = viber.position()
      if sp.at == "player" then
        st.ax, st.az = px, pz -- a zona fica onde o herói ESTAVA: sai dela
      else
        st.ax, st.az = x, z
      end
      viber.play_clip(sp.clip or "roar,attack")
      if sp.label then viber.toast(sp.label) end
      viber.sound("roar")
    else
      st.phase, st.pt, st.kind = "windup", cfg.windup, "normal"
      viber.play_clip("attack")
    end
    st.pulse = 0
  end

  -- Resolve o impacto do golpe em curso.
  local function land_attack(st)
    local has, px, py, pz = viber.player_position()
    if not has then return end
    local x, y, z = viber.position()
    if st.kind == "special" then
      local sp = cfg.special
      local r = sp.radius or 4.5
      viber.ring(st.ax, st.az, r, sp.color or "#ff4a2a")
      viber.burst("ground-dust", st.ax, y + 0.2, st.az, 26)
      viber.shake(0.4)
      viber.sound("hit")
      if dist2(px, pz, st.ax, st.az) <= r and math.abs(py - y) <= REACH_VERTICAL + 1.0 then
        viber.damage_player(cfg.damage * (sp.mult or 1.6))
        if sp.on_hit then sp.on_hit() end
      else
        viber.damage_number("ESQUIVA", { color = "#9fd8ff", x = px, y = py + 2.0, z = pz })
      end
    else
      if dist2(px, pz, x, z) <= cfg.range + REACH_SLACK and math.abs(py - y) <= REACH_VERTICAL then
        viber.damage_player(cfg.damage)
        if cfg.on_attack then cfg.on_attack() end
      else
        viber.sound("whoosh")
        viber.damage_number("ESQUIVA", { color = "#9fd8ff", x = px, y = py + 2.0, z = pz })
      end
    end
  end

  -- Fase de ataque ativa (windup/recover/stagger): devolve true se consumiu
  -- o frame (a criatura não anda nem reavalia a FSM).
  local function tick_phase(st, dt)
    if not st.phase then return false end
    st.pt = st.pt - dt
    if st.phase == "windup" then
      if st.kind == "special" then
        -- Aviso pulsado da zona; a criatura fica parada a olhar o herói.
        st.pulse = st.pulse - dt
        if st.pulse <= 0 then
          st.pulse = TELEGRAPH_PULSE
          viber.ring(st.ax, st.az, cfg.special.radius or 4.5, cfg.special.color or "#ff4a2a")
        end
      else
        st.pulse = st.pulse - dt
        if st.pulse <= 0 then
          st.pulse = TELEGRAPH_PULSE
          local x, _, z = viber.position()
          viber.ring(x, z, cfg.range + 0.3, TELEGRAPH_COLOR)
        end
      end
      viber.face_player()
      if st.pt <= 0 then
        land_attack(st)
        st.phase, st.pt = "recover", cfg.recover
        st.t = 0
      end
    elseif st.pt <= 0 then
      st.phase = nil
    end
    return true
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
    local x, _, z = viber.position()

    local ok, hp, max = viber.entity_hp()
    if ok then
      if hp <= 0 then return end
      react_to_damage(st, hp, max)
      check_enrage(st, hp, max)
    end

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

    if tick_phase(st, dt) then
      if ok and st.state == "chase" then boss_bar(st, hp, max) end
      return
    end

    local dist = dist2(px, pz, x, z)
    -- A janela de aggro força só a FSM a chase (o golpe mede a distância REAL).
    local fsm_dist = dist
    if (st.aggro_until or 0) > viber.time() then fsm_dist = 0 end
    st.state = viber.next_state(st.state or "wander", fsm_dist, cfg.aggro, cfg.deaggro)

    -- Hook na TRANSIÇÃO wander → chase (não repete por tick).
    if st.state == "chase" and st.pstate ~= "chase" and cfg.on_chase then
      cfg.on_chase()
    end
    st.pstate = st.state

    if st.state == "chase" then
      if ok then boss_bar(st, hp, max) end
      if dist > cfg.range then
        viber.move_towards(px, pz, chase_speed(st))
        -- Ao chegar ao alcance, o 1.º golpe sai a meio da cadência (não
        -- depois de uma cadência inteira parada a olhar).
        st.t = cooldown(st) * 0.55
      else
        viber.face_player()
        st.t = (st.t or 0) + dt
        if st.t >= cooldown(st) then
          st.t = 0
          begin_attack(st, px, pz)
        end
      end
    else
      if st.target == nil then pick_target(st) end
      local td = dist2(st.target[1], st.target[2], x, z)
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
