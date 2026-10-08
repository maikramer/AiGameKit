-- Driver do PROFILER [P] (UiRoot id="profiler", painel em world/profiler.xml,
-- pintura em ui/profiler.css).
--
-- Contract: a engine publica um snapshot a ~4 Hz enquanto o modal está aberto
-- (`viber.profiler()` → tabela ou nil fechado); este script espelha o estado
-- para a UI (abas/classes/textos/listas) e devolve cliques como comandos
-- (`viber.profiler_cmd`). Teclas F5/F12/Pause/`/PageUp/PageDown são
-- engine-side — o P é do modal declarativo (key="p", toggle nativo).
--
-- Tudo a 5 Hz (CADENCE): o passe de entidades do snapshot é o custo real;
-- o script nem acorda o GC com o modal fechado.

local CADENCE = 0.2
local ACC = 1.0 -- >CADENCE: preenche no primeiro frame com o modal aberto
local LAST_STATUS = nil -- só reescreve o rodapé quando muda
local LAST_UI_TAB = nil -- detetor de mudança de aba (UI → engine)

-- ── formatação ────────────────────────────────────────────────
local function fmt_ms(v)
  if v == nil then return "—" end
  return string.format("%.2f ms", v)
end

local function fmt_pos(p)
  if p == nil then return "—" end
  return string.format("%.1f  %.1f  %.1f", p.x or 0, p.y or 0, p.z or 0)
end

local function fmt_pct(v)
  if v == nil then return "—" end
  return string.format("%.0f%%", v)
end

-- ── listas (UiList) ───────────────────────────────────────────
local function fill_groups(systems)
  local rows = {}
  for _, g in ipairs(systems.groups or {}) do
    if (g.samples or 0) > 0 then
      rows[#rows + 1] = {
        name = g.name,
        val = string.format("%s · %s", fmt_ms(g.avg_ms), fmt_pct(g.pct)),
      }
    end
  end
  viber.ui.list("prof-groups", rows)
end

local function fill_systems(systems)
  local rows = {}
  for i, s in ipairs(systems.systems or {}) do
    if i > 12 then break end
    local hot = (s.avg_ms or 0) >= 1.0 and "! " or ""
    -- Bevy 0.20: contagem de componentes lidos/escritos por sistema (o
    -- `schedule.graph` do BRP) — diz EM QUÊ o sistema caro toca.
    local rw = ""
    if s.reads or s.writes then
      rw = string.format(" · R%d/W%d", s.reads or 0, s.writes or 0)
    end
    rows[#rows + 1] = {
      name = hot .. s.name,
      val = string.format("%s · %s%s",
        string.format("%.2f", s.avg_ms or 0), fmt_ms(s.p95_ms), rw),
    }
  end
  viber.ui.list("prof-systems", rows)
end

local function fill_scripts(systems)
  local rows = {}
  for i, s in ipairs(systems.scripts_timed or {}) do
    if i > 8 then break end
    rows[#rows + 1] = { name = s.name, val = fmt_ms(s.avg_ms) }
  end
  viber.ui.list("prof-scripts", rows)
end

local function fill_nearby(world)
  local t = world.tabs and world.tabs.world
  if t == nil then return end
  viber.ui.set_text("prof-nearby", string.format("%d/%d ≤%.0fm",
    #(t.nearby or {}), t.nearby_in_radius or 0, t.nearby_radius or 0))
  local rows = {}
  for i, n in ipairs(t.nearby or {}) do
    if i > 12 then break end
    rows[#rows + 1] = {
      name = string.format("%s (#%d)", n.name or "?", n.entity or 0),
      val = string.format("%.1fm  %s%s", n.dist or 0, fmt_pos(n.pos),
        (n.tags and #n.tags > 0) and ("  [" .. table.concat(n.tags, ",") .. "]") or ""),
    }
  end
  viber.ui.list("prof-nearby-rows", rows)
end

local function fill_shapes(physics)
  local shapes = physics.colliders and physics.colliders.by_shape or {}
  local rows = {}
  for shape, count in pairs(shapes) do
    rows[#rows + 1] = { name = shape, val = tostring(count) }
  end
  table.sort(rows, function(a, b) return a.name < b.name end)
  viber.ui.list("prof-shapes", rows)
end

local function fill_audio(audio)
  local buses = audio.buses or {}
  viber.ui.set_text("prof-buses", string.format(
    "master %.2f · música %.2f · sfx %.2f",
    buses.master or 0, buses.music or 0, buses.sfx or 0))
  viber.ui.set_text("prof-sinks", string.format(
    "%d total · %d a tocar · %d pausados · %d spatial",
    audio.total or 0, audio.playing or 0, audio.paused or 0, audio.spatial or 0))

  local rows = {}
  for _, l in ipairs(audio.layers or {}) do
    rows[#rows + 1] = {
      name = l.layer or "?",
      val = string.format("base %.2f%s%s", l.base_volume or 0,
        l.paused and "  [pausa]" or "", l.muted and "  [mute]" or ""),
    }
  end
  viber.ui.list("prof-layers", rows)

  local sinks = {}
  for _, s in ipairs(audio.sinks or {}) do
    sinks[#sinks + 1] = s
  end
  table.sort(sinks, function(a, b)
    local pa, pb = a.paused and 1 or 0, b.paused and 1 or 0
    if pa ~= pb then return pa < pb end
    return (a.name or "") < (b.name or "")
  end)
  local srows = {}
  for i, s in ipairs(sinks) do
    if i > 10 then break end
    srows[#srows + 1] = {
      name = (s.paused and "· " or "▶ ") .. (s.name or "?"),
      val = string.format("v=%.2f%s%s%s", s.volume or 0,
        s.spatial and "  3d" or "", s.looping and "  ∞" or "",
        s.layer and ("  [" .. s.layer .. "]") or ""),
    }
  end
  viber.ui.list("prof-sink-rows", srows)
end

local EXTRAS = { "colliders", "grass", "physics-pause", "" }

local function fill_extras(snap)
  local list = snap.extras or {}
  local descs = {}
  for i = 1, 4 do
    local e = list[i]
    local slot = tostring(i)
    if e then
      viber.ui.set_visible("prof-extra-" .. slot, true)
      viber.ui.set_text("prof-extra-label-" .. slot, e.label or e.id or "?")
      viber.ui.set_text("prof-extra-state-" .. slot, e.on and "ON" or "OFF")
      viber.ui.toggle_class("prof-extra-" .. slot, "on", e.on == true)
      descs[#descs + 1] = (e.label or e.id) .. " — " .. (e.description or "")
    else
      viber.ui.set_visible("prof-extra-" .. slot, false)
    end
  end
  viber.ui.set_text("prof-extra-desc", table.concat(descs, "\n"))
end

-- ── preenchimento por aba (só a activa: o resto fica barato) ──
local function fill_active(snap)
  local systems = snap.tabs and snap.tabs.systems
  if systems == nil then return end
  local state = snap.state or {}
  viber.ui.set_text("prof-fps", string.format("%.0f", systems.fps or 0))

  -- A aba REAL é a do widget nativo (o espelho state.tab_name chega com
  -- até um ciclo de publish de atraso — usar o ui_tab evita preencher a
  -- aba errada no frame da troca).
  local which = ui_tab ~= "" and ui_tab or (state.tab_name or "systems")
  if which == "systems" then
    local frame = systems.frame_ms or {}
    viber.ui.set_text("prof-frame", fmt_ms(frame.avg))
    viber.ui.set_text("prof-p95", fmt_ms(frame.p95))
    viber.ui.set_text("prof-worst", (systems.min_fps_window ~= nil)
      and string.format("%.0f", systems.min_fps_window) or "—")
    local avg = frame.avg or 0
    if avg > 0 then
      local h60 = 16.667 - avg
      viber.ui.set_text("prof-head", string.format("%+.1f ms%s", h60,
        avg > 16.667 and "  ✗" or ""))
    else
      viber.ui.set_text("prof-head", "—")
    end
    fill_groups(systems)
    fill_systems(systems)
    fill_scripts(systems)
  elseif which == "world" then
    local t = snap.tabs.world or {}
    local p = t.player
    viber.ui.set_text("prof-player", p and string.format("%s  %s  yaw %.0f°",
      p.name or "player", fmt_pos(p.pos), p.yaw_deg or 0) or "(nenhum)")
    local c = t.camera
    viber.ui.set_text("prof-cam", c and string.format("%s  %s",
      c.name or "câmera", fmt_pos(c.pos)) or "(nenhuma)")
    viber.ui.set_text("prof-entities", string.format("%d  ·  scripts %d/%d activos  ·  partículas %d  ·  chunks %d",
      t.entity_count or 0,
      (systems.scripts or {}).active or 0, (systems.scripts or {}).total or 0,
      systems.particle_emitters or 0, systems.terrain_chunks or 0))
    fill_nearby(snap)
  elseif which == "physics" then
    local ph = snap.tabs.physics or {}
    local b = ph.bodies or {}
    viber.ui.set_text("prof-bodies", string.format("%d  (fixos %d · din %d · cin %d)",
      b.total or 0, b.fixed or 0, b.dynamic or 0, b.kinematic or 0))
    viber.ui.set_text("prof-sleep", string.format("%d dormindo · %d acordados",
      b.sleeping or 0, b.awake or 0))
    local c = ph.colliders or {}
    viber.ui.set_text("prof-colliders", string.format("%d  · sensores %d  · pendentes %d  · cct %d",
      c.total or 0, c.sensors or 0, ph.pending_colliders or 0, ph.cct or 0))
    viber.ui.set_text("prof-step", ph.step and string.format(
      "%.2f ms  (média %.2f · p95 %.2f)", ph.step.last_ms, ph.step.avg_ms, ph.step.p95_ms)
      or "—")
    local r = ph.rapier
    viber.ui.set_text("prof-rapier", r and string.format(
      "corpos %d · colisores %d · juntas %d · dt %.4f",
      r.bodies, r.colliders, r.impulse_joints, r.timestep) or "sem contexto")
    fill_shapes(ph)
  elseif which == "audio" then
    fill_audio(snap.tabs.audio or {})
  elseif which == "extras" then
    fill_extras(snap)
  end
end

-- ── loop ──────────────────────────────────────────────────────
-- IMPORTANTe: `clicked(id)` vale SÓ no frame do press — cliques e o sync de
-- abas correm TODOS os frames; só o preenchimento pesado obedece ao CADENCE.
-- (O bug original: cliques consultados a cada 0,2 s morriam no drain por
-- frame e a aba voltava a SISTEMAS no tick seguinte.)
function on_update(dt)
  -- Cliques → comandos (todos os frames). ABAS: o tab-group nativo trata o
  -- clique sozinho — aqui só DETETAMOS a mudança e empurramos para a engine
  -- (sem depender de clicked(), que vale só no frame do press).
  if viber.ui.clicked("prof-freeze") then viber.profiler_cmd("freeze") end
  if viber.ui.clicked("prof-reset") then viber.profiler_cmd("reset") end
  -- COPIAR/EXPORTAR existem em TODAS as abas (o JSON é sempre o completo;
  -- o export escreve ficheiro, o copy vai ao clipboard via engine).
  for _, t in ipairs({ "systems", "world", "physics", "audio", "extras" }) do
    if viber.ui.clicked("prof-copy-" .. t) then viber.profiler_cmd("copy") end
    if viber.ui.clicked("prof-export-" .. t) then viber.profiler_cmd("export") end
  end
  for i, id in ipairs(EXTRAS) do
    if id ~= "" and viber.ui.clicked("prof-extra-" .. i) then
      viber.profiler_cmd("extra:" .. id)
    end
  end

  local snap = viber.profiler()
  if snap == nil then
    ACC = 1.0 -- modal fechado: primeiro frame após abrir já preenche
    return
  end
  local state = snap.state or {}

  -- Abas: FONTE ÚNICA = o widget nativo (tab-group). O utilizador clica ou
  -- usa o teclado nativo do modal (]/. [ , dígitos); o driver só DETETA a
  -- mudança e espelha para a engine (o snapshot passa a refletir a aba).
  -- Sem force-back nenhum: dois sincronizadores a escrever a mesma aba
  -- ping-pongam via publish atrasado.
  local ui_tab = viber.ui.tab("prof")
  if ui_tab ~= "" and ui_tab ~= LAST_UI_TAB then
    LAST_UI_TAB = ui_tab
    viber.profiler_cmd("tab:" .. ui_tab)
  end
  viber.ui.toggle_class("profiler-win", "frozen", state.frozen == true)
  viber.ui.toggle_class("prof-freeze", "armed", state.frozen == true)
  viber.ui.set_text("prof-freeze-label", state.frozen and "LIBERTAR" or "CONGELAR")
  local status = state.status or ""
  if status ~= LAST_STATUS then
    LAST_STATUS = status
    viber.ui.set_text("prof-status", status ~= "" and status or "—")
  end

  -- Preenchimento pesado (listas/textos): ao ritmo do CADENCE.
  ACC = ACC + dt
  if ACC < CADENCE then return end
  ACC = 0.0
  fill_active(snap)
end
