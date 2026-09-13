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
use crate::profiler::{Group, timed};
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
    DamagePlayer {
        amount: f32,
        from: Option<Vec3>,
    },
    HealPlayer(f32),
    /// Aplica um status effect ao herói (hoje: `"venom"`) — tratado pelo
    /// feedback (tick 1/s, path único de dano).
    ApplyStatus {
        kind: String,
        secs: f32,
    },
    /// Quests: aceitar / entregar / reportar progresso (hooks para scripts,
    /// aplicados pelo [`crate::quests::QuestLog`]).
    QuestAccept(String),
    QuestTurnIn(String),
    /// kill/collect: alvo + quantidade.
    QuestReport {
        target: String,
        amount: u32,
    },
    /// Visita a um marco nomeado.
    QuestVisit(String),
    /// Destrutível com queda (`break-style: fall`) — tomba na direção
    /// herói→entidade e despawna no fim.
    Topple {
        entity: Entity,
    },
    /// Deposita recurso no vault (`gold`/`wood`/`stone`) — economia loop 4.
    /// `from_collect` marca `viber.report_collect`, que aceita também itens de
    /// objetivo (fallback `item_add`); a chamada explícita `viber.vault_add`
    /// não — recurso desconhecido é erro diagnosticável, não item silencioso.
    VaultAdd {
        kind: String,
        amount: u32,
        from_collect: bool,
    },
    /// Adiciona item ao inventário (`potion`, `antidote`, `bomb`…).
    ItemAdd {
        id: String,
        amount: u32,
    },
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
    /// Gesto one-shot no rig da entidade (`viber.gesture`): fuzzy match do
    /// nome de clip, blend ~250 ms — o driver de locomoção recupera o rig
    /// no fim do clip (mesma mecânica dos gestos idle de NPC).
    Gesture {
        entity: Entity,
        name: String,
    },
    /// SFX curto (`viber.sound`) tocado na posição da entidade — consumido
    /// pelo `sfx_player_system` do [`crate::ambient`].
    PlaySfx {
        clip: crate::ambient::SfxClip,
        position: Option<Vec3>,
    },
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
    /// Range de interação da entidade actual (`viber.set_interaction`) —
    /// `viber.interacted` respeita-o em vez de hardcodar 3,5 m.
    pub interaction_range: Option<f32>,
    /// Vencedor por tecla do frame (`interact::InteractionFocus`): só ele
    /// vê `viber.interacted(tecla)` a `true`.
    pub interaction_focus: std::collections::HashMap<KeyCode, Entity>,
    /// Snapshot do HP do herói `(current, max)` para `viber.player_hp`.
    pub player_hp: Option<(f32, f32)>,
    /// Handle de leitura partilhado do terreno (`viber.ground_below`) —
    /// dois `Arc` clones; o terreno não muda pós-bootstrap, portanto o
    /// snapshot É o terreno (não nasce segunda fonte de altura).
    pub terrain: Option<crate::terrain::runtime::TerrainReader>,
}

impl ScriptCtx {
    const LOG_CAP: usize = 256;
    /// Cap por linha: um `viber.log` gigante não pode reter dezenas de MB
    /// no ring de 256 linhas (trunca em char boundary, igual ao bridge).
    const MAX_LOG_MESSAGE: usize = 8192;

