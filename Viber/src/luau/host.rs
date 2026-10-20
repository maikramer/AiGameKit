//! A VM Luau partilhada ([`LuaScriptHost`]): carregamento e cache de chunks
//! por path, activação do top-level, chamada dos hooks (`on_update`,
//! `on_player_attack`) e o livro de warn-once. Cada chunk corre num
//! environment sandbox próprio (`__index` → globals reais).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bevy::prelude::*;
use mlua::{Function, Lua, LuaOptions, StdLib, Table, VmState};

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
    /// `on_trigger_enter(other)` — sensor Rapier.
    pub on_trigger_enter: Option<Function>,
    /// `on_trigger_stay(other, dt)`.
    pub on_trigger_stay: Option<Function>,
    /// `on_trigger_exit(other)`.
    pub on_trigger_exit: Option<Function>,
    /// `on_collision_enter(other)` — collider sólido.
    pub on_collision_enter: Option<Function>,
    /// `on_collision_stay(other, dt)`.
    pub on_collision_stay: Option<Function>,
    /// `on_collision_exit(other)`.
    pub on_collision_exit: Option<Function>,
    /// True once the chunk's top-level has run.
    pub ran: bool,
}

/// Qual hook de contacto chamar. Stay leva `(other, dt)`; os outros só `other`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContactHook {
    /// Sensor começou a intersectar.
    TriggerEnter,
    /// Sensor continua a intersectar.
    TriggerStay,
    /// Sensor deixou de intersectar.
    TriggerExit,
    /// Collider sólido começou o contacto.
    CollisionEnter,
    /// Collider sólido continua em contacto.
    CollisionStay,
    /// Collider sólido deixou o contacto.
    CollisionExit,
}

impl ContactHook {
    /// Stay passa o `dt` além da tabela `other`.
    pub fn is_stay(self) -> bool {
        matches!(self, Self::TriggerStay | Self::CollisionStay)
    }

    pub(crate) fn enter(trigger: bool) -> Self {
        if trigger {
            Self::TriggerEnter
        } else {
            Self::CollisionEnter
        }
    }

    pub(crate) fn exit(trigger: bool) -> Self {
        if trigger {
            Self::TriggerExit
        } else {
            Self::CollisionExit
        }
    }

