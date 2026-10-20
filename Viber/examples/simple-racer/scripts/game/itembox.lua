-- Item box de corrida: o kart do jogador apanha-a ao passar (raio 3,2 m
-- em XZ) e recebe um power-up (nitro + turbo). Cooldown por caixa — volta
-- a armar passados `cooldown` s. O modelo (caixote) é filho do nó.
--
--@export nitro: number(0..1) = 1.0
--@export turbo: number(0..10) = 1.4
--@export cooldown: number(1..30) = 6.0

local st = viber.state()
st.cd = 0.0
st.toast = 0.0

local PICKUP_RADIUS_SQ = 3.2 * 3.2

function on_update(dt)
  st.cd = st.cd - dt
  -- toast: limpa depois de 2 s (o texto vive no HUD do mundo).
  if st.toast > 0.0 then
    st.toast = st.toast - dt
    if st.toast <= 0.0 then
      viber.ui.set_text("item-toast", "")
    end
  end
  if st.cd > 0.0 then
    return
  end
  local has, px, _, pz = viber.player_position()
  if not has then
    return
  end
  local o = viber.position()
  local dx, dz = px - o.x, pz - o.z
  if dx * dx + dz * dz > PICKUP_RADIUS_SQ then
    return
  end
  st.cd = viber.prop("cooldown")
  viber.game().pickup = {
    nitro = viber.prop("nitro"),
    turbo = viber.prop("turbo"),
  }
  viber.ui.set_text("item-toast", "POWER-UP! NITRO + TURBO")
  st.toast = 2.0
  viber.sound("nitro")
end
