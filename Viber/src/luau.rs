//! Luau scripting runtime: each `world_dir/scripts/<path>` chunk defines
//! `function on_update(dt)` and drives its owner entity through the `viber`
//! API (`log`, `time`, `position`, `set_position`, `distance_to_player`).
//!
//! Design notes:
//! - One shared sandboxed Luau VM ([`LuaScriptHost`]); every script chunk is
//!   compiled with its own environment table (`__index` → real globals) so
//!   scripts cannot clobber each other's globals.
//! - The owning entity is *injected* into each call: before invoking
//!   `on_update` the system stores a [`ScriptCtx`] snapshot (entity, position
//!   snapshot, player position, clock) as Lua app data; the `viber` closures
//!   read it back. `set_position` only queues a command — applied to
//!   [`bevy::math::Vec3`]-bearing entities after all scripts ran. No `unsafe`,
//!   no raw `World` pointers.
//! - Script errors are pcall-style: reported once per script path
//!   ([`warn_once`]) and never abort the engine.
//!
//! WIRED-BY-ORCHESTRATOR: [`LuaScriptRef`] is inserted by the spawn step
//! (recipes/spawn) on entities that declare `<script src="...">`; the
//! orchestrator also adds [`LuauScriptPlugin`] to the `App` with the world's
//! `scripts/` directory.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use crate::player::Player;
use crate::vitals::{Health, Xp};
use bevy::prelude::*;
use mlua::{Function, Lua, Table};

/// A component marking an entity as owned by a Luau script (`scripts/<path>`
/// relative to the world directory).
///
/// WIRED-BY-ORCHESTRATOR: inserted by the spawn step; this module only
/// observes it (`on_add` / update / `on_remove`).
#[derive(Debug, Clone, Component)]
pub struct LuaScriptRef {
    /// Script path relative to `world_dir/scripts/` (e.g. `"doors/gate.lua"`).
    pub path: String,
}

/// Comandos que scripts enfileiram e a engine aplica pós-frame. A engine
/// provê os BLOCOS PRIMITIVOS (percepção, movimento com snap no terreno,
/// combate, UI) — a composição de comportamento vive no Luau.
#[derive(Debug, Clone)]
pub enum ScriptCommand {
    /// Delta XZ desejado; a engine aplica e senta o Y no terreno.
    MoveBy(Entity, Vec2),
    /// Vira a entidade para olhar um ponto (yaw only).
    FaceTowards(Entity, Vec3),
    TeleportPlayer(Vec3),
    AddXp(u32),
    DamagePlayer { amount: f32, from: Option<Vec3> },
    HealPlayer(f32),
    /// Aplica um status effect ao herói (hoje: `"venom"`) — tratado pelo
    /// feedback (tick 1/s, path único de dano).
    ApplyStatus { kind: String, secs: f32 },
    /// Quests: aceitar / entregar / reportar progresso (hooks para scripts,
    /// aplicados pelo [`crate::quests::QuestLog`]).
    QuestAccept(String),
    QuestTurnIn(String),
    /// kill/collect: alvo + quantidade.
    QuestReport { target: String, amount: u32 },
    /// Visita a um marco nomeado.
    QuestVisit(String),
    /// Destrutível com queda (`break-style: fall`) — tomba na direção
    /// herói→entidade e despawna no fim.
    Topple { entity: Entity },
    /// Deposita recurso no vault (`gold`/`wood`/`stone`) — economia loop 4.
    VaultAdd { kind: String, amount: u32 },
    /// Adiciona item ao inventário (`potion`, `antidote`, `bomb`…).
    ItemAdd { id: String, amount: u32 },
    /// Mensagem de UI (balão/toast) — consumida via [`ScriptToast`].
    Toast(String),
    /// Registra alvo de interação ("[J] Minerar") na entidade.
    SetInteraction {
        entity: Entity,
        label: String,
        key: String,
        range: f32,
    },
    Despawn(Entity),
}

/// Evento disparado quando um script pede `viber.toast(msg)` — o HUD pode
/// consumir; enquanto isso cada toast também vai para o log (bridge).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct ScriptToast(pub String);

/// Raio de ativação do script ("LOD de IA"): além deste raio do player o
/// `on_update` NEM RODA — inimigo congelado (lógica + animação paradas).
/// Autoria via `activation-radius` no spawner; default 45 m.
#[derive(Debug, Clone, Component)]
pub struct ScriptActivation {
    pub radius: f32,
}

impl Default for ScriptActivation {
    fn default() -> Self {
        Self {
            radius: DEFAULT_ACTIVATION_RADIUS,
        }
    }
}

/// Distância padrão de congelamento total de scripts de criatura (m).
pub const DEFAULT_ACTIVATION_RADIUS: f32 = 45.0;

/// Alvo de interação registado por script (`viber.set_interaction`): o prompt
/// "[tecla] label" aparece quando o player está perto.
#[derive(Debug, Clone, Component)]
pub struct ScriptInteraction {
    pub label: String,
    pub key: KeyCode,
    /// Distância máxima player↔alvo (m).
    pub range: f32,
}

/// Per-call context injected into the shared VM (Lua app data) by
/// [`luau_update`]. The `viber` closures read the snapshot and queue commands.
#[derive(Default, Debug)]
pub struct ScriptCtx {
    /// Entity currently running `on_update` (None outside script calls).
    pub entity: Option<Entity>,
    /// Position snapshot of the current entity (start of frame).
    pub origin: Vec3,
    /// Player position snapshot, when a [`crate::player::Player`] exists.
    pub player: Option<Vec3>,
    /// Seconds since engine startup.
    pub elapsed: f64,
    /// Delta time passed to `on_update`.
    pub dt: f32,
    /// Queued `set_position` commands, drained by [`luau_update`].
    pub pending: Vec<(Entity, Vec3)>,
    /// Queued [`ScriptCommand`]s (drained e aplicado pós-frame).
    pub commands: Vec<ScriptCommand>,
    /// Teclas pressionadas neste frame (para `viber.interacted`).
    pub just_pressed: Vec<KeyCode>,
    /// Ring of `viber.log` lines (capped) — also read by tests.
    pub logs: Vec<String>,
    /// Snapshot dos estados de quest ("not_taken|active|ready|done") para
    /// `viber.quest_state`, atualizado no início de cada frame.
    pub quest_states: std::collections::HashMap<String, String>,
    /// Snapshot do vault (recursos + itens) para `vault_get`/`item_count`.
    pub vault: std::collections::HashMap<String, u32>,
    /// Hostis vivos por banda do mundo (travel::REGIONS) —
    /// `viber.alive_in_region(idx)`.
    pub alive_regions: [u32; 5],
}

