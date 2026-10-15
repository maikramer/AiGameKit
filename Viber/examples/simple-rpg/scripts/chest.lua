-- chest.lua: baú de uma abertura só — loot real no vault/inventário via hooks
-- da economia. O conteúdo é de CADA baú (`prop-*` no XML / Inspector › Script).
--@export xp: int(0..) = 30                 -- XP ao abrir
--@export gold: int(0..) = 25               -- ouro ao abrir
--@export item: string = "potion"           -- item do inventário ("" = nenhum)
--@export item_count: int(0..) = 1          -- quantos desse item
--@export item_label: string = "poção"      -- nome do item no toast
--@export prompt: string = "Abrir o baú"
--@export range: number(0.5..10) = 2.8      -- alcance do [E] (m)
--@signal opened(gold, xp)                  -- abriu (1×) — quem ouve é dado do baú: connect-opened="@grupo:método"

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (o top-level é partilhado)
  if not st.ready then
    st.ready = true
    viber.set_interaction(viber.prop("prompt"), "e", viber.prop("range"))
  end
  if viber.interacted("e") and not st.opened then
    st.opened = true
    local p = viber.props()
    local loot = {}
    if p.xp > 0 then
      viber.add_xp(p.xp)
      loot[#loot + 1] = "+" .. p.xp .. " XP"
    end
    if p.gold > 0 then
      viber.vault_add("gold", p.gold)
      loot[#loot + 1] = "+" .. p.gold .. " ouro"
    end
    if p.item ~= "" and p.item_count > 0 then
      viber.item_add(p.item, p.item_count)
      loot[#loot + 1] = "+" .. p.item_count .. " " .. p.item_label
    end
    viber.emit("opened", p.gold, p.xp)
    if #loot == 0 then
      viber.toast("O baú range e cede... está vazio.")
    else
      viber.toast("O baú range e cede... " .. table.concat(loot, ", "))
    end
  elseif viber.interacted("e") then
    viber.toast("O baú está vazio.")
  end
end
