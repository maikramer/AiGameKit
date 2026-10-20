-- lib/once.lua: marca de "já feito" que SOBREVIVE ao save.
--
-- `viber.state()` é por entidade viva. Um load cria entidades novas: o baú,
-- o musgo e a relíquia voltavam a pagar. A marca vive em `viber.game()`
-- (primitivo, vai no save), chaveada pelo nome do mundo.
--
--   local once = viber.load("lib/once.lua")
--   if once.taken() then … end
--   once.mark()

local M = {}

function M.key()
  local name = viber.entity_name()
  if name and name ~= "" then
    return "once:" .. name
  end
  local x, _, z = viber.position()
  return string.format("once:%.0f:%.0f", x or 0, z or 0)
end

function M.taken()
  return (viber.game()[M.key()] or 0) > 0
end

function M.mark()
  viber.game()[M.key()] = 1
end

return M
