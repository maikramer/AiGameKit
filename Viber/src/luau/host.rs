//! A VM Luau partilhada ([`LuaScriptHost`]): carregamento e cache de chunks
//! por path, activação do top-level, chamada dos hooks (`on_update`,
//! `on_player_attack`) e o livro de warn-once. Cada chunk corre num
//! environment sandbox próprio (`__index` → globals reais).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use bevy::prelude::*;
use mlua::{Function, Lua, Table};

use super::commands::ScriptCommand;
use super::ctx::ScriptCtx;

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
        {
            let mut ctx = host
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.scripts_dir = Some(host.scripts_dir.clone());
        }
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
        self.activate_at(entity, path, Vec3::ZERO)
    }

    /// [`LuaScriptHost::activate`] com a posição de spawn real — o `home`
    /// (centro do wander) é gravado a partir dela. `ctx.origin` chega aqui
    /// stale (só `run_update` o actualiza, e corre DEPOIS de `on_add` no
    /// primeiro frame), por isso a posição tem de vir de fora.
    pub fn activate_at(&mut self, entity: Entity, path: &str, origin: Vec3) -> mlua::Result<()> {
        // Refresh the per-call ctx so top-level viber calls don't panic.
        let origin = if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
            ctx.entity = Some(entity);
            ctx.path = Some(path.to_string());
            ctx.origin = origin;
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
            script.on_player_attack = script.env.raw_get::<Option<Function>>("on_player_attack")?;
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
            ctx.path = Some(path.to_string());
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
        origin: Vec3,
        attacker_pos: Vec3,
    ) -> mlua::Result<()> {
        {
            let mut ctx = self
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.entity = Some(entity);
            ctx.path = Some(path.to_string());
            // Sem isto, on_player_attack corria com origin/dt/elapsed da
            // ÚLTIMA entidade do frame — position()/move_towards/damage_player
            // calculavam a partir de outra criatura.
            ctx.origin = origin;
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
            ctx.commands.retain(|c| {
                // Só os comandos bound à entidade morrem com ela (quests/
                // toasts/vault são world-scoped); os restantes ficam.
                let owner = match c {
                    ScriptCommand::MoveBy(e, _)
                    | ScriptCommand::FaceTowards(e, _)
                    | ScriptCommand::SetInteraction { entity: e, .. }
                    | ScriptCommand::Despawn(e)
                    | ScriptCommand::Gesture { entity: e, .. } => *e,
                    _ => return true,
                };
                owner != entity
            });
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


    /// Instala a API `viber` na VM — composta em `api.rs` (grupos
    /// categorizados: núcleo, input, eventos, timers, entidade, jogo).
    fn install_viber_api(&self) -> mlua::Result<()> {
        super::api::install(&self.lua)
    }
}