    fn push_log(&mut self, mut line: String) {
        if line.len() > Self::MAX_LOG_MESSAGE {
            let mut end = Self::MAX_LOG_MESSAGE;
            while !line.is_char_boundary(end) {
                end -= 1;
            }
            line.truncate(end);
            line.push('…');
        }
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
            ctx.commands
                .retain(|c| !matches!(c, ScriptCommand::MoveBy(e, _) | ScriptCommand::FaceTowards(e, _) | ScriptCommand::SetInteraction { entity: e, .. } | ScriptCommand::Despawn(e) | ScriptCommand::Gesture { entity: e, .. } if *e == entity));
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
                    .and_then(|reader| reader.voxel.surface_below(&*reader.grid, x, z, y));
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
                let step = Vec2::new(dx * ctx.dt, dz * ctx.dt);
                ctx.commands.push(ScriptCommand::MoveBy(entity, step));
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
                ctx.commands.push(ScriptCommand::Gesture { entity, name });
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
                        "viber.set_interaction: tecla desconhecida '{key}' (válidas: e j f q r space)"
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

/// Registry nome-clip Lua (`viber.sound`) — case-insensitive, tabela única.
/// Os gatilhos nativos usam as variantes directamente; os scripts passam por
/// aqui. Aliases aceitáveis além do nome canónico (ex. `level_up`) ficam em
/// linhas próprias apontando à mesma variante.
pub const SFX_NAME_REGISTRY: &[(&str, crate::ambient::SfxClip)] = &[
    ("hit", crate::ambient::SfxClip::Hit),
    ("whoosh", crate::ambient::SfxClip::Whoosh),
    ("harvest", crate::ambient::SfxClip::Harvest),
    ("ui", crate::ambient::SfxClip::Ui),
    ("chop_hit", crate::ambient::SfxClip::ChopHit),
    ("chop_break", crate::ambient::SfxClip::ChopBreak),
    ("mine_hit", crate::ambient::SfxClip::MineHit),
    ("mine_break", crate::ambient::SfxClip::MineBreak),
    ("levelup", crate::ambient::SfxClip::LevelUp),
    ("level_up", crate::ambient::SfxClip::LevelUp),
    ("quest_complete", crate::ambient::SfxClip::QuestDone),
    ("quest_done", crate::ambient::SfxClip::QuestDone),
    ("travel", crate::ambient::SfxClip::Travel),
    ("loot", crate::ambient::SfxClip::Loot),
    ("chest_open", crate::ambient::SfxClip::Loot),
    ("footstep", crate::ambient::SfxClip::Footstep),
    ("footstep_water", crate::ambient::SfxClip::FootstepWater),
    ("hurt", crate::ambient::SfxClip::Hurt),
    ("heal", crate::ambient::SfxClip::Heal),
    ("game_over", crate::ambient::SfxClip::GameOver),
    ("quest_accept", crate::ambient::SfxClip::QuestAccept),
    ("notification", crate::ambient::SfxClip::Notification),
    ("coin", crate::ambient::SfxClip::Coin),
    ("buy", crate::ambient::SfxClip::Buy),
    ("error", crate::ambient::SfxClip::Error),
    ("save", crate::ambient::SfxClip::Save),
    ("load", crate::ambient::SfxClip::Load),
    ("shop_open", crate::ambient::SfxClip::ShopOpen),
    ("enemy_hurt", crate::ambient::SfxClip::EnemyHurt),
    ("enemy_death", crate::ambient::SfxClip::EnemyDeath),
    ("wolf_growl", crate::ambient::SfxClip::WolfGrowl),
    ("growl", crate::ambient::SfxClip::WolfGrowl),
    ("slime_squish", crate::ambient::SfxClip::SlimeSquish),
    ("boss_roar", crate::ambient::SfxClip::BossRoar),
    ("roar", crate::ambient::SfxClip::BossRoar),
    ("shield_block", crate::ambient::SfxClip::ShieldBlock),
    ("block", crate::ambient::SfxClip::ShieldBlock),
    ("door_open", crate::ambient::SfxClip::DoorOpen),
    ("door_close", crate::ambient::SfxClip::DoorClose),
    ("bomb_drop", crate::ambient::SfxClip::BombDrop),
    ("jump", crate::ambient::SfxClip::Jump),
    ("dash", crate::ambient::SfxClip::Dash),
];

/// Nome de clip SFX Lua (`"hit"`, `"UI"`) → [`crate::ambient::SfxClip`]
/// (case-insensitive via [`SFX_NAME_REGISTRY`]; desconhecido = `None`).
pub fn sfx_clip_from_str(name: &str) -> Option<crate::ambient::SfxClip> {
    let lower = name.to_ascii_lowercase();
    SFX_NAME_REGISTRY
        .iter()
        .find(|(alias, _)| *alias == lower)
        .map(|(_, clip)| *clip)
}

/// Índice do clip de gesto para um pedido `viber.gesture(name)` (pure fn).
///
/// Tenta cada alternativa (separadas por `,` ou `|`) por ordem; dentro de
/// cada uma, o match EXACTO normalizado ganha à substring — ambos os lados
/// passam por [`crate::animation::normalize_clip_name`] (caixa, `_`/`-` e
/// prefixos de ferramenta caem fora), por isso `"foldarms"` encontra
/// `Animator3D_FoldArms`. Sem correspondência = `None` (o chamador avisa 1×
/// e ignora, sem crash).
pub fn match_gesture_clip(
    animator: &crate::animation::CharacterAnimator,
    request: &str,
) -> Option<usize> {
    let names: Vec<String> = animator
        .clip_names
        .iter()
        .map(|n| crate::animation::normalize_clip_name(n))
        .collect();
    for want in request.split([',', '|']) {
        let want = crate::animation::normalize_clip_name(want);
        if want.is_empty() {
            continue;
        }
        if let Some(i) = names.iter().position(|n| *n == want) {
            return Some(i);
        }
        if let Some(i) = names.iter().position(|n| n.contains(&want)) {
            return Some(i);
        }
    }
    None
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
        // Árbitro das interações: `viber.interacted` LÊ-O, portanto vive com o
        // plugin (uma app mínima de teste com só este plugin tem de correr).
        app.init_resource::<crate::interact::InteractionFocus>();
        app.add_systems(bevy::app::PreUpdate, crate::interact::focus_interactions);
        app.add_message::<ScriptToast>();
        // SFX de scripts (`viber.sound`) — idempotente com o AmbientPlugin.
        app.add_message::<crate::ambient::SfxEvent>();
        // O dano de scripts segue o path único do feedback (i-frames etc.).
        app.add_message::<crate::feedback::PlayerHurt>();
        // .chain() obriga on_add → update → on_remove dentro do mesmo frame
        // (um tuple simples não garante ordem no Bevy 0.19).
        app.add_message::<crate::feedback::AttackAlert>();
        app.insert_resource(host);
        // Hot-reload de scripts (VIBER_HOT_RELOAD=0 desliga): watcher sobre
        // <world>/scripts/; a recarga corre antes do chain de scripts para o
        // frame seguinte já usar o chunk novo. Watcher a falhar = warn e a
        // engine segue SEM hot-reload (nunca é fatal).
        if crate::hot_reload::enabled_from_env() {
            match crate::hot_reload::HotReloadState::new(&self.scripts_dir) {
                Ok(state) => {
                    app.insert_resource(state);
                    app.add_systems(
                        Update,
                        crate::hot_reload::hot_reload_poll.before(luau_on_add),
                    );
                }
                Err(e) => {
                    warn!("hot-reload desativado (watcher falhou: {e})");
                }
            }
        }
        app.add_systems(
            Update,
            (
                luau_on_add,
                timed(Group::Scripts, luau_update),
                timed(Group::Scripts, aggro_alert_system),
                luau_on_remove,
            )
                .chain(),
        );
    }
}

/// Hook `on_add`: when a [`LuaScriptRef`] appears, ensure its chunk is loaded
/// (from `<scripts_dir>/<path>`) and run its top level. Errors warn once and
/// never abort. A posição de spawn (`Transform` autoral — o `GlobalTransform`
/// ainda não propagou no primeiro frame) alimenta o `home` do wander.
pub fn luau_on_add(
    mut host: ResMut<LuaScriptHost>,
    added: Query<(Entity, &LuaScriptRef, Option<&Transform>), Added<LuaScriptRef>>,
) {
    for (entity, lref, transform) in &added {
        let origin = transform.map(|t| t.translation).unwrap_or(Vec3::ZERO);
        let result = host
            .ensure_loaded(&lref.path)
            .and_then(|()| host.activate_at(entity, &lref.path, origin));
        if let Err(e) = result {
            host.warn_once(&lref.path, &e);
        }
    }
}

/// Runs `on_update(dt)` of every live script, then applies queued commands
/// (posição com snap no terreno, teleporte, vitals do player, toasts,
/// interações). A script error is pcall'd: warned once, engine keeps running.
/// Estado por-runtime que sobrevive entre frames, agrupado num `SystemParam`:
/// o `luau_update` chegou ao limite de 16 parâmetros do Bevy.
#[derive(bevy::ecs::system::SystemParam)]
pub struct LuauRuntimeLocals<'s> {
    /// Warn 1× por entidade sem rig/clip de gesto.
    pub gesture_warned: bevy::ecs::system::Local<'s, std::collections::HashSet<Entity>>,
    /// Warn 1× por kind de status desconhecido (`viber.apply_status`) — o
    /// script corre por frame; sem isto o warn unknown-kind virava spam.
    pub status_warned: bevy::ecs::system::Local<'s, std::collections::HashSet<String>>,
    /// Snapshots de quest/vault só com `Changed` (1.ª passagem força o seed).
    pub snapshots_seeded: bevy::ecs::system::Local<'s, bool>,
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn luau_update(
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    // `Option`: apps mínimas (testes, headless) montam sistemas soltos sem o
    // recurso — sem foco, o `interacted` cai no teste de alcance de sempre.
    focus: Option<Res<crate::interact::InteractionFocus>>,
    mut host: ResMut<LuaScriptHost>,
    mut scripts: Query<
        (
            Entity,
            &LuaScriptRef,
            Option<&mut Transform>,
            // O MUNDO da entidade. O `Transform` é LOCAL: um NPC dentro do
            // grupo `city` (assentado a y≈38,7 no terreno) tem local y=0, e
            // usar isso como posição punha-o 38 m abaixo do herói — o
            // `viber.interacted` (raio 3,5 m) nunca disparava e o [E] "não
            // fazia nada" em toda a cidade (repro do utilizador 2026-09-12).
            Option<&GlobalTransform>,
            Option<&ScriptActivation>,
            Option<&ScriptInteraction>,
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
    mut sfx: bevy::ecs::message::MessageWriter<crate::ambient::SfxEvent>,
    mut hurts: bevy::ecs::message::MessageWriter<crate::feedback::PlayerHurt>,
    mut quests: Option<ResMut<crate::quests::QuestLog>>,
    mut vault: Option<ResMut<crate::economy::Vault>>,
    // Gestos (`viber.gesture`): rig da entidade + os AnimationPlayers das
    // cenas glTF (play_action precisa dos dois).
    mut animators: Query<&mut crate::animation::CharacterAnimator>,
    mut animation_players: crate::animation::PlayerQuery,
    // Estado persistente agrupado num `SystemParam` (o Bevy limita sistemas a
    // 16 parâmetros — este trio chegou a esse teto).
    mut locals: LuauRuntimeLocals,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    let elapsed = time.elapsed_secs_f64();
    let (player_pos, mut player_components) = match players.single_mut() {
        Ok((p_entity, global, transform, health, xp)) => (
            Some(global.translation()),
            Some((p_entity, transform, health, xp)),
        ),
        Err(_) => (None, None),
    };
    let just_pressed: Vec<KeyCode> = keys.get_just_pressed().copied().collect();

    // Snapshots de quest/vault para `viber.quest_state`/`vault_get` — só
    // reconstruídos quando o recurso mudou desde o último run (ou no arranque):
    // 21 defs + o vault por frame era trabalho morto. `is_changed` num `ResMut`
    // apanha mutações de outros sistemas entre runs e as feitas por ESTE
    // sistema no run anterior — os scripts continuam a ler valores frescos no
    // frame seguinte à mudança. O vault suja também o snapshot de quests: o
    // status/progresso de misses de coleta depende do vault.
    let seeded = *locals.snapshots_seeded;
    *locals.snapshots_seeded = true;
    let vault_dirty = !seeded || vault.as_ref().is_some_and(|v| v.is_changed());
    let quests_dirty = vault_dirty || quests.as_ref().is_some_and(|q| q.is_changed());
    // Snapshot dos estados de quest para `viber.quest_state` (frame-start).
    if quests_dirty {
        if let Some(quests) = quests.as_deref_mut() {
            let snapshot: std::collections::HashMap<String, String> = quests
                .defs
                .iter()
                .map(|d| {
                    (
                        d.id.clone(),
                        crate::quests::status_name(quests.status(&d.id, vault.as_deref()))
                            .to_string(),
                    )
                })
                .collect();
            if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
                ctx.quest_states = snapshot;
            }
        }
    }
    // Snapshot do vault para `vault_get`/`item_count`.
    if vault_dirty {
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
    }

    // Semeia as teclas ANTES de correr os on_update: `viber.interacted`
    // tem de ver as teclas pressionadas NESTE frame (semear só no fim
    // fazia os scripts lerem o snapshot do frame anterior).
    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
        ctx.just_pressed = just_pressed.clone();
        // Vencedor por tecla deste frame (decidido no PreUpdate).
        ctx.interaction_focus = focus
            .as_deref()
            .map(crate::interact::InteractionFocus::snapshot)
            .unwrap_or_default();
        // Snapshot do HP do herói para `viber.player_hp` (frame-start).
        ctx.player_hp = player_components
            .as_ref()
            .and_then(|(_, _, health, _)| health.as_deref().map(|h| (h.current, h.max)));
        // Handle de leitura do terreno para `viber.ground_below` — dois
        // clones de Arc por frame; o terreno é imutável pós-bootstrap.
        ctx.terrain = terrain.as_deref().map(|rt| rt.reader());
    }

