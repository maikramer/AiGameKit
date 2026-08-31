-- example.lua — demonstra o runtime Luau do Viber.
--
-- Um script Luau define `function on_update(dt)` e controla a entidade dona
-- através da API global `viber`:
--   viber.log(msg)                  — escreve no log da engine (e buffer)
--   viber.time()                    — segundos desde o arranque
--   viber.position() -> x, y, z     — posição da entidade (snapshot do frame)
--   viber.set_position(x, y, z)     — move a entidade (aplicado no fim do frame)
--   viber.distance_to_player()      — distância euclidiana ao player (nil se ausente)
--
-- Erros de script são capturados (pcall): avisam 1x no log e a engine segue.

local elapsed_in_script = 0

-- Top-level do chunk corre 1x quando a entidade ganha o script (on_add).
viber.log("example.lua carregado")

function on_update(dt)
	elapsed_in_script = elapsed_in_script + dt

	local px, py, pz = viber.position()
	local d = viber.distance_to_player()

	-- Pequeno movimento vertical demo: oscila ±1 m em volta da origem.
	viber.set_position(px, py + math.sin(elapsed_in_script * 2.0), pz)

	-- Log throttled a ~1 Hz para não inundar o console.
	if math.floor(elapsed_in_script) > math.floor(elapsed_in_script - dt) then
		viber.log(string.format(
			"tick t=%.1fs dt=%.3f pos=(%.1f, %.1f, %.1f) player=%s",
			viber.time(),
			dt,
			px,
			py,
			pz,
			d and string.format("%.1fm", d) or "ausente"
		))
	end

	if d and d < 5.0 then
		viber.log("player perto! (" .. string.format("%.1f", d) .. " m)")
	end
end
