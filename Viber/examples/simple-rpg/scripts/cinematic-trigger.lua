-- cinematic-trigger.lua: toca uma `<Cinematic>` quando o herói entra no raio.
-- Tudo é DADO da instância (Inspector › Script ou `viber scene set`):
--   prop-cinematic="campo-de-batalha" prop-radius="18"
-- "Só uma vez" vale por save: a marca vive em `viber.game()` (persiste).
--@export cinematic: string = ""            -- id da `<Cinematic>` (viber cinematic list)
--@export radius: number(1..120) = 14        -- raio de entrada (m)
--@export once: bool = true                  -- só a primeira vez (por save)

function on_update(dt)
  local id = viber.prop("cinematic")
  if id == "" or viber.cinematic_playing() then return end
  local g = viber.game()
  local key = "cine_" .. id
  if viber.prop("once") and g[key] then return end
  local has, px, _, pz = viber.player_position()
  if not has then return end
  local x, _, z = viber.position()
  if math.sqrt((px - x) ^ 2 + (pz - z) ^ 2) > viber.prop("radius") then return end
  g[key] = true
  viber.cinematic(id)
end
