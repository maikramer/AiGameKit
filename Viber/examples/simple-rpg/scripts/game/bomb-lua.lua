-- game/bomb-lua.lua: a bomba do jogo (Lua). Colado ao `<Prototype
-- id="bomb-lua">` — cada instância largada por game/abilities.lua nasce com
-- este comportamento: fuse curto, detonação em ÁREA (`radial_damage` com a
-- paridade nativa: falloff, knockback, cadáver/XP/quests/evento Kill) e o
-- juice do nativo (burst/ring/shake/kick/hit-stop/número).
--
-- Nota honesta: esta bomba detona NO SÍTIO onde foi largada (sem balística).
-- A balística completa precisa de passar a velocidade inicial ao spawn
-- (follow-up: `opts` do `viber.spawn_prototype`), que a API ainda não tem.

local FUSE = 1.5
local RADIUS, DAMAGE, KNOCKBACK = 6.0, 90, 9

function on_update(dt)
  local st = viber.state()
  st.t = (st.t or 0) + dt
  -- pisca (aviso) nos últimos 0,4 s
  if st.t > FUSE - 0.4 and st.t < FUSE and not st.blink then
    st.blink = true
    local x, y, z = viber.position()
    viber.burst("sparkle", x, y + 0.3, z, 6)
  end
  if st.t < FUSE or st.boom then
    return
  end
  st.boom = true
  local x, y, z = viber.position()
  viber.radial_damage(x, z, RADIUS, DAMAGE, { knockback = KNOCKBACK })
  viber.burst("ground-dust", x, y + 0.2, z, 14)
  viber.burst("magic", x, y + 0.6, z, 10)
  viber.ring(x, z, RADIUS * 0.9)
  viber.shake(0.5)
  viber.kick(0, 1.6, 0)
  viber.hit_stop(0.13)
  viber.sound("hit")
  viber.damage_number("BOOM!", { x = x, y = y + 1.2, z = z, color = "#ff8a3d" })
  viber.despawn_self()
end
