//! Superfície Luau do profiler — `viber.profiler()` (leitura) e
//! `viber.profiler_cmd(cmd)` (ações), no mesmo padrão do `viber.ui`:
//!
//! * **leitura** — o sistema [`publish_profiler_view`] publica um snapshot
//!   JSON num `Arc<Mutex<…>>` (leve com o modal fechado, completo a ~4 Hz
//!   com o modal `<UiModal id="profiler-win" key="p">` aberto) e a closure
//!   converte-o para tabela Lua on-demand (reutiliza `json_to_lua` da
//!   bridge);
//! * **escrita** — as closures enfileiram comandos numa fila `Arc<Mutex>`
//!   e [`apply_profiler_commands`] aplica-os pós-frame (mesmo contrato do
//!   `UiCommandQueue`: scripts correm num thread só, lock sem contenção).
//!
//! As teclas **F5** (aba), **F12**/**Pause** (congelar), **Backquote**
//! (exportar) e **PageUp/PageDown** (raio) continuam engine-side
//! ([`profiler_keys_system`]) — o modal declarativo trata do **P** sozinho
//! (`key="p"` faz toggle nativo) — e o driver `ui/profiler.lua` sincroniza
//! o estado para a UI por `viber.ui.select_tab`/classes.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use bevy::prelude::*;
use serde_json::Value;

use super::{ProfilerState, TABS};

/// Cadência de publicação do snapshot completo (s) — 4 Hz chega para uma
/// janela de debug e mantém o passe de entidades fora do caminho quente.
const PUBLISH_INTERVAL: f32 = 0.25;

/// Recurso partilhado entre as closures Lua e os sistemas da engine.
/// Mesma forma do `UiScriptState`: clonável, locks sem contenção.
#[derive(Clone, Default, Resource)]
pub struct ProfilerScriptState {
    /// Último snapshot publicado (`None` = modal fechado, nada a mostrar).
    pub view: Arc<Mutex<Option<Value>>>,
    /// Comandos enfileirados por `viber.profiler_cmd` (aplicados pós-frame).
    pub queue: Arc<Mutex<Vec<String>>>,
    /// True depois de `viber.profiler` existir no host.
    pub installed: bool,
}

/// Instala `viber.profiler`/`viber.profiler_cmd` no host Luau (uma vez).
pub fn install_profiler_script_api(
    mut state: ResMut<ProfilerScriptState>,
    host: Option<ResMut<crate::luau::LuaScriptHost>>,
) {
    if state.installed {
        return;
    }
    let Some(host) = host else { return };
    let snapshot = state.clone();
    match install_profiler_api(&host.lua, &snapshot) {
        Ok(()) => {
            state.installed = true;
            debug!("profiler: viber.profiler instalado no host de scripts");
        }
        Err(error) => {
            state.installed = true;
            warn!("profiler: falha a instalar viber.profiler ({error})");
        }
    }
}

fn install_profiler_api(lua: &mlua::Lua, state: &ProfilerScriptState) -> mlua::Result<()> {
    let viber: mlua::Table = lua.globals().get("viber")?;

    // viber.profiler() -> table | nil — snapshot publicado pela engine
    // (nil com o modal fechado: o driver nem acorda o GC).
    {
        let view = state.view.clone();
        viber.set(
            "profiler",
            lua.create_function(move |lua, ()| {
                let cached = view.lock().ok().and_then(|guard| guard.clone());
                match cached {
                    Some(value) => Ok(crate::bridge::lua::json_to_lua(lua, &value)?),
                    None => Ok(mlua::Value::Nil),
                }
            })?,
        )?;
    }

    // viber.profiler_cmd(cmd) — enfileira ação ("freeze", "export", "reset",
    // "tab:<nome>", "radius:<±N>", "extra:<id>"); aplicada pós-frame.
    {
        let queue = state.queue.clone();
        viber.set(
            "profiler_cmd",
            lua.create_function(move |_, cmd: String| {
                if let Ok(mut queue) = queue.lock() {
                    queue.push(cmd);
                }
                Ok(())
            })?,
        )?;
    }

    Ok(())
}