impl ScriptCtx {
    const LOG_CAP: usize = 256;

    fn push_log(&mut self, line: String) {
        if self.logs.len() >= Self::LOG_CAP {
            self.logs.remove(0);
        }
        self.logs.push(line);
    }
}

/// A compiled script chunk with its sandboxed environment.
#[derive(Clone)]
pub struct LoadedScript {
    /// Per-script global environment (`__index` falls back to real globals,
    /// so the shared `viber` API table stays visible).
    pub env: Table,
    /// Top-level chunk (executed once, defines `on_update` in `env`).
    pub chunk: Function,
    /// `on_update(dt)` extracted from the environment after execution.
    pub on_update: Option<Function>,
    /// Callback opcional de aggro-chain (`on_player_attack(px, pz)`).
    pub on_player_attack: Option<Function>,
    /// True once the chunk's top-level has run.
    pub ran: bool,
}

/// Registry of loaded chunks: `HashMap<path → loaded chunk handle>`.
#[derive(Default)]
pub struct LuaScriptRegistry {
    chunks: HashMap<String, LoadedScript>,
}

impl LuaScriptRegistry {
    pub fn get(&self, path: &str) -> Option<&LoadedScript> {
        self.chunks.get(path)
    }

    pub fn get_mut(&mut self, path: &str) -> Option<&mut LoadedScript> {
        self.chunks.get_mut(path)
    }

    pub fn contains(&self, path: &str) -> bool {
        self.chunks.contains_key(path)
    }

    pub fn insert(&mut self, path: String, script: LoadedScript) {
        self.chunks.insert(path, script);
    }

    /// Paths of all loaded scripts (sorted, for stable logging).
    pub fn paths(&self) -> Vec<&str> {
        let mut paths: Vec<&str> = self.chunks.keys().map(String::as_str).collect();
        paths.sort_unstable();
        paths
    }
}

/// Bevy resource holding the shared Luau VM, the chunk registry and the
/// warn-once bookkeeping. Build with [`LuaScriptHost::new`], load scripts via
/// [`LuaScriptHost::load_script`] (code string) or
/// [`LuaScriptHost::load_script_from_dir`] (disk), then add
/// [`LuauScriptPlugin`]-equivalent systems.
#[derive(Resource)]
pub struct LuaScriptHost {
    /// The shared sandboxed Luau VM (`Lua::new()` with the `luau` feature).
    pub lua: Lua,
    /// Loaded chunks keyed by script path.
    pub registry: LuaScriptRegistry,
    /// Directory scripts are loaded from: `<world_dir>/scripts`.
    pub scripts_dir: PathBuf,
    /// Script paths whose last error was already warned (warn 1x).
    warned: HashSet<String>,
}

impl LuaScriptHost {
    /// Creates the VM, installs the `viber` API table and seeds the app-data
    /// [`ScriptCtx`].
    pub fn new(scripts_dir: PathBuf) -> mlua::Result<Self> {
        let lua = Lua::new();
        let host = Self {
            lua,
            registry: LuaScriptRegistry::default(),
            scripts_dir,
            warned: HashSet::new(),
        };
        host.install_viber_api()?;
        host.lua.set_app_data(ScriptCtx::default());
        Ok(host)
    }

    /// Compiles `code` under `path` with a fresh sandboxed environment.
    /// The chunk is *not* executed yet — [`LuaScriptHost::activate`] runs the
    /// top level when the first entity references the script.
    pub fn load_script(&mut self, path: &str, code: &str) -> mlua::Result<()> {
        let env = self.create_script_env()?;
        let chunk = self
            .lua
            .load(code)
            .set_name(path)
            .set_environment(env.clone())
            .into_function()?;
        self.registry.insert(
            path.to_string(),
            LoadedScript {
                env,
                chunk,
                on_update: None,
                on_player_attack: None,
                ran: false,
            },
        );
        Ok(())
    }

    /// Loads a script from disk: `<scripts_dir>/<path>`.
    pub fn load_script_from_dir(&mut self, path: &str) -> mlua::Result<()> {
        let full = self.scripts_dir.join(path);
        let code = std::fs::read_to_string(&full).map_err(|e| {
            mlua::Error::runtime(format!("failed to read script {}: {e}", full.display()))
        })?;
        self.load_script(path, &code)
    }

    /// Ensures `path` is in the registry, loading it from
    /// `<scripts_dir>/<path>` when missing.
    pub fn ensure_loaded(&mut self, path: &str) -> mlua::Result<()> {
        if self.registry.contains(path) {
            return Ok(());
        }
        self.load_script_from_dir(path)
    }