    pub(crate) fn stay(trigger: bool) -> Self {
        if trigger {
            Self::TriggerStay
        } else {
            Self::CollisionStay
        }
    }
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

/// Tempo máximo de UMA entrada na VM (hook, top-level, timer, `on_spawned`)
/// antes de a interromper com erro — um `while true do end` num script
/// congelava a engine inteira.
pub const SCRIPT_CALL_BUDGET: Duration = Duration::from_millis(500);

/// Teto de heap da VM partilhada: um script a crescer uma tabela sem fim
/// falha com erro Lua em vez de levar o processo ao OOM.
pub const SCRIPT_MEMORY_LIMIT: usize = 512 * 1024 * 1024;

/// Relógio do orçamento: prazo em ns desde `epoch` (0 = sem prazo). O
/// interrupt do Luau lê-o; o relógio só é consultado a cada 64 interrupts.
struct BudgetClock {
    epoch: Instant,
    deadline_ns: AtomicU64,
    ticks: AtomicU32,
}

/// Arma o orçamento enquanto vive; ao sair repõe o prazo anterior.
pub struct BudgetGuard {
    clock: Arc<BudgetClock>,
    previous: u64,
}

impl Drop for BudgetGuard {
    fn drop(&mut self) {
        self.clock
            .deadline_ns
            .store(self.previous, Ordering::Relaxed);
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
    budget: Arc<BudgetClock>,
}

impl LuaScriptHost {
    /// Creates the VM, installs the `viber` API table and seeds the app-data
    /// [`ScriptCtx`].
    pub fn new(scripts_dir: PathBuf) -> mlua::Result<Self> {
        // ALL_SAFE no Luau = TODAS as libs do Luau (mlua 0.12): o Luau não
        // tem `package`/`os`/`io` — o `require` de módulos passa pelo
        // `viber.load`, preso a `scripts/`.
        let lua = Lua::new_with(StdLib::ALL_SAFE, LuaOptions::default())?;
        lua.set_memory_limit(SCRIPT_MEMORY_LIMIT)?;
        // Luau JIT (feature `luau-jit`): chunks novos compilam para código
        // nativo. A rede de segurança é o `set_interrupt` lá em baixo —
        // `test_infinite_loop_is_interrupted_by_the_cpu_budget` prova que o
        // orçamento interrompe TAMBÉM em código JIT. `VIBER_LUAU_JIT=0`
        // volta ao interpretador puro (A/B de perf no mesmo binário).
        #[cfg(feature = "luau-jit")]
        {
            let jit = std::env::var("VIBER_LUAU_JIT")
                .map(|v| v != "0")
                .unwrap_or(true);
            lua.enable_jit(jit);
        }
        // mlua 0.12 trouxe o require-by-string do Luau: o global nasce na VM
        // e leria ficheiros do disco — módulos passam pelo `viber.load`.
        lua.globals().set("require", mlua::Value::Nil)?;
        // Biblioteca padrão só-leitura: os envs por script isolam globals,
        // mas `string.format = nil` num script mexia na tabela PARTILHADA.
        for lib in [
            "string",
            "math",
            "table",
            "coroutine",
            "bit32",
            "utf8",
            "os",
            "buffer",
        ] {
            if let Ok(t) = lua.globals().get::<Table>(lib) {
                t.set_readonly(true);
            }
        }
        let budget = Arc::new(BudgetClock {
            epoch: Instant::now(),
            deadline_ns: AtomicU64::new(0),
            ticks: AtomicU32::new(0),
        });
        let clock = budget.clone();
        lua.set_interrupt(move |_| {
            let deadline = clock.deadline_ns.load(Ordering::Relaxed);
            if deadline == 0
                || !clock
                    .ticks
                    .fetch_add(1, Ordering::Relaxed)
                    .is_multiple_of(64)
            {
                return Ok(VmState::Continue);
            }
            if clock.epoch.elapsed().as_nanos() as u64 >= deadline {
                return Err(mlua::Error::runtime(format!(
                    "orçamento de CPU excedido ({} ms numa chamada) — loop infinito?",
                    SCRIPT_CALL_BUDGET.as_millis()
                )));
            }
            Ok(VmState::Continue)
        });
        let host = Self {
            lua,
            registry: LuaScriptRegistry::default(),
            scripts_dir,
            warned: HashSet::new(),
            budget,
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

    /// Arma o [`SCRIPT_CALL_BUDGET`] até o guard sair de escopo. Entradas na
    /// VM sem guard (REPL do bridge, testes) correm sem prazo.
    pub fn budget_guard(&self) -> BudgetGuard {
        let deadline = self.budget.epoch.elapsed() + SCRIPT_CALL_BUDGET;
        let previous = self
            .budget
            .deadline_ns
            .swap((deadline.as_nanos() as u64).max(1), Ordering::Relaxed);
        BudgetGuard {
            clock: self.budget.clone(),
            previous,
        }
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
        let exports = super::exports::parse_exports(code);
        for error in &exports.errors {
            warn!("[luau] {path}: --@export {error}");
        }
        if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
            ctx.exports.insert(path.to_string(), Arc::new(exports));
        }
        self.registry.insert(
            path.to_string(),
            LoadedScript {
                env,
                chunk,
                on_update: None,
                on_player_attack: None,
                on_trigger_enter: None,
                on_trigger_stay: None,
                on_trigger_exit: None,
                on_collision_enter: None,
                on_collision_stay: None,
                on_collision_exit: None,
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
            let chunk = script.chunk.clone();
            let _budget = self.budget_guard();
            if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
                ctx.top_level = true;
            }
            let ran = chunk.call::<()>(());
            if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
                ctx.top_level = false;
            }
            ran?;
            let script = self
                .registry
                .get_mut(path)
                .ok_or_else(|| mlua::Error::runtime(format!("script '{path}' not loaded")))?;
            script.on_update = script.env.raw_get::<Option<Function>>("on_update")?;
            script.on_player_attack = script.env.raw_get::<Option<Function>>("on_player_attack")?;
            script.on_trigger_enter = script.env.raw_get::<Option<Function>>("on_trigger_enter")?;
            script.on_trigger_stay = script.env.raw_get::<Option<Function>>("on_trigger_stay")?;
            script.on_trigger_exit = script.env.raw_get::<Option<Function>>("on_trigger_exit")?;
            script.on_collision_enter = script
                .env
                .raw_get::<Option<Function>>("on_collision_enter")?;
            script.on_collision_stay = script
                .env
                .raw_get::<Option<Function>>("on_collision_stay")?;
            script.on_collision_exit = script
                .env
                .raw_get::<Option<Function>>("on_collision_exit")?;
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
        let _budget = self.budget_guard();
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
        let _budget = self.budget_guard();
        cb.call::<()>((attacker_pos.x, attacker_pos.z))
    }

    /// Hook de collider/trigger. `stay` passa `(other, dt)`; enter/exit só
    /// `other` (`{id, name?, x, y, z}`). `Ok(())` quando o script não define
    /// o hook — o chamador não avisa.
    #[allow(clippy::too_many_arguments)]
    pub fn run_contact(
        &mut self,
        entity: Entity,
        path: &str,
        origin: Vec3,
        hook: ContactHook,
        other_id: i64,
        other_name: Option<&str>,
        other_pos: Vec3,
        dt: f32,
    ) -> mlua::Result<()> {
        {
            let mut ctx = self
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.entity = Some(entity);
            ctx.path = Some(path.to_string());
            ctx.origin = origin;
            ctx.dt = dt;
        }
        let callback = self.registry.get(path).and_then(|script| match hook {
            ContactHook::TriggerEnter => script.on_trigger_enter.clone(),
            ContactHook::TriggerStay => script.on_trigger_stay.clone(),
            ContactHook::TriggerExit => script.on_trigger_exit.clone(),
            ContactHook::CollisionEnter => script.on_collision_enter.clone(),
            ContactHook::CollisionStay => script.on_collision_stay.clone(),
            ContactHook::CollisionExit => script.on_collision_exit.clone(),
        });
        let Some(callback) = callback else {
            return Ok(());
        };
        let other = self.lua.create_table()?;
        other.raw_set("id", other_id)?;
        other.raw_set("x", other_pos.x)?;
        other.raw_set("y", other_pos.y)?;
        other.raw_set("z", other_pos.z)?;
        if let Some(name) = other_name {
            other.raw_set("name", name)?;
        }
        let _budget = self.budget_guard();
        if hook.is_stay() {
            callback.call::<()>((other, dt))
        } else {
            callback.call::<()>(other)
        }
    }

    /// Entrega um sinal/`call_group`: chama a função global `method` do script
    /// de `entity` com `args` e `viber.sender()` = `sender`. `Ok(false)` quando
    /// o script não define o método (o chamador avisa).
    pub fn run_method(
        &mut self,
        entity: Entity,
        path: &str,
        origin: Vec3,
        method: &str,
        args: mlua::MultiValue,
        sender: Option<i64>,
    ) -> mlua::Result<bool> {
        let Some(script) = self.registry.get(path) else {
            return Ok(false);
        };
        let Some(func) = script.env.raw_get::<Option<Function>>(method)? else {
            return Ok(false);
        };
        {
            let mut ctx = self
                .lua
                .app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            ctx.entity = Some(entity);
            ctx.path = Some(path.to_string());
            ctx.origin = origin;
            ctx.sender = sender;
        }
        let result = {
            let _budget = self.budget_guard();
            func.call::<()>(args)
        };
        if let Some(mut ctx) = self.lua.app_data_mut::<ScriptCtx>() {
            ctx.sender = None;
        }
        result.map(|()| true)
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
        // Timers do dono morrem com ele (um respawn re-regista os seus).
        let bits = entity.to_bits() as i64;
        let orphans = super::timers::timer_ids_where(&self.lua, |owner, _| owner == bits);
        super::timers::drop_timers(&self.lua, &orphans);
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
                    | ScriptCommand::Gesture { entity: e, .. }
                    | ScriptCommand::SetPose { entity: e, .. } => *e,
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
        // mlua 0.12: devolve Result (pode falhar em tabelas readonly).
        env.set_metatable(Some(mt))?;
        Ok(env)
    }

    /// Instala a API `viber` na VM — composta em `api.rs` (grupos
    /// categorizados: núcleo, input, eventos, timers, entidade, jogo).
    fn install_viber_api(&self) -> mlua::Result<()> {
        super::api::install(&self.lua)
    }
}
