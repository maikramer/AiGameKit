-- building-portal.lua: portas bidireccionais entre a VILA e a BOLSA DE INTERIORES.
--
-- GERADO por tools/gen_interiors.py (só as CONSTANTES vêm de lá — POCKET e a
-- grelha têm de bater com o <InteriorScene> que a tool emite). Não editar à
-- mão: a lógica vive no template PORTAL_LUA do gerador.
--
-- v6 (2026-10-14), DATA-DRIVEN: o registo porta↔sala sai dos NOMES das
-- entidades — `portal.<room>`/`portal.<room>.<sufixo>` (porta de rua) e
-- `portal.exit_<room>` (saída na bolsa) — lidos com `viber.find_all` +
-- `viber.entity_name`/`viber.entity_position`. A v5 embutia uma tabela ROOMS
-- gerada: mover uma porta no editor desincronizava-a em silêncio e uma porta
-- a >3 m da posição gerada morria (ex-portal.market_stall_c, a 9,9 m da
-- sala). Agora o NOME carrega a sala e a POSIÇÃO sai da própria entidade —
-- mover/renomear portas no editor (ou multiplicá-las por sala) simplesmente
-- funciona.
--
-- A bolsa vive FORA da pegada do heightmap (world-size 4000), declarada por
-- <InteriorScene>: a engine não gera terreno nem colliders lá — o chão é o
-- soalho da sala (FLOOR_Y). Os Y's NÃO são adivinhados: fora, `viber.
-- ground_below` resolve a cota do adro (a v4 teleportava para y≈0.3 na vila
-- e o herói caía 24 m).
local POCKET = { x = 2600.0, z = 2600.0 }
local POCKET_MIN_X = POCKET.x - 200.0   -- tudo acima disto é interiores

local FLOOR_Y = 0.6      -- soalho da sala (a laje está a 0.12)
local INWARD = 6.0       -- quanto o herói entra para dentro ao chegar
local MATCH_R = 3.0      -- sanity: a saída tem de estar PERTO da parede -Z
local AUTO_EXIT_R = 1.6  -- encostar à porta POR DENTRO sai sem tecla
local ROOM_W = 60.0      -- passo X da grelha (= <InteriorScene room-size>)
local ROOM_H = 55.0      -- passo Z da grelha
local HEAL_EVERY = 2.0   -- de quanto em quanto tempo se re-tenta o registo

-- ── REGISTO derivado das entidades ──────────────────────────────────────
-- Construído 1× e cacheado em `viber.game().portal_registry` (sobrevive a
-- hot-reload e ao save). Cada portal VALIDA A PRÓPRIA entrada em cada frame
-- (`self_stale`) — mover/renomear uma porta ao vivo reconstrói o registo.
--   doors[room] = { {x=…, z=…}, … }         portas de rua (várias por sala OK)
--   rooms[room] = { ox=…, oz=…, exit_dz=… } saída na bolsa → célula da grelha
--
-- NOTA (snapshot com cap): o registo é construído nos primeiros frames, quando
-- os portais (spawnados antes da vegetação em massa) ainda cabem no snapshot
-- de nomes da engine; DEPOIS de construído, o on_update só precisa do NOME
-- próprio (`viber.entity_name()`, semeado fora do cap) e de posições em cache.
-- Se a minha sala não estiver no registo, `heal` re-tenta periodicamente.
local function build_registry()
  local ids = viber.find_all("portal.")
  if #ids == 0 then return nil end  -- entidades ainda não spawnaram: NÃO cachear
  local reg = { doors = {}, rooms = {} }
  for _, id in ipairs(ids) do
    local name = viber.entity_name(id)
    local ok, x, y, z = viber.entity_position(id)
    if name and ok then
      local exit_room = name:match("^portal%.exit_([%w_]+)$")
      if exit_room then
        -- Posição relativa à bolsa → célula da grelha (ox/oz, o CENTRO da
        -- sala) + o resta até à saída (exit_dz, ~1 m fora da parede -Z).
        local rx, rz = x - POCKET.x, z - POCKET.z
        local ox = math.floor(rx / ROOM_W + 0.5) * ROOM_W
        local oz = math.floor(rz / ROOM_H + 0.5) * ROOM_H
        local exit_dz = rz - oz
        -- Sanity (MATCH_R): a saída pertence à parede -Z da célula — um
        -- exit_dz perto de 0 (centro da sala) ou além do limite da célula é
        -- grelha↔XML desincronizadas, avisa em vez de adivinhar.
        if exit_dz > -MATCH_R or exit_dz < -ROOM_H * 0.5 then
          viber.log("building-portal: saída '" .. name .. "' fora da parede -Z da célula"
            .. " (exit_dz=" .. string.format("%.1f", exit_dz) .. ") — grelha e XML dessincronizados?")
        end
        reg.rooms[exit_room] = { ox = ox, oz = oz, exit_dz = exit_dz }
      else
        local room = name:match("^portal%.([%w_]+)%.[%w_]+$")
          or name:match("^portal%.([%w_]+)$")
        if room then
          reg.doors[room] = reg.doors[room] or {}
          reg.doors[room][#reg.doors[room] + 1] = { x = x, z = z }
        end
        -- Nomes que não casam nenhum padrão são avisados pelo DONO (on_update).
      end
    end
  end
  return reg
end

-- A MINHA entrada no registo ainda bate com a minha posição? (O registo é um
-- cache; uma porta MOVIDA ao vivo no editor tinha entradas velhas — cada
-- entidade valida-se a si própria e dispara o rebuild geral.)
local function self_stale(reg, name, x, z, inside)
  if inside then
    local room = name and name:match("^portal%.exit_([%w_]+)$")
    local r = room and reg.rooms[room]
    if not r then return false end  -- sem entrada: o on_update trata (heal)
    local rx, rz = x - POCKET.x, z - POCKET.z
    local ox = math.floor(rx / ROOM_W + 0.5) * ROOM_W
    local oz = math.floor(rz / ROOM_H + 0.5) * ROOM_H
    return r.ox ~= ox or r.oz ~= oz or r.exit_dz ~= (rz - oz)
  end
  local room = name
    and (name:match("^portal%.([%w_]+)%.[%w_]+$") or name:match("^portal%.([%w_]+)$"))
  local doors = room and reg.doors[room]
  if not doors then return false end
  for _, d in ipairs(doors) do
    local dx, dz = d.x - x, d.z - z
    if dx * dx + dz * dz <= 0.25 then return false end  -- 0,5 m: cobre-me
  end
  return true  -- nome válido mas NENHUMA entrada me cobre → mudei
end

-- Re-tenta o registo de tanto em tanto (a minha sala não tem entrada): o
-- snapshot de nomes da engine tem cap e o registo pode ter sido construído
-- a meio; se os portais voltarem a estar visíveis ao `find_all`, a
-- reconstrução cura o registo sozinha.
local function heal(st, g, dt, msg)
  if not st.warned then
    st.warned = true
    viber.log("building-portal: " .. msg)
  end
  st.retry = (st.retry or 0.0) + dt
  if st.retry >= HEAL_EVERY then
    st.retry = 0.0
    g.portal_registry = nil
  end
end

function on_update(dt)
  local g = viber.game()
  if not g.portal_registry then
    g.portal_registry = build_registry()  -- nil se find_all vazio → retry
  end
  local reg = g.portal_registry

  local st = viber.state()
  local x, y, z = viber.position()
  local inside_pocket = x > POCKET_MIN_X
  local my_name = viber.entity_name()

  -- Cache fresco? Cada portal valida a própria entrada (ver self_stale).
  if reg and my_name and self_stale(reg, my_name, x, z, inside_pocket) then
    reg = build_registry()
    g.portal_registry = reg
  end
  if not reg then return end  -- entidades ainda a spawnar; retry no próximo frame

  if not st.ready then
    st.ready = true
    viber.set_interaction(inside_pocket and "Sair" or "Entrar", "e", 2.8)
  end

  -- SAÍDA AUTOMÁTICA (interiores): a porta de uma sala é um vão aberto sobre
  -- o vazio — encostar a ela tem de tirar o herói dali, não pedir uma tecla
  -- (pedido do utilizador 2026-09-13: "bloquear e imediatamente me fazer
  -- sair"). O piso duro da bolsa trata da queda; este gatilho trata da
  -- saída. Só vale de DENTRO: uma porta de rua continua a exigir [E], senão
  -- passar à frente de uma casa sugava o jogador.
  local auto = inside_pocket and viber.distance_to_player() <= AUTO_EXIT_R
  -- `interacted` não consome o evento duas vezes no mesmo frame entre
  -- entidades: cada portal decide pelo seu próprio cooldown.
  if not (auto or viber.interacted("e")) then
    st.cd = false
    return
  end
  if st.cd then return end

  if inside_pocket then
    -- Sou `portal.exit_<room>`: a sala vem do NOME (sem matching por raio).
    local room = my_name and my_name:match("^portal%.exit_([%w_]+)$")
    if not room then
      if not st.warned then
        st.warned = true
        viber.log("building-portal: '" .. tostring(my_name)
          .. "' não casa portal.exit_<room> — renomear a entidade no editor")
      end
      return
    end
    local doors = reg.doors[room]
    if not doors or #doors == 0 then
      heal(st, g, dt, "sala '" .. room .. "' sem porta de rua registada (portals.xml?)")
      return
    end
    -- Várias portas para a mesma sala (market.stall_a/b/c): sair pela porta
    -- por onde se ENTROU (g.last_door, tolerância 1 m — a porta pode ter
    -- sido movida um pouco); sem memória, a 1.ª registada. A distância ao
    -- jogador não discrimina (as portas ficam a ~2,6 km daqui).
    local door = doors[1]
    local want = g.last_door and g.last_door[room]
    if want then
      local bd = 1.0
      for _, d in ipairs(doors) do
        local dx, dz = d.x - want.x, d.z - want.z
        local dd = dx * dx + dz * dz
        if dd <= bd then bd, door = dd, d end
      end
    end
    st.cd = true
    local gy = viber.ground_below(door.x, 200.0, door.z)
    viber.sound("door_close")
    viber.teleport_player(door.x, (gy or 25.0) + 0.25, door.z)
    viber.toast("A porta devolve-te à vila.")
  else
    -- Sou `portal.<room>[.sufixo]`: a sala vem do NOME.
    local room = my_name
      and (my_name:match("^portal%.([%w_]+)%.[%w_]+$") or my_name:match("^portal%.([%w_]+)$"))
    if not room then
      if not st.warned then
        st.warned = true
        viber.log("building-portal: '" .. tostring(my_name)
          .. "' não casa portal.<room>[.sufixo] — renomear a entidade no editor")
      end
      return
    end
    local r = reg.rooms[room]
    if not r then
      heal(st, g, dt, "porta '" .. room .. "' sem sala na bolsa (portal.exit_"
        .. room .. " em falta no interiors.xml?)")
      return
    end
    st.cd = true
    -- Memória da porta usada: a MINHA posição (sou a porta de rua) — ao sair,
    -- o herói devolve a esta porta em vez da 1.ª da sala.
    g.last_door = g.last_door or {}
    g.last_door[room] = { x = x, z = z }
    viber.sound("door_open")
    viber.teleport_player(POCKET.x + r.ox, FLOOR_Y, POCKET.z + r.oz + r.exit_dz + INWARD)
    viber.toast("Entras no edifício.")
  end
end
