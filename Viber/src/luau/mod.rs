//! Runtime de scripting Luau: cada chunk `world_dir/scripts/<path>` define
//! `function on_update(dt)` e conduz a sua entidade através da API `viber`.
//!
//! Organização por categoria:
//! - [`host`]: a VM partilhada, registry de chunks e ciclo de vida;
//! - [`ctx`]: o snapshot por-chamada lido pelas closures `viber.*`;
//! - [`commands`]: a fila de comandos Lua→engine, aplicada pós-frame;
//! - [`runtime`]: os sistemas Bevy (o único sítio onde o mundo é mutado);
//! - [`api`]: a tabela `viber.*` de jogo, composta de grupos;
//! - [`input`]/[`events`]/[`timers`]/[`entity`]/[`game`]: grupos da API;
//! - [`sfx`]: registry de sons e fuzzy match de gestos.
//!
//! Design notes:
//! - One shared sandboxed Luau VM ([`LuaScriptHost`]); every script chunk is
//!   compiled with its own environment table (`__index` → real globals) so
//!   scripts cannot clobber each other's globals.
//! - The owning entity is *injected* into each call: before invoking
//!   `on_update` the system stores a [`ScriptCtx`] snapshot (entity, position
//!   snapshot, player position, clock) as Lua app data; the `viber` closures
//!   read it back. Setters only queue commands — applied after all scripts
//!   ran. No `unsafe`, no raw `World` pointers.
//! - Script errors are pcall-style: reported once per script path
//!   ([`LuaScriptHost::warn_once`]) and never abort the engine.
//!
//! WIRED-BY-ORCHESTRATOR: [`LuaScriptRef`] is inserted by the spawn step
//! (recipes/spawn); the orchestrator also adds [`LuauScriptPlugin`] to the
//! `App` with the world's `scripts/` directory.

pub mod api;
pub mod commands;
pub mod components;
pub mod ctx;
pub mod events;
pub mod entity;
pub mod fx;
pub mod game;
pub mod host;
pub mod input;
pub mod ownership;
pub mod runtime;
pub mod sfx;
pub mod terrain;
pub mod timers;

#[cfg(test)]
mod tests;

pub use commands::ScriptCommand;
pub use components::{
    DEFAULT_ACTIVATION_RADIUS, LuaScriptRef, ScriptActivation, ScriptInteraction, ScriptToast,
};
pub use ctx::{ScriptCtx, SurfaceCache, SurfaceRegistries};
pub use events::{ScriptEventQueue, ScriptGameEvent, EVENT_QUEUE_CAP};
pub use host::{LoadedScript, LuaScriptHost, LuaScriptRegistry};
pub use input::{input_code_from_str, key_code_from_str, InputCode};
pub use ownership::ScriptSystemOwners;
pub use runtime::{
    aggro_alert_system, luau_on_add, luau_on_remove, luau_update, LuauRuntimeLocals,
};
pub use sfx::{match_gesture_clip, sfx_clip_from_str, SFX_NAME_REGISTRY};

use bevy::prelude::*;
use crate::profiler::{Group, timed};
use std::path::PathBuf;


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
        // Números de dano (`viber.damage_number`) — idempotente com o
        // Combat/Feedback; num preset sem eles o texto só não é desenhado.
        app.add_message::<crate::feedback::DamageNumberEvent>();
        app.init_resource::<ScriptEventQueue>();
        // Posse de sistemas nativos por scripts (`viber.own_system`).
        app.init_resource::<ScriptSystemOwners>();
        // Fila de spawns de prototypes (viber.spawn_prototype) — consumida
        // pelo sistema exclusivo registado no `run`; sem ele os pedidos
        // acumulam aqui (bounded pelos pedidos do jogo).
        app.init_resource::<crate::recipes::spawn::PendingScriptSpawns>();
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
