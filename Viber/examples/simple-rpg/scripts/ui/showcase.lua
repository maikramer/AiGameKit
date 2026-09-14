-- ui/showcase.lua — comportamento da vitrina da UI declarativa.
--
-- Demonstra a metade dinâmica da API: alimentar listas por script
-- (viber.ui.list), ler o estado de widgets (viber.ui.read), reagir
-- a cliques e ligar/desligar animação em runtime (viber.ui.set_anim).

-- Mochila de demonstração: uma fonte de lista que NÃO é da engine —
-- só existe porque o script a alimenta.
local mochila = {
  { name = "Poção", count = 12 },
  { name = "Antídoto", count = 3 },
  { name = "Pedaço de âmbar", count = 1 },
  { name = "Flechas", count = 40 },
}

local missoes = {
  { title = "Falar com o ferreiro", progress_text = "pronta", status = "ready" },
  { title = "Recolher 5 madeiras", progress_text = "3/5", status = "active" },
  { title = "Derrotar o lobo-ancião", progress_text = "0/1", status = "active" },
}

-- O top-level corre uma vez: instala as fontes e o estado inicial.
viber.ui.list("bag-demo", mochila)

-- Nota: `quests` já é fonte da engine (menu_data); reescrevê-la por
-- script sobrepõe-se — só para a vitrina ficar autónoma.
viber.ui.list("quests", missoes)

local function descreve()
  local nome = viber.ui.read("hero-name")
  local vol = viber.ui.read("volume")
  local hud = viber.ui.read("check-hud")
  local anim = viber.ui.read("check-anim")
  return string.format(
    "read() em vivo:\n" ..
    "  hero-name .text = %s\n" ..
    "  volume    .value = %s\n" ..
    "  check-hud .checked = %s\n" ..
    "  check-anim .checked = %s\n" ..
    "  listas: quests=%d, bag-demo=%d",
    nome and nome.text or "—",
    vol and tostring(vol.value) or "—",
    hud and tostring(hud.checked) or "—",
    anim and tostring(anim.checked) or "—",
    viber.ui.list_count("quests"),
    viber.ui.list_count("bag-demo")
  )
end

local st = viber.state() -- estado desta entidade UiRoot

-- ── UI v2: bind por script — a tag do banner acende via class-bind
-- (`<UiPanel class="banner" bind="demo.gradiente:on-tag">` + `.banner.on`).
-- Nomes da engine têm prioridade; "demo.gradiente" é nosso.
viber.ui.set("demo.gradiente", true)

function on_update(dt)
  viber.ui.set_text("readout-text", descreve())

  -- O selo roda só enquanto o check o manda (set_anim em runtime).
  local anim = viber.ui.read("check-anim")
  local a_ligar = anim and anim.checked or false
  if a_ligar ~= st.selo_a_rodar then
    st.selo_a_rodar = a_ligar
    viber.ui.set_anim("seal", a_ligar and "spin 6" or "none")
  end

  -- ── Eventos: o lote do frame drenado UMA vez. Substitui o padrão
  --    `clicked()` de 1 frame (que perdia cliques com polling lento)
  --    e a detecção de mudança manual. Sem prefixo: vem tudo.
  for _, ev in ipairs(viber.ui.events()) do
    if ev.type == "click" and ev.id == "demo-btn" then
      -- Tween explícito: o banner esbate — a volta começa no tween_done.
      viber.ui.tween("banner", { property = "opacity", to = 0.25,
                                 duration = 0.3, easing = "ease-out" })
    elseif ev.type == "tween_done" and ev.id == "banner"
           and ev.property == "opacity" then
      viber.ui.tween("banner", { property = "opacity", to = 1,
                                 duration = 0.5, easing = "ease-in-out" })
    elseif ev.type == "text_changed" then
      viber.log("hero-name mudou -> " .. tostring(ev.text))
    elseif ev.type == "value_changed" then
      viber.log("volume -> " .. tostring(ev.value))
    end
  end

  -- ── Criação dinâmica: uma linha nova a cada 4 s (cap 3), com tween
  --    de entrada; contada por viber.ui.query(".linha-dinamica").
  st.spawn_timer = (st.spawn_timer or 3.5) + dt
  if st.spawn_timer >= 4.0 then
    st.spawn_timer = 0.0
    local linhas = viber.ui.query(".linha-dinamica")
    if #linhas >= 3 then
      -- cheio: remove a mais antiga (destroy tira a subárvore do registry)
      viber.ui.destroy(linhas[1])
    end
    local id = viber.ui.create{
      tag = "uipanel", parent = "dynamic-area", class = "linha-dinamica",
      id = "dyn-" .. tostring((st.linhas or 0) + 1),
    }
    viber.ui.create{
      tag = "uitext", parent = id,
      text = "linha dinâmica #" .. tostring((st.linhas or 0) + 1),
    }
    st.linhas = (st.linhas or 0) + 1
    viber.ui.tween(id, { property = "opacity", from = 0, to = 1, duration = 0.4 })
    viber.log("criado " .. id .. " — query conta "
              .. tostring(#viber.ui.query(".linha-dinamica")) .. " linha(s)")
  end
end
