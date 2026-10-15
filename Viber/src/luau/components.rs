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

/// Valores por instância dos props exportados pelo script (atributos
/// `prop-<nome>` do nó, sem o prefixo, texto cru). O tipo vem das anotações
/// `--@export` do script ([`super::exports`]) e é aplicado em `viber.prop`.
#[derive(Debug, Clone, Default, Component, PartialEq)]
pub struct ScriptProps(pub std::sync::Arc<Vec<(String, String)>>);

/// Grupos da entidade (`groups="guardas aldeia"`) — `viber.group`,
/// `viber.in_group`, `viber.call_group` e os alvos `@grupo` das conexões.
#[derive(Debug, Clone, Default, Component, PartialEq)]
pub struct Groups(pub std::sync::Arc<Vec<String>>);

/// Conexões de sinal do EMISSOR (`connect-<sinal>="alvo:método; …"`): o que
/// um `viber.emit(sinal, …)` desta entidade chama.
#[derive(Debug, Clone, Default, Component, PartialEq)]
pub struct SignalConnections(pub std::sync::Arc<Vec<(String, Vec<super::exports::Connection>)>>);

impl SignalConnections {
    pub fn of(&self, signal: &str) -> &[super::exports::Connection] {
        self.0
            .iter()
            .find(|(name, _)| name == signal)
            .map(|(_, conns)| conns.as_slice())
            .unwrap_or(&[])
    }
}

/// Os componentes de grupos/conexões de um nó (`None` quando não declara).
/// Um `connect-*` mal escrito avisa e cai (o `viber scene lint` aponta-o).
pub fn link_components(
    links: &crate::recipes::NodeLinks,
    label: &str,
) -> (Option<Groups>, Option<SignalConnections>) {
    let groups =
        (!links.groups.is_empty()).then(|| Groups(std::sync::Arc::new(links.groups.clone())));
    let mut parsed = Vec::new();
    for (signal, raw) in &links.connections {
        match super::exports::parse_connections(raw) {
            Ok(conns) if !conns.is_empty() => parsed.push((signal.clone(), conns)),
            Ok(_) => {}
            Err(error) => warn!("{label}: connect-{signal}: {error}"),
        }
    }
    let connections = (!parsed.is_empty()).then(|| SignalConnections(std::sync::Arc::new(parsed)));
    (groups, connections)
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