    /// Runs the chunk top level once (defines `on_update` and any script
    /// state) and extracts the `on_update` handle. Idempotent per path —
    /// entities sharing a script share its globals.
    pub fn activate(&mut self, entity: Entity, path: &str) -> mlua::Result<()> {
        // Refresh the per-call ctx so top-level viber calls don't panic.
        let origin = if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
            ctx.entity = Some(entity);
            ctx.origin
        } else {
            Vec3::ZERO
        };
        // Home = posição de spawn (centro do wander), gravada uma vez.
        let key = entity.to_bits() as i64;
        {
            let states: Table = self.lua.named_registry_value("viber_states")?;
            let table = states.raw_get::<Table>(key).or_else(|_| {
                let fresh = self.lua.create_table()?;
                let _ = states.raw_set(key, fresh.clone());
                Ok::<Table, mlua::Error>(fresh)
            })?;
            if table.raw_get::<Table>("home").is_err() {
                let home = self.lua.create_table()?;
                home.raw_set("x", origin.x)?;
                home.raw_set("z", origin.z)?;
                table.raw_set("home", home)?;
                table.raw_set("picks", 0u64)?;
            }
        }
        let script = self
            .registry
            .get_mut(path)
            .ok_or_else(|| mlua::Error::runtime(format!("script '{path}' not loaded")))?;
        if !script.ran {
            script.chunk.call::<()>(())?;
            script.on_update = script.env.raw_get::<Option<Function>>("on_update")?;
            script.on_player_attack = script
                .env
                .raw_get::<Option<Function>>("on_player_attack")?;
            script.ran = true;
        }
        Ok(())
    }

    /// Calls `on_update(dt)` for `entity`'s script. The [`ScriptCtx`] snapshot
    /// (entity, origin, player, clock) must be passed in; commands queued by
    /// `viber.set_position` accumulate until [`LuaScriptHost::take_pending`].
    /// Scripts without `on_update` are a no-op.
    #[allow(clippy::too_many_arguments)]
    pub fn run_update(
        &mut self,
        entity: Entity,
        path: &str,
        dt: f32,
        origin: Vec3,
        player: Option<Vec3>,
        elapsed: f64,
    ) -> mlua::Result<()> {
        {
            let mut ctx = self
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.entity = Some(entity);
            ctx.origin = origin;
            ctx.player = player;
            ctx.dt = dt;
            ctx.elapsed = elapsed;
        } // release the borrow before re-entering Lua
        let on_update = self.registry.get(path).and_then(|s| s.on_update.clone());
        let Some(on_update) = on_update else {
            return Ok(());
        };
        on_update.call::<()>(dt)
    }

    /// Aggro-chain: chama `on_player_attack(px, pz)` no script da entidade
    /// (opcional — scripts sem o callback são ignorados).
    pub fn run_player_attack_alert(
        &mut self,
        entity: Entity,
        path: &str,
        attacker_pos: Vec3,
    ) -> mlua::Result<()> {
        {
            let mut ctx = self
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.entity = Some(entity);
        }
        let cb = self
            .registry
            .get(path)
            .and_then(|s| s.on_player_attack.clone());
        let Some(cb) = cb else {
            return Ok(());
        };
        cb.call::<()>((attacker_pos.x, attacker_pos.z))
    }

    /// Drains all queued `set_position` commands (called once per frame after
    /// every script ran).
    pub fn take_pending(&self) -> Vec<(Entity, Vec3)> {
        self.lua
            .app_data_mut::<ScriptCtx>()
            .map(|mut ctx| std::mem::take(&mut ctx.pending))
            .unwrap_or_default()
    }

    /// `viber.log` lines so far (ring buffer, oldest first).
    pub fn logs(&self) -> Vec<String> {
        self.lua
            .app_data_ref::<ScriptCtx>()
            .map(|ctx| ctx.logs.clone())
            .unwrap_or_default()
    }

    /// Reads a global value from a script's sandboxed environment (test/HUD
    /// introspection helper).
    pub fn script_global(&self, path: &str, key: &str) -> mlua::Result<mlua::Value> {
        let script = self
            .registry
            .get(path)
            .ok_or_else(|| mlua::Error::runtime(format!("script '{path}' not loaded")))?;
        script.env.raw_get(key)
    }

    /// Reports `err` for `path`; returns true the first time (so callers only
    /// emit a `warn!` once per script). Never panics — script errors are
    /// data, engine keeps running.
    pub fn warn_once(&mut self, path: &str, err: &dyn std::fmt::Display) -> bool {
        if self.warned.insert(path.to_string()) {
            warn!("luau script '{path}' error (further errors silenced): {err}");
            true
        } else {
            false
        }
    }

    /// Clears warn-once state for `path` (e.g. after a successful reload).
    pub fn clear_warnings(&mut self, path: &str) {
        self.warned.remove(path);
    }

    /// Removes per-entity leftovers when a [`LuaScriptRef`] despawns (drops
    /// queued commands for that entity; the chunk stays cached for reuse).
    pub fn deactivate(&mut self, entity: Entity) {
        // Estado por entidade: limpa a tabela Lua (respawn = estado fresco).
        if let Ok(states) = self.lua.named_registry_value::<Table>("viber_states") {
            let _ = states.raw_remove(entity.to_bits() as i64);
        }
        if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
            ctx.pending.retain(|(e, _)| *e != entity);
            ctx.commands
                .retain(|c| !matches!(c, ScriptCommand::MoveBy(e, _) | ScriptCommand::FaceTowards(e, _) | ScriptCommand::SetInteraction { entity: e, .. } | ScriptCommand::Despawn(e) if *e == entity));
            if ctx.entity == Some(entity) {
                ctx.entity = None;
            }
        }
    }

    /// Fresh per-script environment whose metatable falls back to the real
    /// globals (so the shared `viber` API and stdlib stay reachable without
    /// letting scripts overwrite each other's globals).
    fn create_script_env(&self) -> mlua::Result<Table> {
        let env = self.lua.create_table()?;
        let mt = self.lua.create_table()?;
        mt.set("__index", self.lua.globals())?;
        env.set_metatable(Some(mt));
        Ok(env)
    }

    /// Installs the `viber` API table on the VM globals:
    /// `log(msg)`, `time()`, `position()`, `set_position(x, y, z)`,
    /// `distance_to_player()`.
    fn install_viber_api(&self) -> mlua::Result<()> {
        let lua = &self.lua;
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

        // viber.home() -> x, z — posição de spawn (centro do wander).
        api.set(
            "home",
            lua.create_function(|lua, ()| {
                let origin = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|ctx| ctx.origin)
                    .unwrap_or_default();
                Ok((origin.x, origin.z))
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

        // viber.move_towards(x, z, speed) — passo deste frame na direção do
        // ponto; a engine senta o Y no terreno ao aplicar.
        api.set(
            "move_towards",
            lua.create_function(|lua, (x, z, speed): (f32, f32, f32)| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.move_towards fora de on_update"));
                };
                let dir = Vec2::new(x - ctx.origin.x, z - ctx.origin.z);
                if dir.length_squared() > 1e-6 {
                    let step = dir.normalize() * speed * ctx.dt;
                    ctx.commands.push(ScriptCommand::MoveBy(entity, step));
                }
                Ok(())
            })?,
        )?;

        // viber.move_by(dx, dz) — passo relativo direto.
        api.set(
            "move_by",
            lua.create_function(|lua, (dx, dz): (f32, f32)| {
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let Some(entity) = ctx.entity else {
                    return Err(mlua::Error::runtime("viber.move_by fora de on_update"));
                };
                let step = Vec2::new(dx * ctx.dt, dz * ctx.dt);
                ctx.commands.push(ScriptCommand::MoveBy(entity, step));
                Ok(())
            })?,
        )?;

        // viber.face_towards(x, z) / viber.face_player()
        let face_towards = |lua: &Lua| {
            lua.create_function(|lua, (x, z): (f32, f32)| {
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

        // viber.wander_target(radius) -> x, z — ponto determinístico ao redor
        // do home (mesma matemática da IA da engine, exposta ao script).
        api.set(
            "wander_target",
            lua.create_function(|lua, radius: f32| {
                let ctx = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|c| ScriptCtx {
                        entity: c.entity,
                        origin: c.origin,
                        player: c.player,
                        elapsed: c.elapsed,
                        dt: c.dt,
                        pending: Vec::new(),
                        commands: Vec::new(),
                        just_pressed: c.just_pressed.clone(),
                        logs: Vec::new(),
                        quest_states: c.quest_states.clone(),
                        vault: c.vault.clone(),
                        alive_regions: c.alive_regions,
                    })
                    .unwrap_or_default();
                let entity = ctx
                    .entity
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
                let mut ctx = lua
                    .app_data_mut::<ScriptCtx>()
                    .expect("ScriptCtx app data seeded in LuaScriptHost::new");
                let from = ctx.origin;
                ctx.commands
                    .push(ScriptCommand::DamagePlayer { amount, from: Some(from) });
                Ok(())
            })?,
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
                    .push(ScriptCommand::QuestReport { target: kind, amount: 1 });
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
                    .push(ScriptCommand::VaultAdd { kind, amount });
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
                    range: range.unwrap_or(3.5),
                });
                Ok(())
            })?,
        )?;
        // viber.interacted(key) -> bool — tecla pressionada NESTE frame E
        // player dentro do alcance de interação (3.5 m).
        api.set(
            "interacted",
            lua.create_function(|lua, key: String| {
                let ctx = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|c| ScriptCtx {
                        entity: c.entity,
                        origin: c.origin,
                        player: c.player,
                        elapsed: c.elapsed,
                        dt: c.dt,
                        pending: Vec::new(),
                        commands: Vec::new(),
                        just_pressed: c.just_pressed.clone(),
                        logs: Vec::new(),
                        quest_states: c.quest_states.clone(),
                        vault: c.vault.clone(),
                        alive_regions: c.alive_regions,
                    })
                    .unwrap_or_default();
                let code = key_code_from_str(&key)
                    .ok_or_else(|| mlua::Error::runtime(format!("tecla desconhecida '{key}'")))?;
                if !ctx.just_pressed.contains(&code) {
                    return Ok(false);
                }
                Ok(ctx
                    .player
                    .map(|p| p.distance(ctx.origin) <= 3.5)
                    .unwrap_or(false))
            })?,
        )?;

        lua.globals().set("viber", api)?;
        let states = lua.create_table()?;
        lua.set_named_registry_value("viber_states", states)?;
        Ok(())
    }
}