/// Publica o snapshot para as closures: leve com o modal fechado (nada),
/// completo a [`PUBLISH_INTERVAL`] com `<UiModal id="profiler-win">` aberto.
pub fn publish_profiler_view(world: &mut World) {
    // Throttle num Local-like: o estado vive no recurso (o sistema é
    // exclusivo, sem Locals convenientes).
    world.resource_scope(|world: &mut World, mut throttle: Mut<PublishThrottle>| {
        throttle.0 += world.resource::<Time>().delta_secs();
        let open = world
            .get_resource::<crate::ui::modal::UiModalsOpen>()
            .map(|m| m.open.iter().any(|id| id == "profiler-win"))
            .unwrap_or(false);
        if !open {
            // Modal fechado: larga o cache — `viber.profiler()` devolve nil.
            if let Ok(mut view) = world.resource::<ProfilerScriptState>().view.lock()
                && view.is_some()
            {
                *view = None;
            }
            return;
        }
        if throttle.0 < PUBLISH_INTERVAL {
            return;
        }
        throttle.0 = 0.0;

        let value = super::full_snapshot(world);
        if let Ok(mut view) = world.resource::<ProfilerScriptState>().view.lock() {
            *view = Some(value);
        }
    });
}

/// Throttle do publicador (o sistema é exclusivo; estado em recurso).
#[derive(Default, Resource)]
pub struct PublishThrottle(pub f32);

/// Aplica os comandos enfileirados por `viber.profiler_cmd` — pós-frame,
/// igual ao `apply_ui_commands`.
pub fn apply_profiler_commands(world: &mut World) {
    let pending: Vec<String> = world
        .resource::<ProfilerScriptState>()
        .queue
        .lock()
        .map(|mut queue| std::mem::take(&mut *queue))
        .unwrap_or_default();
    for cmd in pending {
        let status = run_profiler_cmd(world, &cmd);
        if let Some(mut state) = world.get_resource_mut::<ProfilerState>() {
            state.status = status;
        }
    }
}

/// Executa um comando do profiler e devolve a mensagem de estado. Partilhado
/// pelas teclas (F5/F12/…) e pelos comandos Lua.
pub fn run_profiler_cmd(world: &mut World, cmd: &str) -> String {
    let cmd = cmd.trim();
    if let Some(tab) = cmd.strip_prefix("tab:") {
        if let Some(index) = TABS.iter().position(|&t| t == tab) {
            if let Some(mut state) = world.get_resource_mut::<ProfilerState>() {
                state.tab = index;
            }
            return format!("aba: {tab}");
        }
        return format!("aba desconhecida: {tab}");
    }
    match cmd {
        "freeze" => {
            if let Some(mut state) = world.get_resource_mut::<ProfilerState>() {
                state.frozen = !state.frozen;
                return if state.frozen {
                    "congelado".into()
                } else {
                    "aquisição retomada".into()
                };
            }
            "sem estado".into()
        }
        "export" => match super::export_to_file(world, None) {
            Ok(path) => format!("exportado → {}", path.display()),
            Err(error) => format!("export falhou: {error}"),
        },
        "copy" => match super::copy_snapshot_to_clipboard(world) {
            Ok(bytes) => format!("JSON copiado ({bytes} bytes)"),
            Err(error) => format!("copiar falhou: {error}"),
        },
        "reset" => {
            super::timed::reset_timings();
            "timings limpos".into()
        }
        _ => {
            if let Some(delta) = cmd.strip_prefix("radius:") {
                let delta: f32 = delta.trim().parse().unwrap_or(0.0);
                if let Some(mut state) = world.get_resource_mut::<ProfilerState>() {
                    state.nearby_radius = (state.nearby_radius + delta).clamp(5.0, 200.0);
                    return format!("raio: {:.0} m", state.nearby_radius);
                }
                return "sem estado".into();
            }
            if let Some(id) = cmd.strip_prefix("extra:") {
                return match super::toggle_extra(world, id) {
                    Some(on) => format!("{id} → {}", if on { "ON" } else { "OFF" }),
                    None => format!("extra desconhecido: {id}"),
                };
            }
            format!("comando desconhecido: {cmd}")
        }
    }
}

