//! Componentes e mensagens do runtime Luau: a ligação entidade↔script.
//!
//! - [`LuaScriptRef`]: o componente que liga uma entidade a um chunk
//!   (`scripts/<path>`), inserido pelo spawn.
//! - [`ScriptActivation`]: o "LOD de IA" — raio além do qual o script nem roda.
//! - [`ScriptInteraction`]: alvo de interação registado por script
//!   (`viber.set_interaction`), arbitrado pelo `interact::InteractionFocus`.
//! - [`ScriptToast`]: mensagem de toast pedida por `viber.toast`.

use bevy::prelude::*;

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