/// Tecla Lua (`"e"`, `"j"`, `"f"`, `"space"`) → [`KeyCode`].
pub fn key_code_from_str(key: &str) -> Option<KeyCode> {
    match key.to_ascii_lowercase().as_str() {
        "e" => Some(KeyCode::KeyE),
        "j" => Some(KeyCode::KeyJ),
        "f" => Some(KeyCode::KeyF),
        "q" => Some(KeyCode::KeyQ),
        "r" => Some(KeyCode::KeyR),
        "space" => Some(KeyCode::Space),
        _ => None,
    }
}

/// Bevy plugin wiring the Luau runtime: inserts [`LuaScriptHost`], then runs
/// `on_add` → `update` → `on_remove` hooks every frame. The orchestrator adds
/// it with the world's scripts dir (`world_dir.join("scripts")`).
pub struct LuauScriptPlugin {
    /// Directory scripts load from: `<world_dir>/scripts`.
    pub scripts_dir: PathBuf,
}

impl Default for LuauScriptPlugin {
    fn default() -> Self {
        Self {
            scripts_dir: PathBuf::from("scripts"),
        }
    }
}

impl bevy::app::Plugin for LuauScriptPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        let host = LuaScriptHost::new(self.scripts_dir.clone())
            .expect("failed to initialize Luau VM (LuaScriptHost)");
        // Garante o clock mesmo sem TimePlugin (plugin autossuficiente em apps mínimos).
        app.init_resource::<Time>();
        // Input para `viber.interacted` + evento de toasts de script.
        app.init_resource::<ButtonInput<KeyCode>>();
        app.add_message::<ScriptToast>();
        // O dano de scripts segue o path único do feedback (i-frames etc.).
        app.add_message::<crate::feedback::PlayerHurt>();
        // .chain() obriga on_add → update → on_remove dentro do mesmo frame
        // (um tuple simples não garante ordem no Bevy 0.19).
        app.add_message::<crate::feedback::AttackAlert>();
        app.insert_resource(host).add_systems(
            Update,
            (
                luau_on_add,
                luau_update,
                aggro_alert_system,
                luau_on_remove,
            )
                .chain(),
        );
    }
}

