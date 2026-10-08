-- HUD v3: os toggles de estado (danger, ready, empty, venom, xp-pop,
-- weather) são TODOS class-binds engine-driven no hud.xml — este script
-- só trata do clique do diário, do soco visual do combo e do ciclo de
-- vida da barra de chefe (binds de script alimentados por lib/fsm.lua).
local st = viber.state()

-- Mesmo TTL do lib/fsm.lua (M.BOSS_BAR_TTL): sem sinal há 1.5 s = chefe
-- morto, adormecido ou deixado para trás.
local BOSS_BAR_TTL = 1.5

-- Linhas de "outras missões" por baixo do cartão (a do cartão fica de fora).
local OTHER_ROWS = 3
local OTHERS_PERIOD = 0.5

local function refresh_other_quests()
  local shown = viber.ui.get("quest.title")
  local rows = {}
  for _, def in ipairs(viber.quest_defs()) do
    local state = viber.quest_state(def.id)
    if (state == "active" or state == "ready") and def.title ~= shown then
      rows[#rows + 1] = { title = def.title, ready = state == "ready" }
    end
  end
  -- Prontas primeiro: são as que pedem uma ação (voltar ao dador).
  table.sort(rows, function(a, b)
    if a.ready ~= b.ready then return a.ready end
    return a.title < b.title
  end)
  for i = 1, OTHER_ROWS do
    local row = rows[i]
    local id = "quest-other-" .. i
    viber.ui.set_text(id, row and (row.title .. (row.ready and "  · pronta" or "")) or "")
    viber.ui.set_visible(id, row ~= nil)
    viber.ui.toggle_class(id, "ready", row ~= nil and row.ready)
  end
  local extra = #rows - OTHER_ROWS
  viber.ui.set_text("quest-other-more", extra > 0 and ("+" .. extra .. " no diário [Q]") or "")
  viber.ui.set_visible("quest-other-more", extra > 0)
  viber.ui.set("quests.others", #rows > 0)
end

function on_update(dt)
  st.others_t = (st.others_t or 0) - dt
  if st.others_t <= 0 then
    st.others_t = OTHERS_PERIOD
    refresh_other_quests()
  end

  if not st.seeded then
    -- Semeia os binds de script antes do 1.º sinal (sem isto o XML
    -- avisava "unknown binding" até alguém os escrever).
    st.seeded = true
    viber.ui.set("boss.active", false)
    viber.ui.set("boss.name", "")
    viber.ui.set("boss", 1)
    viber.ui.set("boss.enraged", false)
    viber.ui.set("quests.others", false)
  end

  if viber.ui.clicked("open-journal") then
    viber.ui.open("menu", true)
  end

  local seen = viber.game().boss_bar_t or -100
  -- `viber.game()` viaja no save: um carimbo vindo de outra sessão pode
  -- estar no "futuro" do relógio desta — vale como sinal nenhum.
  if seen > viber.time() then seen = -100 end
  local boss_live = viber.time() - seen < BOSS_BAR_TTL
  if boss_live ~= st.boss_live then
    st.boss_live = boss_live
    if not boss_live then
      viber.ui.set("boss.active", false)
    end
  end

  local combo = viber.ui.get("combo.text")
  if combo ~= st.combo then
    st.combo = combo
    viber.ui.set_anim("combo-text", combo ~= "" and "shake 0.45 0.5" or "none")
  end
end