    for (entity, lref, transform, global, activation, interaction) in &mut scripts {
        // Posição no MUNDO (o `Transform` sozinho é local ao grupo pai).
        let Some(origin) = global
            .map(GlobalTransform::translation)
            .or_else(|| transform.as_ref().map(|t| t.translation))
        else {
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
        // Range de interação da entidade actual (para `viber.interacted`).
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.interaction_range = interaction.map(|i| i.range);
        }
        // Secção "scripts" do profiler: um escopo por ficheiro (igual ao
        // `script/<file>` do VibeGame). record_script auto-gateia em freeze.
        let script_t0 = std::time::Instant::now();
        if let Err(e) = host.run_update(entity, &lref.path, dt, origin, player_pos, elapsed) {
            host.warn_once(&lref.path, &e);
        }
        crate::profiler::timed::record_script(
            &lref.path,
            script_t0.elapsed().as_secs_f32() * 1000.0,
        );
    }

    // Recolhe os comandos enfileirados por TODOS os scripts (as teclas já
    // foram semeadas antes do loop).
    let mut queued: Vec<ScriptCommand> = Vec::new();
    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
        queued = std::mem::take(&mut ctx.commands);
    }
    for command in queued {
        match command {
            ScriptCommand::MoveBy(entity, delta) => {
                if let Ok((_, _, Some(mut transform), global, _, _)) = scripts.get_mut(entity) {
                    // O alvo calcula-se no MUNDO (o terreno é mundo) e
                    // escreve-se em LOCAL: `offset` é o que o pai acrescenta.
                    // Grupos de cidade/cena são translações puras — com um pai
                    // rodado/escalado isto seria uma aproximação (ver o
                    // comentário do `origin`).
                    let offset = global
                        .map(|g| g.translation() - transform.translation)
                        .unwrap_or(Vec3::ZERO);
                    let world = transform.translation + offset;
                    let x = world.x + delta.x;
                    let z = world.z + delta.y;
                    // Piso SOB a entidade (Y conhecido): um NPC movido por
                    // script sob um overhang não salta para o topo do mundo.
                    let y = match terrain.as_ref() {
                        Some(rt) => rt
                            .surface_below(x, z, world.y + crate::player::GROUND_PROBE)
                            .unwrap_or_else(|| rt.sample(x, z)),
                        None => world.y,
                    };
                    transform.translation = Vec3::new(x, y, z) - offset;
                }
            }
            ScriptCommand::FaceTowards(entity, target) => {
                if let Ok((_, _, Some(mut transform), global, _, _)) = scripts.get_mut(entity) {
                    // Direção no MUNDO (o alvo é o herói, em mundo).
                    let here = global
                        .map(GlobalTransform::translation)
                        .unwrap_or(transform.translation);
                    let dir = Vec3::new(target.x - here.x, 0.0, target.z - here.z);
                    if dir.length_squared() > 1e-6 {
                        transform.rotation = crate::player::facing_rotation(dir.normalize());
                    }
                }
            }
            ScriptCommand::TeleportPlayer(pos) => {
                if let Some((entity, Some(transform), _, _)) = player_components.as_mut() {
                    // ASSENTA no destino em vez de largar o herói no ar: as
                    // colunas do destino podem estar por assar (vinha de uma
                    // bolsa de interior a 2,6 km) e, sem collider, o herói
                    // caía pelo mundo fora antes de o streaming o apanhar.
                    // A superfície analítica existe sempre — é a mesma que o
                    // `player_movement` usa como chão de último recurso.
                    let landed = crate::player::landing_position(terrain.as_deref(), pos);
                    transform.translation = landed;
                    commands
                        .entity(*entity)
                        .insert(crate::player::TeleportSettle::default());
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
                        commands
                            .entity(*p_entity)
                            .insert(crate::feedback::StatusEffects {
                                venom: secs.max(0.0),
                                venom_tick: 0.0,
                            });
                    }
                } else if locals.status_warned.insert(kind.clone()) {
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
                        // ÚNICO sítio onde o alcance autorado vira alcance
                        // efetivo: daqui para a frente (prompt, foco,
                        // `interacted`, colheita) todos leem o mesmo número.
                        range: crate::interact::scaled_range(range),
                    });
                }
            }
            ScriptCommand::Despawn(entity) => {
                commands.entity(entity).try_despawn();
            }
            ScriptCommand::Gesture { entity, name } => {
                let Ok(mut animator) = animators.get_mut(entity) else {
                    if locals.gesture_warned.insert(entity) {
                        warn!(target: "viber::luau",
                            "viber.gesture('{name}'): entidade sem CharacterAnimator — \
                             cena glTF ainda a carregar ou rig sem clips");
                    }
                    continue;
                };
                let Some(index) = match_gesture_clip(&animator, &name) else {
                    if locals.gesture_warned.insert(entity) {
                        warn!(target: "viber::luau",
                            "viber.gesture('{name}'): nenhum clip do rig corresponde (clips: {:?})",
                            animator.clip_names);
                    }
                    continue;
                };
                let Some(node) = animator.nodes.get(index).copied() else {
                    continue;
                };
                // Blend de gesto ~250 ms como o npc_gesture_system; one-shot
                // devolve o rig ao driver no fim do clip.
                crate::animation::play_action(
                    &mut animator,
                    &mut animation_players,
                    node,
                    std::time::Duration::from_millis(250),
                    false,
                );
            }
            ScriptCommand::PlaySfx { clip, position } => {
                sfx.write(crate::ambient::SfxEvent { clip, position });
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
                let (Some(quests), Some(vault_ref)) = (quests.as_deref_mut(), vault.as_deref_mut())
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
            ScriptCommand::VaultAdd {
                kind,
                amount,
                from_collect,
            } => {
                if let Some(vault) = vault.as_deref_mut() {
                    // report_collect serve recursos (gold/wood/stone) E
                    // itens de objetivo ("dark-wood", "bog-moss"): sem o
                    // fallback item_add, quests collect de ITEM eram
                    // incompletáveis (o warn comia o drop). A chamada
                    // explícita viber.vault_add não tem essa desculpa —
                    // typo ("gld") tem de avisar, não criar item à pressa.
                    if !vault.add_resource(&kind, amount) {
                        if from_collect {
                            vault.item_add(&kind, amount);
                        } else {
                            warn!(target: "viber::luau",
                                "viber.vault_add: recurso desconhecido '{kind}' — nada depositado (itens usam viber.item_add)");
                        }
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
                let target_pos =
                    scripts
                        .get(entity)
                        .ok()
                        .and_then(|(_, _, transform, global, _, _)| {
                            global
                                .map(GlobalTransform::translation)
                                .or_else(|| transform.as_ref().map(|t| t.translation))
                        });
                if let (Some(target_pos), Some(player_pos)) = (target_pos, player_pos) {
                    let dir = (target_pos - player_pos).normalize_or_zero();
                    // initial preserva o yaw autoral — sem ele o prop "popeava"
                    // para identidade no 1.º frame da queda.
                    let initial = scripts
                        .get(entity)
                        .ok()
                        .and_then(|(_, _, transform, _, _, _)| {
                            transform.as_ref().map(|t| t.rotation)
                        })
                        .unwrap_or_default();
                    commands.entity(entity).insert(crate::physics_fx::Falling {
                        axis: Vec3::new(dir.z, 0.0, -dir.x),
                        timer: 0.0,
                        initial,
                    });
                    commands.entity(entity).remove::<LuaScriptRef>();
                }
            }
        }
    }

    // Compat: `viber.set_position` legado (posição absoluta = MUNDO; o
    // Transform é local, por isso desconta-se o que o pai acrescenta).
    for (entity, pos) in host.take_pending() {
        if let Ok((_, _, Some(mut transform), global, _, _)) = scripts.get_mut(entity) {
            let offset = global
                .map(|g| g.translation() - transform.translation)
                .unwrap_or(Vec3::ZERO);
            transform.translation = pos - offset;
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
            // Early-out por distância quadrada: sqrt por entidade×alerta
            // não compra nada (a comparação é a mesma).
            if transform.translation().distance_squared(alert_pos)
                <= crate::travel::ALERT_RADIUS_M * crate::travel::ALERT_RADIUS_M
            {
                if let Err(error) = host.run_player_attack_alert(
                    entity,
                    &lref.path,
                    transform.translation(),
                    alert.position,
                ) {
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
        app.add_message::<crate::ambient::SfxEvent>();
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
        app.add_message::<crate::ambient::SfxEvent>();
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

    /// CharacterAnimator mínimo com os clips dados (1 s cada, nós 1..n).
    fn test_animator(names: &[&str]) -> crate::animation::CharacterAnimator {
        crate::animation::CharacterAnimator {
            clip_names: names.iter().map(|s| s.to_string()).collect(),
            nodes: (0..names.len())
                .map(|i| bevy::animation::graph::AnimationNodeIndex::new(i + 1))
                .collect(),
            durations: vec![1.0; names.len()],
            player: Entity::PLACEHOLDER,
            state: None,
            current: None,
            action_time: 0.0,
            locked: false,
            air_time: 0.0,
            speed: 0.0,
            last_pos: None,
        }
    }

    #[test]
    fn test_match_gesture_clip_fuzzy_and_alternatives() {
        // Pack npc_* real: prefixos de ferramenta, `_` e caixa variam.
        let a = test_animator(&["Animator3D_Talk", "Animator3D_Yes", "npc_Fold_Arms", "idle"]);
        assert_eq!(match_gesture_clip(&a, "yes"), Some(1));
        assert_eq!(match_gesture_clip(&a, "YES"), Some(1), "case-insensitive");
        assert_eq!(match_gesture_clip(&a, "talk"), Some(0));
        // "foldarms" encontra "npc_Fold_Arms" (normalização de ambos os lados).
        assert_eq!(match_gesture_clip(&a, "foldarms"), Some(2));
        assert_eq!(match_gesture_clip(&a, "fold_arms"), Some(2));
        // Clip inexistente = None; a alternativa seguinte salva.
        assert_eq!(match_gesture_clip(&a, "salute"), None);
        assert_eq!(match_gesture_clip(&a, "salute,yes"), Some(1));
        assert_eq!(match_gesture_clip(&a, "salute|talk"), Some(0));
        // Pedido vazio (ou só separadores) nunca dá match.
        assert_eq!(match_gesture_clip(&a, ""), None);
        assert_eq!(match_gesture_clip(&a, ","), None);
    }

    #[test]
    fn test_sfx_clip_from_str_maps_known_names() {
        assert_eq!(sfx_clip_from_str("hit"), Some(crate::ambient::SfxClip::Hit));
        assert_eq!(
            sfx_clip_from_str("UI"),
            Some(crate::ambient::SfxClip::Ui),
            "case-insensitive"
        );
        assert_eq!(
            sfx_clip_from_str("harvest"),
            Some(crate::ambient::SfxClip::Harvest)
        );
        assert_eq!(
            sfx_clip_from_str("whoosh"),
            Some(crate::ambient::SfxClip::Whoosh)
        );
        assert_eq!(sfx_clip_from_str("boom"), None);
    }

    #[test]
    fn test_gesture_and_sound_commands_apply() {
        let host = host_with(
            "function on_update(dt)\n  viber.gesture('wave')\n  viber.sound('ui')\nend",
            "fx.lua",
        );
        let mut app = test_app(host);
        // AnimationPlayer da cena glTF (entidade descendente, como no runtime).
        let player = app
            .world_mut()
            .spawn((
                bevy::animation::AnimationPlayer::default(),
                bevy::animation::transition::AnimationTransitions::new(),
            ))
            .id();
        let mut animator = test_animator(&["idle", "Animator3D_Wave"]);
        animator.player = player;
        let npc = app
            .world_mut()
            .spawn((
                Transform::default(),
                animator,
                LuaScriptRef {
                    path: "fx.lua".to_string(),
                },
            ))
            .id();
        app.update();

        // Gesto aplicado: one-shot fica com o rig (action_time > 0).
        let animator = app
            .world()
            .get::<crate::animation::CharacterAnimator>(npc)
            .expect("animator");
        assert!(animator.action_time > 0.0, "o gesto devia estar a tocar");
        let wave = animator.node_matching(|n| n == "wave").expect("wave node");
        assert_eq!(animator.current, Some(wave));
        // Som aplicado: um SfxEvent Ui no buffer de mensagens.
        let mut events = app
            .world_mut()
            .resource_mut::<bevy::ecs::message::Messages<crate::ambient::SfxEvent>>();
        let clips: Vec<_> = events.drain().map(|e| e.clip).collect();
        assert_eq!(clips, vec![crate::ambient::SfxClip::Ui]);
    }

    #[test]
    fn test_gesture_without_animator_warns_once_and_survives() {
        let host = host_with(
            "count = 0\nfunction on_update(dt)\n  count = count + 1\n  viber.gesture('wave')\n  viber.sound('nope')\nend",
            "ghost-fx.lua",
        );
        let mut app = test_app(host);
        // Sem CharacterAnimator e com clip de som inválido: warn 1×, engine segue.
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "ghost-fx.lua".to_string(),
            },
        ));
        app.update();
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("ghost-fx.lua", "count").expect("count") {
            mlua::Value::Integer(n) => assert_eq!(n, 2, "script continua a correr"),
            other => panic!("expected integer count, got {other:?}"),
        }
    }

    #[test]
    fn test_player_hp_read() {
        let host = host_with(
            "got = nil\nfunction on_update(dt) got = { viber.player_hp() } end",
            "hp.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Player::default(),
            Transform::default(),
            crate::vitals::Health::default(),
            crate::vitals::Xp::default(),
        ));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "hp.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        let got = host.script_global("hp.lua", "got").expect("got");
        let mlua::Value::Table(t) = got else {
            panic!("expected table got, {got:?}")
        };
        let ok: bool = t.raw_get(1).expect("ok");
        let cur: f32 = t.raw_get(2).expect("cur");
        let max: f32 = t.raw_get(3).expect("max");
        assert!(ok, "player com vitals tem de reportar hp");
        assert!(
            (cur - max).abs() < 1e-3 && cur > 0.0,
            "default Health nasce cheio, tive cur={cur} max={max}"
        );
    }

    #[test]
    fn test_non_finite_and_unknown_key_args_are_rejected() {
        let host = host_with("function on_update(dt) end", "guards.lua");
        for snippet in [
            "return viber.damage_player(0/0)",
            "return viber.heal_player(math.huge)",
            "return viber.teleport_player(0/0, 0, 0)",
            "return viber.wander_target(0/0)",
            "return viber.set_interaction('Usar', 'k')",
            "return viber.set_interaction('Usar', 'e', 0/0)",
        ] {
            assert!(
                host.lua.load(snippet).exec().is_err(),
                "devia rejeitar: {snippet}"
            );
        }
        // Valores finitos continuam a passar (fora de on_update os setters
        // de combate/teleporte enfileiram à mesma — não precisam de entidade).
        assert!(
            host.lua
                .load("return viber.damage_player(5)")
                .exec()
                .is_ok()
        );
        assert!(host.lua.load("return viber.heal_player(5)").exec().is_ok());
        assert!(
            host.lua
                .load("return viber.teleport_player(1, 2, 3)")
                .exec()
                .is_ok()
        );
    }

    #[test]
    fn test_simple_rpg_npc_scripts_compile() {
        // Smoke dos scripts NPC do exemplo (fonte da verdade dos gestos/som):
        // compile-only — apanha erros de sintaxe sem correr o top-level.
        let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("examples")
            .join("simple-rpg")
            .join("scripts");
        let mut host = LuaScriptHost::new(base.clone()).expect("host");
        for path in [
            "townsfolk.lua",
            "merchant.lua",
            "healer.lua",
            "watch-guard.lua",
        ] {
            host.load_script_from_dir(path)
                .unwrap_or_else(|e| panic!("{path} deve compilar: {e}"));
        }
    }
}