/// Hook `on_add`: when a [`LuaScriptRef`] appears, ensure its chunk is loaded
/// (from `<scripts_dir>/<path>`) and run its top level. Errors warn once and
/// never abort.
pub fn luau_on_add(
    mut host: ResMut<LuaScriptHost>,
    added: Query<(Entity, &LuaScriptRef), Added<LuaScriptRef>>,
) {
    for (entity, lref) in &added {
        let result = host
            .ensure_loaded(&lref.path)
            .and_then(|()| host.activate(entity, &lref.path));
        if let Err(e) = result {
            host.warn_once(&lref.path, &e);
        }
    }
}

/// Runs `on_update(dt)` of every live script, then applies queued commands
/// (posição com snap no terreno, teleporte, vitals do player, toasts,
/// interações). A script error is pcall'd: warned once, engine keeps running.
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn luau_update(
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    mut host: ResMut<LuaScriptHost>,
    mut scripts: Query<
        (
            Entity,
            &LuaScriptRef,
            Option<&mut Transform>,
            Option<&ScriptActivation>,
        ),
        Without<Player>,
    >,
    mut players: Query<
        (
            Entity,
            &GlobalTransform,
            Option<&mut Transform>,
            Option<&mut Health>,
            Option<&mut Xp>,
        ),
        With<crate::player::Player>,
    >,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: bevy::ecs::message::MessageWriter<ScriptToast>,
    mut hurts: bevy::ecs::message::MessageWriter<crate::feedback::PlayerHurt>,
    mut quests: Option<ResMut<crate::quests::QuestLog>>,
    mut vault: Option<ResMut<crate::economy::Vault>>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    let elapsed = time.elapsed_secs_f64();
    let (player_pos, mut player_components) = match players.single_mut() {
        Ok((p_entity, global, transform, health, xp)) => {
            (Some(global.translation()), Some((p_entity, transform, health, xp)))
        }
        Err(_) => (None, None),
    };
    let just_pressed: Vec<KeyCode> = keys.get_just_pressed().copied().collect();

    // Snapshot dos estados de quest para `viber.quest_state` (frame-start).
    if let Some(quests) = quests.as_deref_mut() {
        let snapshot: std::collections::HashMap<String, String> = quests
            .defs
            .iter()
            .map(|d| {
                (
                    d.id.clone(),
                    crate::quests::status_name(
                        quests.status(&d.id, vault.as_deref()),
                    )
                    .to_string(),
                )
            })
            .collect();
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.quest_states = snapshot;
        }
    }
    // Snapshot do vault para `vault_get`/`item_count`.
    if let Some(vault) = vault.as_deref() {
        let snapshot: std::collections::HashMap<String, u32> = [
            ("gold", vault.gold),
            ("wood", vault.wood),
            ("stone", vault.stone),
        ]
        .into_iter()
        .chain(vault.items.iter().map(|(k, v)| (k.as_str(), *v)))
        .map(|(k, v)| (k.to_string(), v))
        .collect();
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.vault = snapshot;
        }
    }

    for (entity, lref, transform, activation) in &mut scripts {
        let Some(origin) = transform.as_ref().map(|t| t.translation) else {
            continue;
        };
        // Congelamento (LOD de IA): além do raio de ativação o on_update nem
        // roda — inimigo distante custa zero lógica (e a animação para junto).
        let radius = activation
            .map(|a| a.radius)
            .unwrap_or(DEFAULT_ACTIVATION_RADIUS);
        if let Some(p) = player_pos {
            if origin.distance(p) > radius {
                continue;
            }
        }
        if let Err(e) = host.run_update(entity, &lref.path, dt, origin, player_pos, elapsed) {
            host.warn_once(&lref.path, &e);
        }
    }

    // Semeia teclas + aplica os comandos enfileirados por TODOS os scripts.
    let mut queued: Vec<ScriptCommand> = Vec::new();
    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
        ctx.just_pressed = just_pressed;
        queued = std::mem::take(&mut ctx.commands);
    }
    for command in queued {
        match command {
            ScriptCommand::MoveBy(entity, delta) => {
                if let Ok((_, _, Some(mut transform), _)) = scripts.get_mut(entity) {
                    let x = transform.translation.x + delta.x;
                    let z = transform.translation.z + delta.y;
                    let y = match terrain.as_ref() {
                        Some(rt) => rt.sample(x, z),
                        None => transform.translation.y,
                    };
                    transform.translation = Vec3::new(x, y, z);
                }
            }
            ScriptCommand::FaceTowards(entity, target) => {
                if let Ok((_, _, Some(mut transform), _)) = scripts.get_mut(entity) {
                    let dir = Vec3::new(
                        target.x - transform.translation.x,
                        0.0,
                        target.z - transform.translation.z,
                    );
                    if dir.length_squared() > 1e-6 {
                        transform.rotation = crate::player::facing_rotation(dir.normalize());
                    }
                }
            }
            ScriptCommand::TeleportPlayer(pos) => {
                if let Some((_, Some(transform), _, _)) = player_components.as_mut() {
                    transform.translation = pos;
                }
            }
            ScriptCommand::AddXp(gain) => {
                if let Some((_, _, _, Some(xp))) = player_components.as_mut() {
                    crate::vitals::gain_xp(xp, gain);
                }
            }
            ScriptCommand::DamagePlayer { amount, from } => {
                // Path único de dano: o feedback aplica (i-frames, vinheta,
                // número flutuante, morte, knockback) no próximo processamento.
                hurts.write(crate::feedback::PlayerHurt {
                    amount,
                    status: false,
                    from,
                });
                if std::env::var_os("VIBER_COMBAT_DEBUG").is_some() {
                    info!(target: "viber::combat", "damage {amount} pedido por script");
                }
            }
            ScriptCommand::HealPlayer(amount) => {
                if let Some((_, _, Some(health), _)) = player_components.as_mut() {
                    health.current = (health.current + amount).min(health.max);
                }
            }
            ScriptCommand::ApplyStatus { kind, secs } => {
                if kind.eq_ignore_ascii_case("venom") {
                    if let Some((p_entity, _, _, _)) = player_components.as_mut() {
                        commands.entity(*p_entity).insert(crate::feedback::StatusEffects {
                            venom: secs.max(0.0),
                            venom_tick: 0.0,
                        });
                    }
                } else {
                    warn!(target: "viber::luau", "apply_status: kind desconhecido '{kind}'");
                }
            }
            ScriptCommand::Toast(msg) => {
                info!(target: "viber::luau", "[toast] {msg}");
                toasts.write(ScriptToast(msg));
            }
            ScriptCommand::SetInteraction {
                entity,
                label,
                key,
                range,
            } => {
                if let Some(code) = key_code_from_str(&key) {
                    commands.entity(entity).insert(ScriptInteraction {
                        label,
                        key: code,
                        range,
                    });
                }
            }
            ScriptCommand::Despawn(entity) => {
                commands.entity(entity).try_despawn();
            }
            ScriptCommand::QuestAccept(id) => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                let title = quests.def(&id).map(|d| d.title.clone());
                if quests.accept(&id) {
                    toasts.write(ScriptToast(format!(
                        "Quest aceita: {}",
                        title.unwrap_or(id)
                    )));
                }
            }
            ScriptCommand::QuestTurnIn(id) => {
                let (Some(quests), Some(vault_ref)) =
                    (quests.as_deref_mut(), vault.as_deref_mut())
                else {
                    continue;
                };
                let title = quests.def(&id).map(|d| d.title.clone());
                let Some(rewards) = quests.turn_in(&id, Some(vault_ref)) else {
                    continue;
                };
                {
                    if rewards.xp > 0 {
                        if let Some((_, _, _, Some(xp))) = player_components.as_mut() {
                            crate::vitals::gain_xp(xp, rewards.xp);
                        }
                    }
                    if rewards.gold > 0 {
                        vault_ref.add_resource("gold", rewards.gold);
                    }
                    for item in &rewards.items {
                        if let Some((item_id, n)) = crate::quests::parse_item_reward(item) {
                            vault_ref.item_add(&item_id, n);
                        }
                    }
                    toasts.write(ScriptToast(format!(
                        "Quest entregue: {} (+{} XP{})",
                        title.unwrap_or_else(|| id.clone()),
                        rewards.xp,
                        if rewards.gold > 0 {
                            format!(", +{} ouro", rewards.gold)
                        } else {
                            String::new()
                        }
                    )));
                }
            }
            ScriptCommand::QuestReport { target, amount } => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                for ready in quests.report_progress(&target, amount) {
                    if let Some(def) = quests.def(&ready) {
                        toasts.write(ScriptToast(format!(
                            "Objetivo completo: {} — volta ao NPC",
                            def.title
                        )));
                    }
                }
            }
            ScriptCommand::QuestVisit(place) => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                for ready in quests.report_visit(&place) {
                    if let Some(def) = quests.def(&ready) {
                        toasts.write(ScriptToast(format!(
                            "Objetivo completo: {} — volta ao NPC",
                            def.title
                        )));
                    }
                }
            }
            ScriptCommand::VaultAdd { kind, amount } => {
                if let Some(vault) = vault.as_deref_mut() {
                    if !vault.add_resource(&kind, amount) {
                        warn!(target: "viber::economy", "vault_add: recurso desconhecido '{kind}'");
                    }
                }
            }
            ScriptCommand::ItemAdd { id, amount } => {
                if let Some(vault) = vault.as_deref_mut() {
                    vault.item_add(&id, amount);
                }
            }
            ScriptCommand::Topple { entity } => {
                // tomba na direção herói→entidade (break-style: fall)
                let target_pos = scripts
                    .get(entity)
                    .ok()
                    .and_then(|(_, _, transform, _)| transform.as_ref().map(|t| t.translation));
                if let (Some(target_pos), Some(player_pos)) = (target_pos, player_pos) {
                    let dir = (target_pos - player_pos).normalize_or_zero();
                    commands.entity(entity).insert(crate::physics_fx::Falling {
                        axis: Vec3::new(dir.z, 0.0, -dir.x),
                        timer: 0.0,
                    });
                    commands.entity(entity).remove::<LuaScriptRef>();
                }
            }
        }
    }

    // Compat: `viber.set_position` legado (posição absoluta, sem snap).
    for (entity, pos) in host.take_pending() {
        if let Ok((_, _, Some(mut transform), _)) = scripts.get_mut(entity) {
            transform.translation = pos;
        }
    }
}

