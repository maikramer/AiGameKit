//! Instalação da tabela `viber` (API de jogo dos scripts): percepção,
//! movimento com snap no terreno, IA primitiva, combate/progressão, quests,
//! vault, interação. Os grupos NOVOS vivem em módulos próprios e são
//! compostos aqui (`input`, `events`, `timers`, `entity`, `game`).

use bevy::prelude::*;
use mlua::{Lua, Table};

use super::commands::ScriptCommand;
use super::ctx::ScriptCtx;
use super::{entity, events, game, input, terrain, timers};
use super::input::key_code_from_str;
use super::sfx::{sfx_clip_from_str, SFX_NAME_REGISTRY};

    /// Installs the `viber` API table on the VM globals:
    /// `log(msg)`, `time()`, `position()`, `set_position(x, y, z)`,
    /// `distance_to_player()`.
pub(crate) fn install(lua: &Lua) -> mlua::Result<()> {
        let api = lua.create_table()?;

        // viber.log(msg) — engine log + ring buffer.
        api.set(
            "log",
            lua.create_function(|lua, msg: String| {
                let entity = lua
                    .app_data_ref::<ScriptCtx>()
                    .and_then(|ctx| ctx.entity)
                    .map(|e| format!("{e:?}"))
                    .unwrap_or_else(|| "pre-activate".into());
                info!(target: "viber::luau", "[{entity}] {msg}");
                if let Some(mut ctx) = lua.app_data_mut::<ScriptCtx>() {
                    ctx.push_log(msg);
                }
                Ok(())
            })?,
        )?;

        // viber.time() — seconds since engine startup.
        api.set(
            "time",
            lua.create_function(|lua, ()| {
                let elapsed = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|ctx| ctx.elapsed)
                    .unwrap_or_default();
                Ok(elapsed)
            })?,
        )?;

        // viber.position() -> x, y, z — start-of-frame snapshot.
        api.set(
            "position",
            lua.create_function(|lua, ()| {
                let origin = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|ctx| ctx.origin)
                    .unwrap_or_default();
                Ok((origin.x, origin.y, origin.z))
            })?,
        )?;

        // viber.set_position(x, y, z) — queues a command applied post-frame.
        api.set(
            "set_position",
            lua.create_function(|lua, (x, y, z): (f32, f32, f32)| {
                if !(x.is_finite() && y.is_finite() && z.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.set_position: coordenadas não finitas (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime(
                        "viber.set_position outside on_update (no owning entity)",
                    ));
                };
                ctx.pending.push((entity, Vec3::new(x, y, z)));
                Ok(())
            })?,
        )?;

        // viber.distance_to_player() -> number | nil (nil = no player).
        api.set(
            "distance_to_player",
            lua.create_function(|lua, ()| {
                let (origin, player) = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|ctx| (ctx.origin, ctx.player))
                    .unwrap_or((Vec3::ZERO, None));
                Ok(player.map(|p| p.distance(origin)))
            })?,
        )?;

        // ── Estado por entidade ─────────────────────────────────────────
        // Chunks são partilhados entre entidades; o estado de CADA entidade
        // vive numa tabela separada (`states[entity_bits]`), criada à pressa.
        let state_fn = lua.create_function(|lua, ()| {
            let entity = lua
                .app_data_ref::<ScriptCtx>()
                .and_then(|ctx| ctx.entity)
                .ok_or_else(|| mlua::Error::runtime("viber.state fora de on_update"))?;
            let key = entity.to_bits() as i64;
            let states: Table = lua.named_registry_value("viber_states")?;
            let table = states.raw_get::<Table>(key).or_else(|_| {
                let fresh = lua.create_table()?;
                let _ = states.raw_set(key, fresh.clone());
                Ok::<Table, mlua::Error>(fresh)
            })?;
            Ok(table)
        })?;
        api.set("state", state_fn)?;

        // viber.self_name() -> string
        api.set(
            "self_name",
            lua.create_function(|lua, ()| {
                let name = lua
                    .app_data_ref::<ScriptCtx>()
                    .and_then(|ctx| ctx.entity)
                    .map(|e| format!("{e:?}"))
                    .unwrap_or_default();
                Ok(name)
            })?,
        )?;

        // viber.home() -> x, z — posição de SPAWN (centro do wander), lida
        // da tabela de estado gravada em `activate_at`; ctx.origin é a
        // posição ACTUAL e fazia scripts de leash orbitarem o sítio corrente.
        api.set(
            "home",
            lua.create_function(|lua, ()| {
                let (entity, fallback) = {
                    let ctx = lua.app_data_ref::<ScriptCtx>();
                    (
                        ctx.as_ref().and_then(|c| c.entity),
                        ctx.as_ref().map(|c| c.origin).unwrap_or_default(),
                    )
                };
                if let Some(entity) = entity {
                    let key = entity.to_bits() as i64;
                    let states: Table = lua.named_registry_value("viber_states")?;
                    if let Ok(state) = states.raw_get::<Table>(key) {
                        if let Ok(home) = state.raw_get::<Table>("home") {
                            let x: f32 = home.raw_get("x")?;
                            let z: f32 = home.raw_get("z")?;
                            return Ok((x, z));
                        }
                    }
                }
                Ok((fallback.x, fallback.z))
            })?,
        )?;

        // viber.player_position() -> x, y, z | nil
        api.set(
            "player_position",
            lua.create_function(|lua, ()| {
                let player = lua.app_data_ref::<ScriptCtx>().and_then(|ctx| ctx.player);
                match player {
                    Some(p) => Ok((true, p.x, p.y, p.z)),
                    None => Ok((false, 0.0, 0.0, 0.0)),
                }
            })?,
        )?;

        // viber.player_hp() -> ok, cur, max — snapshot do HP do herói
        // (o healer usa para recusar a cura com um gesto "no").
        api.set(
            "player_hp",
            lua.create_function(|lua, ()| {
                let hp = lua
                    .app_data_ref::<ScriptCtx>()
                    .and_then(|ctx| ctx.player_hp);
                match hp {
                    Some((cur, max)) => Ok((true, cur, max)),
                    None => Ok((false, 0.0, 0.0)),
                }
            })?,
        )?;

        // viber.ground_below(x, y, z) -> y | nil — a superfície sólida mais
        // alta em ou abaixo de `y` nesta coluna. Acima do mundo = o topo;
        // dentro de uma gruta = o piso da gruta; sob um arco = o chão do
        // vão. `nil` quando não há terreno sólido abaixo. É a query que
        // deixa uma criatura andar num túnel sem ser sentada na colina por
        // cima (os snaps de move_towards/move_by continuam a usar o topo).
        api.set(
            "ground_below",
            lua.create_function(|lua, (x, y, z): (f32, f32, f32)| {
                if !(x.is_finite() && y.is_finite() && z.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.ground_below: argumentos não finitos (NaN/inf)",
                    ));
                }
                let ground = lua
                    .app_data_ref::<ScriptCtx>()
                    .and_then(|ctx| ctx.terrain.clone())
                    .and_then(|reader| reader.voxel.surface_below(&reader.base(), x, z, y));
                Ok(ground)
            })?,
        )?;

        // viber.move_towards(x, z, speed) — passo deste frame na direção do
        // ponto; a engine senta o Y no terreno ao aplicar.
        api.set(
            "move_towards",
            lua.create_function(|lua, (x, z, speed): (f32, f32, f32)| {
                if !(x.is_finite() && z.is_finite() && speed.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.move_towards: argumentos não finitos (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.move_towards fora de on_update"));
                };
                ctx.commands.push(ScriptCommand::MoveTowards {
                    entity,
                    goal: Vec2::new(x, z),
                    speed,
                });
                Ok(())
            })?,
        )?;

        // viber.move_by(dx, dz) — passo relativo direto.
        api.set(
            "move_by",
            lua.create_function(|lua, (dx, dz): (f32, f32)| {
                if !(dx.is_finite() && dz.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.move_by: delta não finito (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.move_by fora de on_update"));
                };
                ctx.commands
                    .push(ScriptCommand::MoveBy(entity, Vec2::new(dx, dz)));
                Ok(())
            })?,
        )?;

        // viber.face_towards(x, z) / viber.face_player()
        let face_towards = |lua: &Lua| {
            lua.create_function(|lua, (x, z): (f32, f32)| {
                if !(x.is_finite() && z.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.face_towards: coordenadas não finitas (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.face fora de on_update"));
                };
                let target = Vec3::new(x, ctx.origin.y, z);
                ctx.commands
                    .push(ScriptCommand::FaceTowards(entity, target));
                Ok(())
            })
        };
        api.set("face_towards", face_towards(lua)?)?;
        api.set(
            "face_player",
            lua.create_function(|lua, ()| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let (Some(entity), Some(player)) = (ctx.entity, ctx.player) else {
                    return Ok(());
                };
                ctx.commands
                    .push(ScriptCommand::FaceTowards(entity, player));
                Ok(())
            })?,
        )?;

        // viber.on_road(x, z) / viber.in_water(x, z) — consultas ao mundo
        // carvado. Existiam na engine (`TerrainRuntime`) mas o Luau só tinha o
        // `ground_below`; sem elas um script não conseguia sequer perguntar
        // "estou na estrada?".
        api.set(
            "on_road",
            lua.create_function(|lua, (x, z): (f32, f32)| {
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(surfaces) = ctx.as_ref().and_then(|c| c.surfaces.as_ref()) else {
                    return Ok(false);
                };
                Ok(surfaces.on_road(x, z))
            })?,
        )?;
        api.set(
            "in_water",
            lua.create_function(|lua, (x, z): (f32, f32)| {
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(surfaces) = ctx.as_ref().and_then(|c| c.surfaces.as_ref()) else {
                    return Ok(false);
                };
                Ok(surfaces.in_water(x, z))
            })?,
        )?;

        // viber.set_locomotion(walk, run [, turn_rate]) — nominais explícitos
        // do rig. Só é preciso quando a auto-calibração pelas velocidades
        // comandadas não serve (ex. um rig cujo clip `walk` foi assado a uma
        // cadência diferente da velocidade a que o script o move).
        api.set(
            "set_locomotion",
            lua.create_function(
                |lua, (walk, run, turn_rate): (f32, f32, Option<f32>)| {
                    if !(walk.is_finite() && run.is_finite()) || walk <= 0.0 || run <= 0.0 {
                        return Err(mlua::Error::runtime(
                            "viber.set_locomotion: walk/run têm de ser finitos e > 0",
                        ));
                    }
                    if turn_rate.is_some_and(|t| !t.is_finite() || t <= 0.0) {
                        return Err(mlua::Error::runtime(
                            "viber.set_locomotion: turn_rate tem de ser finito e > 0",
                        ));
                    }
                    let mut ctx = lua
                        .app_data_mut::<ScriptCtx>()
                        .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                    let Some(entity) = ctx.entity else {
                        return Err(mlua::Error::runtime(
                            "viber.set_locomotion fora de on_update",
                        ));
                    };
                    ctx.commands.push(ScriptCommand::SetLocomotion {
                        entity,
                        walk,
                        run,
                        turn_rate,
                    });
                    Ok(())
                },
            )?,
        )?;

        // viber.gesture(name) — gesto one-shot no rig da entidade; o fuzzy
        // match contra os clips do GLB é feito na aplicação (pós-frame) e
        // alternativas separadas por vírgula são tentadas por ordem.
        api.set(
            "gesture",
            lua.create_function(|lua, name: String| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.gesture fora de on_update"));
                };
                ctx.commands.push(ScriptCommand::Gesture {
                    entity,
                    name,
                    speed: 1.0,
                });
                Ok(())
            })?,
        )?;

        // viber.sound(clip) — SFX curto na posição da entidade (volume cai
        // com a distância à câmara). Nome inválido = erro de script (warn 1×).
        api.set(
            "sound",
            lua.create_function(|lua, name: String| {
                let clip = sfx_clip_from_str(&name).ok_or_else(|| {
                    let names: Vec<&str> = SFX_NAME_REGISTRY.iter().map(|(n, _)| *n).collect();
                    mlua::Error::runtime(format!(
                        "clip de som desconhecido '{name}' ({})",
                        names.join(", ")
                    ))
                })?;
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let position = Some(ctx.origin);
                ctx.commands.push(ScriptCommand::PlaySfx { clip, position });
                Ok(())
            })?,
        )?;

        // viber.wander_target(radius) -> x, z — ponto determinístico ao redor
        // do home (mesma matemática da IA da engine, exposta ao script).
        api.set(
            "wander_target",
            lua.create_function(|lua, radius: f32| {
                if !radius.is_finite() {
                    return Err(mlua::Error::runtime(
                        "viber.wander_target: raio não finito (NaN/inf)",
                    ));
                }
                // Só os campos necessários (entity): clonar o ctx inteiro
                // (quest_states + vault) por chamada eram milhares de
                // alocações/frame com ~100 scripts activos.
                let entity = lua
                    .app_data_ref::<ScriptCtx>()
                    .and_then(|c| c.entity)
                    .ok_or_else(|| mlua::Error::runtime("viber.wander_target fora de on_update"))?;
                let states: Table = lua.named_registry_value("viber_states")?;
                let key = entity.to_bits() as i64;
                let state: Table = states.raw_get::<Table>(key).map_err(|_| {
                    mlua::Error::runtime("viber.wander_target antes de viber.state()")
                })?;
                let home: Table = state.raw_get("home")?;
                let home = Vec2::new(home.raw_get("x")?, home.raw_get("z")?);
                let picks: u64 = state.raw_get("picks").unwrap_or(0u64);
                state.raw_set("picks", picks + 1)?;
                let seed = crate::ai::enemy_seed(entity.to_bits() as u32, picks);
                let target = crate::ai::wander_target(home, radius, seed);
                Ok((target.x, target.y))
            })?,
        )?;

        // viber.next_state(cur, dist, aggro, deaggro) -> "wander"|"chase"
        // A máquina wander↔chase da engine, exposta para os scripts comporem.
        api.set(
            "next_state",
            lua.create_function(|_, (cur, dist, aggro, deaggro): (String, f32, f32, f32)| {
                let state = match cur.as_str() {
                    "chase" => crate::ai::EnemyState::Chase,
                    _ => crate::ai::EnemyState::Wander,
                };
                match crate::ai::enemy_next_state(dist, state, aggro, deaggro) {
                    crate::ai::EnemyState::Chase => Ok("chase"),
                    crate::ai::EnemyState::Wander => Ok("wander"),
                }
            })?,
        )?;

        // ── Player / combate / progressão ───────────────────────────────
        api.set(
            "damage_player",
            lua.create_function(|lua, amount: f32| {
                if !amount.is_finite() {
                    return Err(mlua::Error::runtime(
                        "viber.damage_player: amount não finito (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let from = ctx.origin;
                ctx.commands.push(ScriptCommand::DamagePlayer {
                    amount,
                    from: Some(from),
                });
                Ok(())
            })?,
        )?;
        // viber.fire_projectile(id [, x, y, z]) — dispara um projétil do
        // `<ProjectileTemplate id=…>` da boca da entidade para o peito do
        // herói (ou para o ponto dado). `false` quando não há alvo.
        api.set(
            "fire_projectile",
            lua.create_function(
                |lua, (template, x, y, z): (String, Option<f32>, Option<f32>, Option<f32>)| {
                    let mut ctx = lua
                        .app_data_mut::<ScriptCtx>()
                        .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                    let target = match (x, y, z) {
                        (Some(x), Some(y), Some(z)) => Vec3::new(x, y, z),
                        (None, None, None) => match ctx.player {
                            Some(p) => p + Vec3::Y * crate::projectile::CHEST_HEIGHT,
                            None => return Ok(false),
                        },
                        _ => {
                            return Err(mlua::Error::runtime(
                                "viber.fire_projectile: alvo precisa de x, y e z",
                            ));
                        }
                    };
                    if !target.is_finite() {
                        return Err(mlua::Error::runtime(
                            "viber.fire_projectile: alvo não finito (NaN/inf)",
                        ));
                    }
                    let origin = ctx.origin + Vec3::Y * crate::projectile::MUZZLE_HEIGHT;
                    let shooter = ctx.entity;
                    ctx.commands.push(ScriptCommand::FireProjectile {
                        template,
                        origin,
                        target,
                        shooter,
                    });
                    Ok(true)
                },
            )?,
        )?;
        api.set(
            "topple",
            lua.create_function(|lua, ()| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.topple fora de on_update"));
                };
                ctx.commands.push(ScriptCommand::Topple { entity });
                Ok(())
            })?,
        )?;
        api.set(
            "heal_player",
            lua.create_function(|lua, amount: f32| {
                if !amount.is_finite() {
                    return Err(mlua::Error::runtime(
                        "viber.heal_player: amount não finito (NaN/inf)",
                    ));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::HealPlayer(amount));
                Ok(())
            })?,
        )?;
        api.set(
            "apply_status",
            lua.create_function(|lua, (kind, secs): (String, f32)| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::ApplyStatus { kind, secs });
                Ok(())
            })?,
        )?;

        // ── Quests ──────────────────────────────────────────────────────
        api.set(
            "quest_state",
            lua.create_function(|lua, id: String| {
                let ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                Ok(ctx
                    .quest_states
                    .get(&id)
                    .cloned()
                    .unwrap_or_else(|| "unknown".into()))
            })?,
        )?;
        api.set(
            "quest_accept",
            lua.create_function(|lua, id: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::QuestAccept(id));
                Ok(())
            })?,
        )?;
        api.set(
            "quest_turn_in",
            lua.create_function(|lua, id: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::QuestTurnIn(id));
                Ok(())
            })?,
        )?;
        api.set(
            "report_kill",
            lua.create_function(|lua, kind: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::QuestReport {
                        target: kind,
                        amount: 1,
                    });
                Ok(())
            })?,
        )?;
        // Colheita deposita no VAULT — os objetivos collect das quests leem o
        // inventário (auto-progress).
        api.set(
            "report_collect",
            lua.create_function(|lua, (item, amount): (String, u32)| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::VaultAdd {
                        kind: item,
                        amount,
                        from_collect: true,
                    });
                Ok(())
            })?,
        )?;
        api.set(
            "vault_add",
            lua.create_function(|lua, (kind, amount): (String, u32)| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::VaultAdd {
                        kind,
                        amount,
                        from_collect: false,
                    });
                Ok(())
            })?,
        )?;
        api.set(
            "vault_get",
            lua.create_function(|lua, kind: String| {
                let ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                Ok(ctx.vault.get(&kind).copied().unwrap_or(0))
            })?,
        )?;
        // viber.vault_take(kind, amount) — consome do vault (recurso OU
        // item); sem stock = no-op com warn 1×. É o que deixa um jogo
        // implementar a SUA loja/crafting em Lua (pagar ouro, gastar itens).
        api.set(
            "vault_take",
            lua.create_function(|lua, (kind, amount): (String, u32)| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::VaultTake { kind, amount });
                Ok(())
            })?,
        )?;
        // ── Combate & FX (primitivas de jogo) ─────────────────────────
        // viber.entity_position(id?) -> x, y, z | nil — snapshot do frame.
        api.set(
            "entity_position",
            lua.create_function(|lua, id: Option<i64>| {
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(ctx) = ctx.as_ref() else {
                    return Ok((false, 0.0, 0.0, 0.0));
                };
                let bits = match id {
                    Some(bits) => bits,
                    None => match ctx.entity {
                        Some(e) => e.to_bits() as i64,
                        None => return Ok((false, 0.0, 0.0, 0.0)),
                    },
                };
                match ctx.pos_snapshot.get(&bits) {
                    Some((_, pos)) => Ok((true, pos.x, pos.y, pos.z)),
                    None => Ok((false, 0.0, 0.0, 0.0)),
                }
            })?,
        )?;
        // viber.nearby(raio [, limite]) -> { {id, name, x, y, z, distance}, … }
        // — entidades NOMEADAS à volta da entidade corrente, mais perto 1.º.
        api.set(
            "nearby",
            lua.create_function(|lua, (radius, limit): (f32, Option<usize>)| {
                let out = lua.create_table()?;
                if !radius.is_finite() || radius <= 0.0 {
                    return Err(mlua::Error::runtime(
                        "viber.nearby: raio tem de ser finito e > 0",
                    ));
                }
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(ctx) = ctx.as_ref() else {
                    return Ok(out);
                };
                let mut found: Vec<(f32, i64, String, Vec3)> = ctx
                    .pos_snapshot
                    .iter()
                    .map(|(bits, (name, pos))| (pos.distance(ctx.origin), *bits, name.clone(), *pos))
                    .filter(|(d, ..)| *d <= radius)
                    .collect();
                found.sort_by(|a, b| a.0.total_cmp(&b.0));
                let cap = limit.unwrap_or(16).min(64);
                for (i, (distance, bits, name, pos)) in found.into_iter().take(cap).enumerate() {
                    let t = lua.create_table()?;
                    t.raw_set("id", bits)?;
                    t.raw_set("name", name)?;
                    t.raw_set("x", pos.x)?;
                    t.raw_set("y", pos.y)?;
                    t.raw_set("z", pos.z)?;
                    t.raw_set("distance", distance)?;
                    out.raw_set(i + 1, t)?;
                }
                Ok(out)
            })?,
        )?;
        // viber.player_forward() -> dx, dz — forward do herói no plano.
        api.set(
            "player_forward",
            lua.create_function(|lua, ()| {
                let forward = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|c| c.player_forward)
                    .unwrap_or_default();
                Ok((forward.x, forward.y))
            })?,
        )?;
        // viber.radial_damage(x, z, raio, dano [, opts]) — dano em área com
        // falloff linear; opts { knockback = força }. Mortes seguem a
        // paridade nativa (corpo, XP, quests, evento Kill).
        api.set(
            "radial_damage",
            lua.create_function(
                |lua, (x, z, radius, damage, opts): (f32, f32, f32, f32, Option<Table>)| {
                    if !(x.is_finite() && z.is_finite() && radius.is_finite() && damage.is_finite())
                        || radius <= 0.0
                        || damage < 0.0
                    {
                        return Err(mlua::Error::runtime(
                            "viber.radial_damage: valores têm de ser finitos (raio > 0, dano >= 0)",
                        ));
                    }
                    let knockback = opts
                        .and_then(|t| t.raw_get::<f32>("knockback").ok())
                        .unwrap_or(0.0);
                    if !knockback.is_finite() || knockback < 0.0 {
                        return Err(mlua::Error::runtime(
                            "viber.radial_damage: knockback tem de ser finito e >= 0",
                        ));
                    }
                    lua.app_data_mut::<ScriptCtx>()
                        .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                        .commands
                        .push(ScriptCommand::RadialDamage {
                            x,
                            z,
                            radius,
                            damage,
                            knockback,
                        });
                    Ok(())
                },
            )?,
        )?;
        // viber.burst(preset, x, y, z [, count]) — preset validado contra a
        // biblioteca (`particles::PRESET_NAMES`).
        api.set(
            "burst",
            lua.create_function(
                |lua, (preset, x, y, z, count): (String, f32, f32, f32, Option<usize>)| {
                    if !(x.is_finite() && y.is_finite() && z.is_finite()) {
                        return Err(mlua::Error::runtime(
                            "viber.burst: coordenadas não finitas (NaN/inf)",
                        ));
                    }
                    // `sparks` é o preset de impacto do melee (fora da
                    // biblioteca — ver `luau::fx::ScriptFx::burst`).
                    if preset != "sparks"
                        && !crate::particles::PRESET_NAMES.contains(&preset.as_str())
                    {
                        return Err(mlua::Error::runtime(format!(
                            "viber.burst: preset desconhecido '{preset}' (válidos: {} sparks)",
                            crate::particles::PRESET_NAMES.join(", ")
                        )));
                    }
                    lua.app_data_mut::<ScriptCtx>()
                        .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                        .commands
                        .push(ScriptCommand::Burst {
                            preset,
                            pos: Vec3::new(x, y, z),
                            count: count.unwrap_or(12),
                        });
                    Ok(())
                },
            )?,
        )?;
        // viber.ring(x, z, raio [, cor "#hex"]) — anel de choque no chão.
        api.set(
            "ring",
            lua.create_function(|lua, (x, z, radius, color): (f32, f32, f32, Option<String>)| {
                if !(x.is_finite() && z.is_finite() && radius.is_finite()) || radius <= 0.0 {
                    return Err(mlua::Error::runtime(
                        "viber.ring: coordenadas finitas e raio > 0",
                    ));
                }
                let color = match color {
                    Some(hex) => Some(crate::xml::values::parse_color(&hex, "viber.ring").map_err(
                        |e| mlua::Error::runtime(format!("viber.ring: cor inválida ({e})")),
                    )?),
                    None => None,
                };
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::Ring {
                        x,
                        z,
                        radius,
                        color,
                    });
                Ok(())
            })?,
        )?;
        // Câmara & feedback: shake/kick/fov_kick/punch/hit_stop/damage_number.
        api.set(
            "shake",
            lua.create_function(|lua, amount: f32| {
                if !amount.is_finite() || amount < 0.0 {
                    return Err(mlua::Error::runtime("viber.shake: valor finito e >= 0"));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::Shake(amount));
                Ok(())
            })?,
        )?;
        api.set(
            "kick",
            lua.create_function(|lua, (x, y, z): (f32, f32, f32)| {
                if !(x.is_finite() && y.is_finite() && z.is_finite()) {
                    return Err(mlua::Error::runtime("viber.kick: valores não finitos"));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::CameraKick(Vec3::new(x, y, z)));
                Ok(())
            })?,
        )?;
        api.set(
            "fov_kick",
            lua.create_function(|lua, deg: f32| {
                if !deg.is_finite() {
                    return Err(mlua::Error::runtime("viber.fov_kick: valor não finito"));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::FovKick(deg));
                Ok(())
            })?,
        )?;
        api.set(
            "punch",
            lua.create_function(|lua, (stops, bloom): (f32, f32)| {
                if !(stops.is_finite() && bloom.is_finite()) {
                    return Err(mlua::Error::runtime("viber.punch: valores não finitos"));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::Punch { stops, bloom });
                Ok(())
            })?,
        )?;
        api.set(
            "hit_stop",
            lua.create_function(|lua, secs: f32| {
                if !secs.is_finite() || secs < 0.0 {
                    return Err(mlua::Error::runtime("viber.hit_stop: segundos finitos e >= 0"));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::HitStop(secs));
                Ok(())
            })?,
        )?;
        api.set(
            "damage_number",
            lua.create_function(|lua, (text, opts): (String, Option<Table>)| {
                let (pos, color) = match opts {
                    Some(t) => {
                        let x = t.raw_get::<f32>("x").ok();
                        let y = t.raw_get::<f32>("y").ok();
                        let z = t.raw_get::<f32>("z").ok();
                        let hex: Option<String> = t.raw_get("color").ok();
                        let color = match hex {
                            Some(hex) => Some(
                                crate::xml::values::parse_color(&hex, "viber.damage_number")
                                    .map_err(|e| {
                                        mlua::Error::runtime(format!(
                                            "viber.damage_number: cor inválida ({e})"
                                        ))
                                    })?,
                            ),
                            None => None,
                        };
                        (Some((x, y, z)), color)
                    }
                    None => (None, None),
                };
                let pos = match pos {
                    Some((Some(x), Some(y), Some(z))) if x.is_finite() && y.is_finite() && z.is_finite() => {
                        Vec3::new(x, y, z)
                    }
                    _ => {
                        let ctx = lua.app_data_ref::<ScriptCtx>();
                        ctx.as_ref().map(|c| c.origin).unwrap_or_default()
                            + Vec3::Y * 1.8
                    }
                };
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::DamageNumber { text, pos, color });
                Ok(())
            })?,
        )?;
        // viber.play_clip(nome [, opts]) — clip de ação no rig (opts:
        // {speed = 1.0, id = bits}); generaliza o `viber.gesture` com
        // velocidade (mine/chop do rig do herói) e alvo.
        api.set(
            "play_clip",
            lua.create_function(|lua, (name, opts): (String, Option<Table>)| {
                let (speed, id) = match opts {
                    Some(t) => (
                        t.raw_get::<f32>("speed").ok().unwrap_or(1.0),
                        t.raw_get::<i64>("id").ok(),
                    ),
                    None => (1.0, None),
                };
                if !speed.is_finite() || speed <= 0.0 {
                    return Err(mlua::Error::runtime(
                        "viber.play_clip: speed tem de ser finito e > 0",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let entity = match id {
                    Some(bits) => entity::entity_from_id(bits)?,
                    None => ctx.entity.ok_or_else(|| {
                        mlua::Error::runtime("viber.play_clip fora de on_update (sem id)")
                    })?,
                };
                ctx.commands.push(ScriptCommand::Gesture {
                    entity,
                    name,
                    speed,
                });
                Ok(())
            })?,
        )?;
        // viber.entity_despawn(id) — remove uma entidade arbitrária (o
        // `despawn_self` é o atalho para a própria).
        api.set(
            "entity_despawn",
            lua.create_function(|lua, id: i64| {
                let target = entity::entity_from_id(id)?;
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::Despawn(target));
                Ok(())
            })?,
        )?;
        // viber.status_clear(kind?) — limpa um status do herói (hoje: venom).
        api.set(
            "status_clear",
            lua.create_function(|lua, kind: Option<String>| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("seeded")
                    .commands
                    .push(ScriptCommand::StatusClear(
                        kind.unwrap_or_else(|| "venom".to_string()),
                    ));
                Ok(())
            })?,
        )?;

        // viber.own_system(nome) — reclama um sistema NATIVO para Lua
        // (`dialogue`, `abilities`, `bomb`, `guard`, `hotbar`, `harvest`): o
        // handler nativo correspondente cala e a lógica passa a ser do
        // script. Sem posse, o comportamento nativo é o de sempre (compat).
        api.set(
            "own_system",
            lua.create_function(|lua, name: String| {
                if !super::ownership::KNOWN_SYSTEMS.contains(&name.as_str()) {
                    return Err(mlua::Error::runtime(format!(
                        "viber.own_system: sistema desconhecido '{name}' (válidos: {})",
                        super::ownership::KNOWN_SYSTEMS.join(", ")
                    )));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::OwnSystem(name));
                Ok(())
            })?,
        )?;
        // viber.say(texto [, segundos]) — escreve no BALÃO nativo do HUD
        // (o mesmo do diálogo de quests). Sem `<DialogueBalloon>` no mundo é
        // no-op com warn 1×.
        api.set(
            "say",
            lua.create_function(|lua, (text, secs): (String, Option<f32>)| {
                let secs = secs.unwrap_or(0.0);
                if !secs.is_finite() || secs < 0.0 {
                    return Err(mlua::Error::runtime(
                        "viber.say: segundos têm de ser finitos e >= 0",
                    ));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::Say { text, secs });
                Ok(())
            })?,
        )?;
        // viber.quest_def(id) -> tabela | nil; viber.quest_defs() -> lista.
        // As MESMAS definições autorais do `<mundo>/quests/*.json` (título,
        // linhas, objetivo, recompensas) — um diálogo 100 % Lua reusa-as.
        let def_to_table = |lua: &Lua, d: &super::ctx::QuestDefLite| -> mlua::Result<Table> {
            let t = lua.create_table()?;
            t.raw_set("id", d.id.clone())?;
            t.raw_set("title", d.title.clone())?;
            t.raw_set("npc", d.npc.clone())?;
            t.raw_set("biome", d.biome.clone())?;
            t.raw_set("kind", d.kind.clone())?;
            t.raw_set("target", d.target.clone())?;
            t.raw_set("count", d.count)?;
            t.raw_set("radius", d.radius)?;
            t.raw_set("gold", d.gold)?;
            t.raw_set("xp", d.xp)?;
            let items = lua.create_table()?;
            for (i, item) in d.items.iter().enumerate() {
                items.raw_set(i + 1, item.clone())?;
            }
            t.raw_set("items", items)?;
            let lines = |vals: &[String]| -> mlua::Result<Table> {
                let out = lua.create_table()?;
                for (i, line) in vals.iter().enumerate() {
                    out.raw_set(i + 1, line.clone())?;
                }
                Ok(out)
            };
            t.raw_set("lines_intro", lines(&d.lines_intro)?)?;
            t.raw_set("lines_progress", lines(&d.lines_progress)?)?;
            t.raw_set("lines_complete", lines(&d.lines_complete)?)?;
            Ok(t)
        };
        api.set(
            "quest_def",
            lua.create_function(move |lua, id: String| {
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(def) = ctx
                    .as_ref()
                    .and_then(|c| c.quest_defs.iter().find(|d| d.id == id))
                else {
                    return Ok(mlua::Value::Nil);
                };
                Ok(mlua::Value::Table(def_to_table(lua, def)?))
            })?,
        )?;
        api.set(
            "quest_defs",
            lua.create_function(move |lua, ()| {
                let out = lua.create_table()?;
                let defs: Vec<super::ctx::QuestDefLite> = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|c| c.quest_defs.clone())
                    .unwrap_or_default();
                for (i, def) in defs.iter().enumerate() {
                    out.raw_set(i + 1, def_to_table(lua, def)?)?;
                }
                Ok(out)
            })?,
        )?;

        // viber.save() / viber.load_save() — gravam/carregam o save do mundo
        // (o mesmo ficheiro do botão Guardar/Carregar do menu; funciona em
        // QUALQUER preset — um jogo sem RPG usa para persistir o game()
        // e a posição). O pedido é drenado no mesmo frame pelo save.
        // NOTA: `load_save` e não `load` — `viber.load` é o carregador de
        // MÓDULOS (`viber.load("lib/fsm.lua")`) desde a fase A; os dois
        // nomes colidiam e um tapava o outro.
        api.set(
            "save",
            lua.create_function(|lua, ()| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::SaveNow);
                Ok(())
            })?,
        )?;
        api.set(
            "load_save",
            lua.create_function(|lua, ()| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::LoadNow);
                Ok(())
            })?,
        )?;

        // viber.spawn_prototype(nome, x, z [, opts]) — instancia um
        // `<Prototype>` do mundo. opts: {y = cota explícita (default: assenta
        // no terreno), on_spawned = fn(bits)}. O spawn real é pós-frame
        // (sistema exclusivo, `recipes::spawn::apply_script_spawns`).
        api.set(
            "spawn_prototype",
            lua.create_function(
                |lua, (name, x, z, opts): (String, f32, f32, Option<Table>)| {
                    if !(x.is_finite() && z.is_finite()) {
                        return Err(mlua::Error::runtime(
                            "viber.spawn_prototype: coordenadas não finitas (NaN/inf)",
                        ));
                    }
                    let (y, on_spawned) = match opts {
                        Some(t) => (
                            t.raw_get::<f32>("y").ok(),
                            t.raw_get::<mlua::Function>("on_spawned").ok(),
                        ),
                        None => (None, None),
                    };
                    if let Some(y) = y {
                        if !y.is_finite() {
                            return Err(mlua::Error::runtime(
                                "viber.spawn_prototype: y não finito (NaN/inf)",
                            ));
                        }
                    }
                    // Validado à fila: prototype desconhecido é ERRO de
                    // script (o transbordo silencioso era o pior caso).
                    let known = lua
                        .app_data_ref::<ScriptCtx>()
                        .map(|c| c.prototype_names.iter().any(|n| n == &name))
                        .unwrap_or(false);
                    if !known {
                        return Err(mlua::Error::runtime(format!(
                            "viber.spawn_prototype: prototype desconhecido '{name}'"
                        )));
                    }
                    let mut ctx = lua
                        .app_data_mut::<ScriptCtx>()
                        .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                    let caller_path = ctx.path.clone();
                    ctx.commands.push(ScriptCommand::SpawnPrototype {
                        name,
                        pos: Vec3::new(x, y.unwrap_or(0.0), z),
                        seat: y.is_none(),
                        on_spawned,
                        caller_path,
                    });
                    Ok(())
                },
            )?,
        )?;
        // viber.own_action(nome) — reclama uma ação da UI para Lua: o
        // handler nativo cala e a ação chega via `viber.events()` como
        // {type="ui_action", name=..., arg=...}. Sem dono, o comportamento
        // nativo é o de sempre (compat).
        api.set(
            "own_action",
            lua.create_function(|lua, name: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::OwnUiAction(name));
                Ok(())
            })?,
        )?;
        api.set(
            "item_add",
            lua.create_function(|lua, (id, amount): (String, u32)| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::ItemAdd { id, amount });
                Ok(())
            })?,
        )?;
        api.set(
            "item_count",
            lua.create_function(|lua, id: String| {
                let ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                Ok(ctx.vault.get(&id).copied().unwrap_or(0))
            })?,
        )?;
        api.set(
            "alive_in_region",
            lua.create_function(|lua, idx: usize| {
                let ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                Ok(ctx.alive_regions.get(idx).copied().unwrap_or(0))
            })?,
        )?;
        api.set(
            "report_visit",
            lua.create_function(|lua, place: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::QuestVisit(place));
                Ok(())
            })?,
        )?;
        api.set(
            "add_xp",
            lua.create_function(|lua, gain: u32| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::AddXp(gain));
                Ok(())
            })?,
        )?;
        // viber.despawn_self() — a entidade se remove (árvore derrubada etc).
        api.set(
            "despawn_self",
            lua.create_function(|lua, ()| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.despawn_self fora de on_update"));
                };
                ctx.commands.push(ScriptCommand::Despawn(entity));
                Ok(())
            })?,
        )?;
        api.set(
            "teleport_player",
            lua.create_function(|lua, (x, y, z): (f32, f32, f32)| {
                if !(x.is_finite() && y.is_finite() && z.is_finite()) {
                    return Err(mlua::Error::runtime(
                        "viber.teleport_player: coordenadas não finitas (NaN/inf)",
                    ));
                }
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::TeleportPlayer(Vec3::new(x, y, z)));
                Ok(())
            })?,
        )?;

        // ── UI / interação ──────────────────────────────────────────────
        api.set(
            "toast",
            lua.create_function(|lua, msg: String| {
                lua.app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                    .commands
                    .push(ScriptCommand::Toast(msg));
                Ok(())
            })?,
        )?;
        api.set(
            "set_interaction",
            lua.create_function(|lua, (label, key, range): (String, String, Option<f32>)| {
                // Validado à fila (erro de script → warn 1×): a aplicação
                // pós-frame largava a tecla desconhecida EM SILÊNCIO — o
                // alvo de interação nunca aparecia sem diagnóstico.
                key_code_from_str(&key).ok_or_else(|| {
                    mlua::Error::runtime(format!(
                        "viber.set_interaction: tecla desconhecida '{key}'"
                    ))
                })?;
                // O default é o alcance BASE autorado; a escala global entra
                // no `SetInteraction` (um só sítio).
                let range = range.unwrap_or(crate::interact::BASE_RANGE_M);
                if !range.is_finite() {
                    return Err(mlua::Error::runtime(
                        "viber.set_interaction: range não finito (NaN/inf)",
                    ));
                }
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime(
                        "viber.set_interaction fora de on_update",
                    ));
                };
                ctx.commands.push(ScriptCommand::SetInteraction {
                    entity,
                    label,
                    key,
                    range,
                });
                Ok(())
            })?,
        )?;
        // viber.interacted(key) -> bool — tecla pressionada NESTE frame E
        // ESTA entidade é o alvo mais próximo dessa tecla
        // (`interact::InteractionFocus`). O alcance sozinho não chega: dois
        // NPC sobrepostos reagiam ambos ao mesmo [E].
        api.set(
            "interacted",
            lua.create_function(|lua, key: String| {
                let code = key_code_from_str(&key)
                    .ok_or_else(|| mlua::Error::runtime(format!("tecla desconhecida '{key}'")))?;
                let ctx = lua.app_data_ref::<ScriptCtx>();
                let Some(ctx) = ctx.as_ref() else {
                    return Ok(false);
                };
                if !ctx.just_pressed.contains(&code) {
                    return Ok(false);
                }
                let Some(entity) = ctx.entity else {
                    return Ok(false);
                };
                // Sem foco publicado ainda (1.º frame, ou entidade que ainda
                // não registou `set_interaction`): cai no teste de alcance de
                // sempre, para não perder a interação do frame de arranque.
                if let Some(winner) = ctx.interaction_focus.get(&code).copied() {
                    return Ok(winner == entity);
                }
                Ok(ctx
                    .player
                    .map(|p| {
                        p.distance(ctx.origin)
                            <= ctx
                                .interaction_range
                                .unwrap_or_else(crate::interact::default_range)
                    })
                    .unwrap_or(false))
            })?,
        )?;

        input::install(lua, &api)?;
        events::install(lua, &api)?;
        terrain::install(lua, &api)?;
        timers::install(lua, &api)?;
        entity::install(lua, &api)?;
        game::install(lua, &api)?;


        lua.globals().set("viber", api)?;
        let states = lua.create_table()?;
        lua.set_named_registry_value("viber_states", states)?;
        // Filas de eventos por-path (`viber.events`): path → array de tabelas.
        // A presença da chave É a subscrição (criada na 1.ª chamada).
        let events = lua.create_table()?;
        lua.set_named_registry_value("viber_events", events)?;
        // Filas de timers (`viber.after`/`every`): id → {at, period?, func, owner, path}.
        lua.set_named_registry_value("viber_timers", lua.create_table()?)?;
        let seq = lua.create_table()?;
        seq.raw_set("next", 1i64)?;
        lua.set_named_registry_value("viber_timer_seq", seq)?;
        lua.set_named_registry_value("viber_timer_cancelled", lua.create_table()?)?;
        // Estado de JOGO world-scoped (`viber.game()`) e cache de módulos
        // (`viber.load`) — ambos sobrevivem ao hot-reload.
        lua.set_named_registry_value("viber_game", lua.create_table()?)?;
        lua.set_named_registry_value("viber_modules", lua.create_table()?)?;
        Ok(())
    }
