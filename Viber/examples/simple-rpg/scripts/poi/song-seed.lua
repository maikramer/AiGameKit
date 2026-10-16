-- poi/song-seed.lua — Semente de Canção (o "Korok" de Vael).
--
-- Colecionável escondido com MICRO-PUZZLE, recompensa própria (item
-- "korok-seed", nunca ouro) e contador de coleção: a cada 5 sementes,
-- +1 ponto de talento (viber.add_skill_points). Persistência: o estado de
-- coleta vive em `viber.game()` (world_kv) → sobrevive a save→load; a
-- entidade despawnata-se ao ser coletada e volta a auto-despintar-se na
-- próxima sessão se a semente já foi contada.
--
-- Indicador de atenção (tese BotW §5.1.2, gravity): coluna de fumo subindo
-- do esconderijo — visível de longe, significado descoberto de perto.
--
-- MODOS (prop-mode):
--   "rock"  — erguer a pedra solitária: [E] e a pedra abre-se em faíscas
--             (a semente estava por baixo).
--   "odd"   — a pedra que canta: num grupo de pedras, UMA brilha; [E] nela.
--   "ring"  — anel de notas: 3 cristais (prop-index 1..3, MESMO prop-group);
--             acender os três libera a semente no último.
--   "hide"  — descoberta pura: a semente está escondida (atrás de relevo,
--             sob ponte, na boca da gruta); o fumo denuncia o sítio, não o
--             objeto.
--
--@export mode: enum(rock,odd,ring,hide) = "rock"
--@export group: string = ""              -- id do anel (mode="ring"; igual nos 3)
--@export index: int(1..3) = 1            -- nota deste cristal (mode="ring")
--@export prompt: string = "Erguer a pedra"
--@export range: number(0.5..10) = 2.2    -- alcance do [E] (m)
--@export seed_key: string = ""           -- chave de persistência (default: nome da entidade)

function on_update(dt)
  local st = viber.state()
  if not st.ready then
    st.ready = true
    st.mode = viber.prop("mode")
    local name = viber.entity_name() or "anon"
    st.key = "korok-" .. (viber.prop("seed_key") ~= "" and viber.prop("seed_key") or name)
    local game = viber.game()
    st.taken = (game[st.key] or 0) > 0
    if st.mode == "ring" then
      st.ring_key = "korokring-" .. viber.prop("group")
      st.ring_done = (game[st.ring_key] or 0) >= 3
    end
    if st.taken or (st.mode == "ring" and st.ring_done) then
      -- Já coletada (sessão anterior): sai de cena sem prompt.
      viber.despawn_self()
      return
    end
    viber.set_interaction(viber.prop("prompt"), "e", viber.prop("range"))
  end

  local function collect(phrase)
    local game = viber.game()
    game[st.key] = 1
    local _, x, y, z = viber.entity_position()
    viber.despawn_self()
    viber.burst("sparkle", x, y + 0.6, z, 16)
    viber.sound("loot")
    local count = viber.vault_get("korok-seed") + 1
    viber.item_add("korok-seed", 1)
    if count % 5 == 0 then
      viber.add_skill_points(1)
      viber.toast(phrase .. " — semente " .. count .. "/20: +1 PONTO DE TALENTO!")
    else
      viber.toast(phrase .. " (" .. count .. " na bolsa)")
    end
  end

  if st.mode == "ring" then
    if st.ring_done then
      return
    end
    if viber.interacted("e") then
      local game = viber.game()
      local n = (game[st.ring_key] or 0) + 1
      game[st.ring_key] = n
      local x, y, z = viber.position()
      viber.despawn_self()
      viber.burst("sparkle", x, y + 0.4, z, 12)
      if n >= 3 then
        collect("O anel ressoa inteiro: semente de canção!")
      else
        viber.sound("ui")
        viber.toast("Uma nota se acende no ar (" .. n .. "/3)...")
      end
    end
    return
  end

  -- rock / odd / hide: um [E] e a semente é tua.
  if viber.interacted("e") and not st.taken then
    local phrase = ({
      rock = "A pedra ergue-se: uma semente de canção!",
      odd = "A pedra que canta guarda uma semente!",
      hide = "Escondida onde ninguém olha: uma semente de canção!",
    })[st.mode] or "Uma semente de canção!"
    collect(phrase)
  end
end