/// Aggro-chain (loop 6): ao acertar uma criatura, aliados scriptados a até
/// [`ALERT_RADIUS_M`] recebem `on_player_attack(px, pz)` — os scripts de
/// matilhas usam-no para passar a perseguir.
#[allow(clippy::type_complexity)]
fn aggro_alert_system(
    mut alerts: bevy::ecs::message::MessageReader<crate::feedback::AttackAlert>,
    mut host: ResMut<LuaScriptHost>,
    mut scripts: Query<(Entity, &LuaScriptRef, &GlobalTransform), Without<crate::player::Player>>,
) {
    for alert in alerts.read() {
        let alert_pos = alert.position;
        for (entity, lref, transform) in &mut scripts {
            // só quem está perto DO ALVO ATINGIDO (não do player)
            if transform
                .translation()
                .distance_squared(alert_pos)
                .sqrt()
                <= crate::travel::ALERT_RADIUS_M
            {
                if let Err(error) =
                    host.run_player_attack_alert(entity, &lref.path, alert.position)
                {
                    host.warn_once(&lref.path, &error);
                }
            }
        }
    }
}

/// Hook `on_remove`: drop per-entity leftovers; the chunk stays cached in the
/// registry so a respawned entity reuses the script's existing globals.
pub fn luau_on_remove(
    mut host: ResMut<LuaScriptHost>,
    mut removed: RemovedComponents<LuaScriptRef>,
) {
    for entity in removed.read() {
        host.deactivate(entity);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::player::Player;
    use std::time::Duration;

    /// Advances the app's `Time` resource by `secs` (no TimePlugin in tests).
    fn advance_time(app: &mut bevy::app::App, secs: f32) {
        let mut time = app.world_mut().remove_resource::<Time>().unwrap();
        time.advance_by(Duration::from_secs_f32(secs));
        app.world_mut().insert_resource(time);
    }

    /// Minimal headless app: Time + host resource + the three runtime systems.
    fn test_app(host: LuaScriptHost) -> bevy::app::App {
        let mut app = bevy::app::App::new();
        app.init_resource::<Time>();
        app.init_resource::<ButtonInput<KeyCode>>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::feedback::PlayerHurt>();
        app.insert_resource(host);
        app.add_systems(Update, (luau_on_add, luau_update, luau_on_remove).chain());
        app
    }

    fn host_with(code: &str, path: &str) -> LuaScriptHost {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script(path, code).expect("load script");
        host
    }

    #[test]
    fn test_script_top_level_runs_and_logs() {
        let mut host = host_with("viber.log('hello from luau')", "log.lua");
        let mut world = World::new();
        let entity = world.spawn_empty().id();
        host.activate(entity, "log.lua").expect("activate");
        assert!(
            host.logs().iter().any(|l| l == "hello from luau"),
            "expected logged line, got {:?}",
            host.logs()
        );
    }

    #[test]
    fn test_syntax_error_is_reported_not_fatal() {
        let mut host = host_with("function on_update(dt) end", "ok.lua");
        let err = host
            .load_script("broken.lua", "this is not luau )))")
            .expect_err("syntax error should fail loading");
        assert!(!err.to_string().is_empty());
        // O host continua utilizável depois do erro de compilação.
        host.load_script("after.lua", "viber.log('still alive')")
            .expect("reload ok");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "after.lua")
            .expect("activate after");
        assert!(host.logs().iter().any(|l| l == "still alive"));
    }

    #[test]
    fn test_runtime_error_warns_once_and_engine_survives() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("bad.lua", "function on_update(dt) error('boom') end")
            .expect("load bad");
        host.load_script(
            "good.lua",
            "calls = 0\nfunction on_update(dt) calls = calls + 1 end",
        )
        .expect("load good");
        let mut world = World::new();
        let bad = world.spawn_empty().id();
        let good = world.spawn_empty().id();
        host.activate(bad, "bad.lua").expect("activate bad");
        host.activate(good, "good.lua").expect("activate good");

        for _ in 0..3 {
            let bad_err = host
                .run_update(bad, "bad.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect_err("bad script errors every frame");
            assert!(bad_err.to_string().contains("boom"));
            host.warn_once("bad.lua", &bad_err);
            host.run_update(good, "good.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect("good script unaffected");
        }

        // Warn 1x: só a primeira chamada devolve true.
        let mut host2 = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host2
            .load_script("x.lua", "function on_update(dt) error('e') end")
            .unwrap();
        host2.activate(bad, "x.lua").unwrap();
        assert!(host2.warn_once("x.lua", &"first"));
        assert!(!host2.warn_once("x.lua", &"second"));

        // Script bom correu as 3 vezes apesar do mau.
        match host.script_global("good.lua", "calls").expect("global") {
            mlua::Value::Integer(n) => assert_eq!(n, 3),
            other => panic!("expected integer calls, got {other:?}"),
        }
    }

    #[test]
    fn test_on_update_call_counter() {
        let mut host = host_with(
            "count = 0\nfunction on_update(dt) count = count + 1 end",
            "counter.lua",
        );
        let mut world = World::new();
        let entity = world.spawn_empty().id();
        host.activate(entity, "counter.lua").expect("activate");
        for i in 1..=4 {
            host.run_update(entity, "counter.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect("run_update");
            match host.script_global("counter.lua", "count").expect("count") {
                mlua::Value::Integer(n) => assert_eq!(n, i),
                other => panic!("expected integer count, got {other:?}"),
            }
        }
    }

    #[test]
    fn test_position_read_write_via_app() {
        let host = host_with(
            "function on_update(dt)\n  local px, py, pz = viber.position()\n  viber.set_position(px + 5, py, pz)\nend",
            "move.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Transform::from_xyz(1.0, 2.0, 3.0),
            LuaScriptRef {
                path: "move.lua".to_string(),
            },
        ));
        app.update();

        let mut q = app.world_mut().query::<&Transform>();
        let tf = q.single(app.world()).expect("transform");
        assert!(
            (tf.translation.x - 6.0).abs() < 1e-4,
            "x should be 6, got {}",
            tf.translation.x
        );
        assert!((tf.translation.y - 2.0).abs() < 1e-4);
        assert!((tf.translation.z - 3.0).abs() < 1e-4);
    }

    #[test]
    fn test_distance_to_player() {
        let host = host_with(
            "dist = nil\nfunction on_update(dt) dist = viber.distance_to_player() end",
            "dist.lua",
        );
        let mut app = test_app(host);
        // Player a 10 m na origem do script.
        app.world_mut()
            .spawn((Player::default(), GlobalTransform::from_xyz(10.0, 0.0, 0.0)));
        app.world_mut().spawn((
            Transform::from_xyz(0.0, 0.0, 0.0),
            LuaScriptRef {
                path: "dist.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        // mlua converte 10.0 (integral) em Value::Integer; aceitar ambos.
        let dist = match host.script_global("dist.lua", "dist").expect("dist") {
            mlua::Value::Number(d) => d,
            mlua::Value::Integer(n) => n as f64,
            other => panic!("expected number dist, got {other:?}"),
        };
        assert!((dist - 10.0).abs() < 1e-4, "dist {dist}");
    }

    #[test]
    fn test_time_api_reports_elapsed() {
        let host = host_with(
            "t0 = nil\nfunction on_update(dt) t0 = viber.time() end",
            "clock.lua",
        );
        let mut app = test_app(host);
        advance_time(&mut app, 2.5);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "clock.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("clock.lua", "t0").expect("t0") {
            mlua::Value::Number(t) => {
                assert!((t - 2.5).abs() < 0.1, "elapsed should be ~2.5, got {t}")
            }
            other => panic!("expected number t0, got {other:?}"),
        }
    }

    #[test]
    fn test_on_update_receives_dt() {
        let host = host_with("got = nil\nfunction on_update(dt) got = dt end", "dt.lua");
        let mut app = test_app(host);
        advance_time(&mut app, 0.25);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "dt.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("dt.lua", "got").expect("got") {
            mlua::Value::Number(d) => assert!((d - 0.25).abs() < 1e-3, "dt {d}"),
            other => panic!("expected number dt, got {other:?}"),
        }
    }

    #[test]
    fn test_script_environments_are_isolated() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("a.lua", "secret = 42\nfunction on_update(dt) end")
            .expect("load a");
        host.load_script(
            "b.lua",
            "assert(secret == nil, 'b must not see a.globals')\nfunction on_update(dt) end",
        )
        .expect("load b");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "a.lua")
            .expect("activate a");
        host.activate(world.spawn_empty().id(), "b.lua")
            .expect("activate b");
        match host.script_global("a.lua", "secret").expect("secret") {
            mlua::Value::Integer(n) => assert_eq!(n, 42),
            other => panic!("expected integer secret, got {other:?}"),
        }
    }

    #[test]
    fn test_on_remove_stops_script_calls() {
        let host = host_with(
            "count = 0\nfunction on_update(dt) count = count + 1 end",
            "count_remove.lua",
        );
        let mut app = test_app(host);
        let entity = app
            .world_mut()
            .spawn((
                Transform::default(),
                LuaScriptRef {
                    path: "count_remove.lua".to_string(),
                },
            ))
            .id();
        app.update();
        app.update();

        app.world_mut().despawn(entity);
        app.update(); // frame pós-despawn: on_remove corre, on_update não deve chamar
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host
            .script_global("count_remove.lua", "count")
            .expect("count")
        {
            mlua::Value::Integer(n) => assert_eq!(n, 2, "script must stop after despawn"),
            other => panic!("expected integer count, got {other:?}"),
        }
    }

    #[test]
    fn test_load_script_from_dir_reads_disk() {
        let tmp = tempfile::tempdir().expect("tmpdir");
        let scripts = tmp.path().join("scripts");
        std::fs::create_dir_all(&scripts).expect("mkdir");
        std::fs::write(
            scripts.join("disk.lua"),
            "loaded_from = 'disk'\nfunction on_update(dt) end",
        )
        .expect("write script");

        let mut host = LuaScriptHost::new(scripts).expect("host");
        host.ensure_loaded("disk.lua").expect("ensure_loaded");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "disk.lua")
            .expect("activate");
        match host
            .script_global("disk.lua", "loaded_from")
            .expect("global")
        {
            mlua::Value::String(s) => assert_eq!(s.to_str().expect("utf8"), "disk"),
            other => panic!("expected string, got {other:?}"),
        }
        // Idempotente: segunda chamada não recarrega nem falha.
        host.ensure_loaded("disk.lua").expect("ensure_loaded again");
    }

    #[test]
    fn test_plugin_registers_host_and_systems() {
        let mut app = bevy::app::App::new();
        app.add_plugins(LuauScriptPlugin::default());
        assert!(app.world().get_resource::<LuaScriptHost>().is_some());

        // Script pré-carregado via o resource do plugin (host de teste).
        let mut host = app.world_mut().resource_mut::<LuaScriptHost>();
        host.load_script("plugin.lua", "viber.log('via plugin')")
            .expect("load");

        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "plugin.lua".to_string(),
            },
        ));
        app.update();
        assert!(
            app.world()
                .resource::<LuaScriptHost>()
                .logs()
                .iter()
                .any(|l| l == "via plugin"),
            "plugin path should run scripts"
        );
    }

    #[test]
    fn test_missing_script_on_disk_warns_but_does_not_panic() {
        let mut app = bevy::app::App::new();
        app.init_resource::<Time>();
        app.init_resource::<ButtonInput<KeyCode>>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::feedback::PlayerHurt>();
        app.insert_resource(
            LuaScriptHost::new(PathBuf::from("/nonexistent/scripts")).expect("host"),
        );
        app.add_systems(Update, (luau_on_add, luau_update, luau_on_remove).chain());
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "ghost.lua".to_string(),
            },
        ));
        app.update(); // on_add falha → warn_once; luau_update ignora path ausente
        app.update();
        assert!(app.world().get_resource::<LuaScriptHost>().is_some());
    }

    #[test]
    fn test_registry_lists_paths_sorted() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("z.lua", "function on_update(dt) end")
            .unwrap();
        host.load_script("a.lua", "function on_update(dt) end")
            .unwrap();
        assert_eq!(host.registry.paths(), vec!["a.lua", "z.lua"]);
        assert!(host.registry.contains("z.lua"));
        assert!(!host.registry.contains("nope.lua"));
    }

    #[test]
    fn test_bundled_example_script_compiles_runs_and_moves() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("assets")
            .join("scripts")
            .join("example.lua");
        let code = std::fs::read_to_string(&path)
            .expect("assets/scripts/example.lua must ship with the crate");

        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("example.lua", &code)
            .expect("compile example");

        let mut app = test_app(host);
        let entity = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 0.0),
                LuaScriptRef {
                    path: "example.lua".to_string(),
                },
            ))
            .id();
        advance_time(&mut app, 0.5);
        app.update();

        // O script empurra py = sin(elapsed * 2): com elapsed 0.5 s, py != 0.
        let mut q = app.world_mut().query::<&Transform>();
        let tf = q.get(app.world(), entity).expect("transform");
        assert!(
            tf.translation.y.abs() > 1e-3,
            "example should oscillate y, got {}",
            tf.translation.y
        );
        // Top-level log + tick @1 Hz ainda não (elapsed < 1).
        let logs = app.world().resource::<LuaScriptHost>().logs();
        assert!(
            logs.iter().any(|l| l.contains("carregado")),
            "example top-level log missing: {logs:?}"
        );
    }
}
