-- game/abilities.lua: as HABILIDADES do herói são do JOGO (Lua). O script
-- reclama os sistemas nativos [C]/[E]/[R] e [B] (`viber.own_system`) — os
-- handlers da engine calam — e reimplementa-os sobre a API genérica:
--
--   [C] dash     → player_forward + teleport_player (assenta no terreno) + FOV kick
--   [E] cura     → heal_player + número flutuante
--   [R] radial   → radial_damage (falloff + knockback + morte com paridade
--                  nativa: cadáver/XP/quests/evento Kill) + juice (burst/ring/shake/hit_stop)
--   [B] bomba    → vault_take + spawn_prototype do `<Prototype id="bomb-lua">`
--                  (o script da bomba detona: ver game/bomb-lua.lua)
--
-- [J] melee, [L] guarda (precisa do componente nativo `Guarding`) e o
-- diálogo [E] continuam da engine — a posse é POR SISTEMA.
--
-- Host: `world/gameplay.xml` (`<Entity tag="always-active">` — o controller
-- corre onde o herói andar; sem o tag o LOD de 45 m congelava-o).

viber.own_system("abilities")
viber.own_system("bomb")

local CD_DASH, CD_HEAL, CD_STRIKE = 6.0, 12.0, 8.0
local DASH_DISTANCE = 4.2
local HEAL_AMOUNT = 50
local STRIKE_RADIUS, STRIKE_DAMAGE, STRIKE_KNOCKBACK = 4.8, 60, 7
local BOMB_LAUNCH_DISTANCE = 2.6

local PERIOD = { dash = CD_DASH, heal = CD_HEAL, strike = CD_STRIKE }

-- O dock do HUD lê `ab.<nome>` (fração em recarga) e `ab.<nome>.ready`
-- (class-bind que acende o slot) — os `cd.*` nativos ficam a zero com o
-- sistema reclamado, e sem isto o dock mostrava tudo sempre pronto.
-- (Nomes literais: o teste `test_shipped_worlds_bind_only_to_known_names`
-- lê os `viber.ui.set("…")` dos scripts para validar os binds do hud.xml.)
local function publish_cooldowns(cd)
  viber.ui.set("ab.dash", cd.dash / PERIOD.dash)
  viber.ui.set("ab.dash.ready", cd.dash <= 0)
  viber.ui.set("ab.heal", cd.heal / PERIOD.heal)
  viber.ui.set("ab.heal.ready", cd.heal <= 0)
  viber.ui.set("ab.strike", cd.strike / PERIOD.strike)
  viber.ui.set("ab.strike.ready", cd.strike <= 0)
end

-- O [E] é de quem estiver à frente: com um prompt [E] no ecrã (NPC,
-- baú, poço…) a tecla é da interação e a cura não sai por baixo.
local function e_is_interaction()
  return viber.ui.get("prompt.key") == "E" and viber.ui.get("prompt.label") ~= ""
end

function on_update(dt)
  local st = viber.state()
  st.cd = st.cd or { dash = 0, heal = 0, strike = 0 }
  for k, v in pairs(st.cd) do
    st.cd[k] = math.max(0, v - dt)
  end
  publish_cooldowns(st.cd)
  -- Menus abertos comem as teclas (paridade com o `MenusOpen` nativo).
  -- `viber.ui.is_open` exige o id do modal (não há "algum aberto?" na API).
  if viber.ui.is_open("menu") or viber.ui.is_open("profiler") then
    return
  end
  local has, px, py, pz = viber.player_position()
  if not has then
    return
  end

  -- [C] dash: reto no forward do herói; o teleport assenta o Y.
  if viber.input.pressed("c") and st.cd.dash <= 0 then
    st.cd.dash = CD_DASH
    local fx, fz = viber.player_forward()
    viber.face_player()
    viber.teleport_player(px + fx * DASH_DISTANCE, py, pz + fz * DASH_DISTANCE)
    viber.fov_kick(8)
    viber.sound("dash")
    viber.burst("ground-dust", px, py + 0.2, pz, 10)
  end

  -- [E] cura (sem interação em alcance — a fronteira do nativo: o prompt
  -- [E] do HUD é a mesma pergunta "há alguém para falar/usar aqui?").
  -- Com a vida cheia não gasta a recarga; o número mostra o que CUROU (e
  -- nasce sobre o herói — sem x/y/z saía sobre a entidade controller).
  if viber.input.pressed("e") and st.cd.heal <= 0 and not e_is_interaction() then
    local _, hp, max_hp = viber.player_hp()
    local healed = math.min(HEAL_AMOUNT, math.max(0, (max_hp or 0) - (hp or 0)))
    if healed < 1 then
      viber.toast("Vida já está cheia.")
    else
      st.cd.heal = CD_HEAL
      viber.heal_player(healed)
      viber.sound("heal")
      viber.burst("sparkle", px, py + 1.0, pz, 14)
      viber.damage_number("+" .. math.floor(healed) .. " HP", { color = "#7ef29d", x = px, y = py + 2.0, z = pz })
    end
  end

  -- [R] golpe radial com juice completo (valores do nativo).
  if viber.input.pressed("r") and st.cd.strike <= 0 then
    st.cd.strike = CD_STRIKE
    viber.face_player()
    viber.radial_damage(px, pz, STRIKE_RADIUS, STRIKE_DAMAGE, { knockback = STRIKE_KNOCKBACK })
    viber.burst("sparks", px, py + 1.0, pz, 18)
    viber.ring(px, pz, STRIKE_RADIUS)
    viber.shake(0.45)
    viber.kick(0, 1.2, 0)
    viber.hit_stop(0.13)
    viber.punch(0.45, 0.18)
    viber.sound("whoosh")
    viber.sound("hit")
  end

  -- [B] bomba: larga o prototype à frente (detona com fuse — bomb-lua.lua).
  if viber.input.pressed("b") then
    if viber.item_count("bomb") > 0 then
      viber.vault_take("bomb", 1)
      local fx, fz = viber.player_forward()
      viber.spawn_prototype("bomb-lua", px + fx * BOMB_LAUNCH_DISTANCE, pz + fz * BOMB_LAUNCH_DISTANCE, {
        y = py + 1.1,
      })
      viber.sound("bomb_drop")
    else
      viber.toast("Sem bombas — compra ao mercador.")
      viber.sound("error")
    end
  end
end
