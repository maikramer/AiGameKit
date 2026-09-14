-- game.lua: o CONTROLLER do jogo — 100% Lua, sem um sistema RPG da engine.
-- Semeia os cristais (spawn_prototype) e guarda o estado em viber.game(),
-- que PERSISTE no save do mundo (world_kv) em qualquer preset: no arranque
-- pedimos `viber.load_save()` e cada cristal recolhido pede `viber.save()`.

local POSITIONS = { { -6, -4 }, { 4, -7 }, { 7, 3 }, { -3, 6 }, { 0, -9 }, { -8, 2 } }

-- Retoma a última sessão (posição + contador); sem save é um no-op.
viber.load_save()

local g = viber.game()
g.total = #POSITIONS
g.score = g.score or 0

function on_update(dt)
  local st = viber.state()
  if st.spawned then
    -- vitória: quando o último cristal entra, festeja com um timer.
    if not st.celebrated and (viber.game().score or 0) >= g.total then
      st.celebrated = true
      viber.sound("quest_complete")
      viber.after(0.6, function()
        viber.toast("Todos os cristais recolhidos — a vala agradece!")
      end)
    end
    return
  end
  st.spawned = true
  for _, p in ipairs(POSITIONS) do
    viber.spawn_prototype("crystal", p[1], p[2])
  end
end