/// Teclas engine-side do profiler: **F12**/**Pause** (congelar),
/// **Backquote** (exportar), **PageUp/PageDown** (raio). O **P** é do modal
/// declarativo (`key="p"`) e as ABAS têm teclado nativo do modal
/// (`]`/`.` próxima, `[`/`,` anterior, dígitos 1–5 saltam) — o driver Luau
/// espelha a aba escolhida para a engine (fonte única: a UI). Corre mesmo
/// com menus abertos — a janela do profiler É um menu.
pub fn profiler_keys_system(world: &mut World) {
    let (freeze, export, radius_up, radius_down) = {
        let keys = world.resource::<ButtonInput<KeyCode>>();
        (
            keys.just_pressed(KeyCode::F12) || keys.just_pressed(KeyCode::Pause),
            keys.just_pressed(KeyCode::Backquote),
            keys.just_pressed(KeyCode::PageUp),
            keys.just_pressed(KeyCode::PageDown),
        )
    };
    if freeze {
        let status = run_profiler_cmd(world, "freeze");
        world.resource_mut::<ProfilerState>().status = status;
    }
    if export {
        let status = run_profiler_cmd(world, "export");
        world.resource_mut::<ProfilerState>().status = status;
    }
    if radius_up {
        let status = run_profiler_cmd(world, "radius:+10");
        world.resource_mut::<ProfilerState>().status = status;
    }
    if radius_down {
        let status = run_profiler_cmd(world, "radius:-10");
        world.resource_mut::<ProfilerState>().status = status;
    }
}

/// Marca-tempo partilhado (o [`Instant`] de arranque para o publish log).
#[allow(dead_code)]
pub fn started_at() -> Instant {
    Instant::now()
}

#[cfg(test)]
mod tests {
    use super::super::{TAB_SYSTEMS, TAB_WORLD};
    use super::*;

    #[test]
    fn test_run_profiler_cmd_tab_aliases() {
        let mut world = World::new();
        world.init_resource::<ProfilerState>();
        assert_eq!(run_profiler_cmd(&mut world, "tab:world"), "aba: world");
        assert_eq!(world.resource::<ProfilerState>().tab, TAB_WORLD);
        assert_eq!(run_profiler_cmd(&mut world, "tab:systems"), "aba: systems");
        assert_eq!(world.resource::<ProfilerState>().tab, TAB_SYSTEMS);
        assert_eq!(
            run_profiler_cmd(&mut world, "tab:nuvem"),
            "aba desconhecida: nuvem"
        );
    }

    #[test]
    fn test_run_profiler_cmd_freeze_and_radius() {
        let mut world = World::new();
        world.init_resource::<ProfilerState>();
        assert_eq!(run_profiler_cmd(&mut world, "freeze"), "congelado");
        assert!(world.resource::<ProfilerState>().frozen);
        assert_eq!(run_profiler_cmd(&mut world, "freeze"), "aquisição retomada");
        assert!(!world.resource::<ProfilerState>().frozen);

        assert_eq!(run_profiler_cmd(&mut world, "radius:+15"), "raio: 45 m");
        assert_eq!(run_profiler_cmd(&mut world, "radius:-100"), "raio: 5 m");
        assert_eq!(run_profiler_cmd(&mut world, "radius:+500"), "raio: 200 m");
        assert_eq!(
            run_profiler_cmd(&mut world, "wat"),
            "comando desconhecido: wat"
        );
    }

    #[test]
    fn test_run_profiler_cmd_reset() {
        let mut world = World::new();
        world.init_resource::<ProfilerState>();
        super::super::timed::set_frozen(false);
        super::super::timed::record_system(super::super::Group::Fx, "test_cmd_sys", 1.0);
        assert_eq!(run_profiler_cmd(&mut world, "reset"), "timings limpos");
        assert!(super::super::timed::systems_snapshot(16.0).is_empty());
    }
}
