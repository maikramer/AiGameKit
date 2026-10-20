local rules = viber.load("lib/rules.lua")
-- game/story.lua: o fio do Capítulo I — O Silêncio de Vael (ver STORY.md).
--
--   * PRÓLOGO na primeira entrada no mundo (marca `story_prologo` no save);
--   * cada GUARDIÃO derrotado devolve uma Nota (item `nota`, a missão-mãe
--     `story_notas` do Ancião Edmar conta-as) e toca a cena `nota`;
--   * ENTREGAR as quatro ao Ancião toca o `epilogo`.
--
-- As marcas vivem em `viber.game()` (valores primitivos persistem no save):
-- recarregar um save não repete cenas nem dá Notas a dobrar.
--
-- Host: `world/gameplay.xml` (`tag="always-active"`).

-- kind do abate (nome do script do chefe) → nome da Nota
local GUARDIANS = {
  witch = "Nota do Bosque",
  ["sand-worm"] = "Nota do Ermo",
  ["bog-warden"] = "Nota do Lodo",
  boss = "Nota dos Picos",
}
local PROLOGUE_DELAY = 0.1 -- a cena começa em negro: o carregamento passa direto para ela

local function notes_found(g)
  local n = 0
  for kind in pairs(GUARDIANS) do
    if g["nota_" .. kind] then n = n + 1 end
  end
  return n
end

function on_update(dt)
  local g = viber.game()
  local st = viber.state()
  st.clock = (st.clock or 0) + dt

  if not g.story_prologo and st.clock > PROLOGUE_DELAY and not viber.cinematic_playing() then
    g.story_prologo = true
    viber.cinematic("prologo")
  end

  for _, ev in ipairs(viber.events()) do
    if ev.type == "kill" and GUARDIANS[ev.name] and not g["nota_" .. ev.name] then
      g["nota_" .. ev.name] = true
      rules.item_add("nota", 1)
      local n = notes_found(g)
      local _, x, y, z = viber.player_position()
      viber.burst("sparkle", x, y + 1.2, z, 40)
      viber.ring(x, z, 4, "#9fd8ff")
      viber.toast(GUARDIANS[ev.name] .. " recuperada (" .. n .. "/4)")
      viber.cinematic("nota")
    elseif ev.type == "quest_done" and ev.id == "story_notas" and not g.story_epilogo then
      g.story_epilogo = true
      viber.cinematic("epilogo")
    elseif ev.type == "cinematic_end" and ev.id == "prologo" then
      viber.toast("Fala com o Ancião Edmar, no salão comunal.")
    end
  end
end
