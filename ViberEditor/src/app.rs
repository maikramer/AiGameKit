//! Assembles the editor: Viber's own App in `RunMode::Edit` (the world renders in-process,
//! frozen) plus the editor plugins on top.

use bevy::feathers::{FeathersPlugins, dark_theme::create_dark_theme, theme::UiTheme};
use bevy::prelude::*;
use bevy::render::view::screenshot::{Screenshot, save_to_disk};
use std::path::{Path, PathBuf};
use viber::app::{AppOptions, RunMode};

use crate::Cli;
use crate::bridge::{self, DEFAULT_BRIDGE_PORT, ViberBridge, launch};
use crate::scene::ScenePlugin;
use crate::ui::EditorUiPlugin;
use crate::ui::strings::Lang;
use crate::ui::worlddoc::default_bake_dir;
use crate::viewport::ViewportPlugin;

/// Substitui o default_font do Bevy (FiraMono-subset, SEM acentos —
/// "Histórico"/"Transformação" liam-se caixas) pela FiraSans completa: um
/// replace no handle default cobre todos os `Text` sem fonte explícita.
fn install_default_font(fonts: &mut Assets<Font>) {
    let bytes = include_bytes!("assets/fonts/FiraSans-Regular.ttf");
    let font = Font::from_bytes(bytes.to_vec());
    let _ = fonts.insert(&Handle::default(), font);
}

fn install_default_font_system(mut fonts: ResMut<Assets<Font>>) {
    install_default_font(&mut fonts);
}

/// Static editor configuration resolved from the CLI.
#[derive(Resource)]
pub struct EditorConfig {
    /// `--viber-bin` override for ▶ Play.
    pub viber_bin: Option<PathBuf>,
    /// Bake directory for the World Doc panel, when one exists.
    pub bake_dir: Option<PathBuf>,
    /// Bottom dock tab to open at startup (QA/agents).
    pub startup_tab: Option<String>,
    /// Editor mode at startup (QA/agents).
    pub startup_mode: Option<String>,
    /// View options at startup (QA/agents).
    pub startup_views: Option<String>,
}

/// `--screenshot`: capture after N frames, then exit.
#[derive(Resource, Debug, Clone)]
struct ScreenshotRequest {
    path: PathBuf,
    frames_left: u32,
    taken: bool,
}

pub fn run(cli: Cli) -> anyhow::Result<()> {
    let world = cli.world.clone().unwrap_or_else(default_world);
    anyhow::ensure!(
        world.is_file(),
        "world file not found: {} — pass a path to a Viber world.xml",
        world.display()
    );
    let world = std::path::absolute(&world)?;
    let port = cli
        .port
        .or_else(|| launch::free_bridge_port(DEFAULT_BRIDGE_PORT))
        .unwrap_or(DEFAULT_BRIDGE_PORT);
    let bake_dir = cli.bake.or_else(|| default_bake_dir(&world));
    let startup_tab = cli.tab.clone();
    let startup_mode = cli.mode.clone();
    let startup_views = cli.views.clone();
    let lang = match cli.lang.as_deref().map(Lang::parse) {
        Some(parsed) => parsed.unwrap_or_else(|| {
            eprintln!(
                "vibereditor: unknown --lang {:?} (use pt or en); defaulting to pt",
                cli.lang.unwrap_or_default()
            );
            Lang::default()
        }),
        None => Lang::default(),
    };

    // Persiste o último mundo aberto (o `viber editor` sem caminho reabre este).
    if let Some(home) = std::env::var_os("HOME").map(std::path::PathBuf::from) {
        let file = home
            .join(".cache")
            .join("viber")
            .join("editor-last-world.txt");
        if let Some(dir) = file.parent()
            && std::fs::create_dir_all(dir).is_ok()
        {
            let _ = std::fs::write(&file, world.display().to_string());
        }
    }

    let title = format!(
        "Viber Editor — {}",
        world
            .file_name()
            .map(|name| name.to_string_lossy())
            .unwrap_or_default()
    );
    // Pré-validação (Fase O1): config.yaml inválido/ausente e mundo
    // procedural abrem um PAINEL DE ERRO em vez de sair para o stderr.
    if let Err(error) = viber::config::load(
        &world
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| world.clone()),
    ) {
        let hint = if error.to_string().contains("já é um mundo estático") {
            crate::startup_error::StartupHint::NeedsBake
        } else {
            crate::startup_error::StartupHint::CreateConfig
        };
        // App mínima de erro: mostra o painel e sai (sem mundo).
        let mut app = crate::startup_error::error_app(
            format!("{error:#}"),
            hint,
            world,
            cli.screenshot.clone(),
            cli.frames,
        );
        app.run();
        return Ok(());
    }

    let mut app = viber::app::build_app(
        &world,
        AppOptions {
            mode: RunMode::Edit,
            bridge_port: cli.bridge,
            window_title: Some(title),
            ..default()
        },
    )?;
    // Embedded from this file (crate-root level) so the asset id is the clean
    // `embedded://vibereditor/assets/fonts/fa-solid-900.ttf`.
    bevy::asset::embedded_asset!(app, "assets/fonts/fa-solid-900.ttf");
    // FiraSans COMPLETA: substitui o default_font do Bevy (FiraMono-SUBSET,
    // sem acentos — "Histórico"/"Transformação" liam-se caixas). Um único
    // replace cobre TODOS os Text sem fonte explícita (árvore, labels…).
    bevy::asset::embedded_asset!(app, "assets/fonts/FiraSans-Regular.ttf");
    bevy::asset::embedded_asset!(app, "assets/icons/logo.png");
    bevy::asset::embedded_asset!(app, "assets/icons/pick.png");
    bevy::asset::embedded_asset!(app, "assets/icons/world.png");
    app.add_plugins((
        FeathersPlugins,
        ViewportPlugin,
        ScenePlugin,
        bridge::BridgePlugin,
        EditorUiPlugin,
    ))
    .insert_resource(UiTheme(create_dark_theme()))
    .add_systems(PreStartup, install_default_font_system)
    .insert_resource(ViberBridge::new(port))
    .insert_resource(EditorConfig {
        viber_bin: cli.viber_bin,
        bake_dir,
        startup_tab,
        startup_mode,
        startup_views,
    })
    .insert_resource(lang);
    if let Some(selector) = cli.select {
        app.insert_resource(InitialFocus(selector)).add_systems(
            Update,
            focus_initial_selection.run_if(resource_exists::<InitialFocus>),
        );
    }
    if let Some(path) = cli.screenshot {
        app.insert_resource(ScreenshotRequest {
            path,
            frames_left: cli.frames,
            taken: false,
        })
        .add_systems(Update, screenshot_and_exit);
    }
    app.run();
    Ok(())
}

/// Ctrl+R — "reload saved scene": saves, then relaunches the editor with the same arguments
/// (keeping the selection) and exits. Edits the live world can't reflect incrementally
/// (terrain features, sky, UI, spawners) appear after it; rebuilding the engine state
/// in-process would mean re-running the whole terrain bootstrap.
pub fn reload_world(world: &mut World) {
    crate::scene::save_world(world);
    let selection = world
        .resource::<crate::scene::Selection>()
        .primary()
        .map(|node| {
            world
                .resource::<crate::scene::doc::SceneDoc>()
                .selector(node)
        });
    let args = relaunch_args(std::env::args().skip(1), selection);
    let exe = match std::env::current_exe() {
        Ok(exe) => exe,
        Err(error) => {
            error!("reload: no editor executable ({error})");
            return;
        }
    };
    match std::process::Command::new(exe).args(&args).spawn() {
        Ok(child) => {
            info!("reload: relaunched as pid {}", child.id());
            world.write_message(AppExit::Success);
        }
        Err(error) => error!("reload failed: {error}"),
    }
}

/// The relaunch arguments: the original ones minus one-shot flags (`--select`,
/// `--screenshot`, `--frames`), plus the current selection.
fn relaunch_args(original: impl Iterator<Item = String>, selection: Option<String>) -> Vec<String> {
    let mut out = Vec::new();
    let mut skip_value = false;
    for arg in original {
        if skip_value {
            skip_value = false;
            continue;
        }
        let flag = arg.split('=').next().unwrap_or_default();
        if matches!(flag, "--select" | "--screenshot" | "--frames") {
            skip_value = !arg.contains('=');
            continue;
        }
        out.push(arg);
    }
    if let Some(selector) = selection {
        out.push("--select".into());
        out.push(selector);
    }
    out
}

/// `--select`: the node to select and frame once the world is up.
#[derive(Resource, Debug, Clone)]
struct InitialFocus(String);

#[allow(clippy::too_many_arguments)] // Bevy system: one parameter per resource/query.
fn focus_initial_selection(
    mut commands: Commands,
    focus: Res<InitialFocus>,
    doc: Res<crate::scene::doc::SceneDoc>,
    mut selection: ResMut<crate::scene::Selection>,
    mut expansion: ResMut<crate::scene::TreeExpansion>,
    selected: Res<crate::viewport::gizmo::SelectedEntity>,
    globals: Query<&GlobalTransform>,
    camera: Option<ResMut<viber::editor_camera::EditorCamState>>,
) {
    let node = match doc.resolve(&focus.0) {
        Ok(node) => node,
        Err(error) => {
            warn!("--select: {error:#}");
            commands.remove_resource::<InitialFocus>();
            return;
        }
    };
    if selection.primary() != Some(&node) {
        crate::scene::reveal(&doc, &node, &mut expansion);
        selection.set_one(node);
        return;
    }
    // Frame it once its entity is resolved (and its transform propagated).
    if let (Some(entity), Some(mut camera)) = (selected.0, camera)
        && let Ok(global) = globals.get(entity)
    {
        let _ = viber::editor_camera::look_at(&mut camera, global.translation());
        camera.distance = 25.0;
        commands.remove_resource::<InitialFocus>();
    }
}

fn screenshot_and_exit(
    mut commands: Commands,
    mut request: ResMut<ScreenshotRequest>,
    mut exit: MessageWriter<AppExit>,
) {
    if request.frames_left > 0 {
        request.frames_left -= 1;
        return;
    }
    if !request.taken {
        request.taken = true;
        info!("screenshot → {}", request.path.display());
        commands
            .spawn(Screenshot::primary_window())
            .observe(save_to_disk(request.path.clone()));
        // A few frames for the capture to be read back and written.
        request.frames_left = 30;
        return;
    }
    exit.write(AppExit::Success);
}

/// The demo world shipped with the sibling Viber checkout.
fn default_world() -> PathBuf {
    let dir = launch::viber_checkout().join("examples").join("simple-rpg");
    dir.join("world.xml")
}

#[cfg(test)]
mod tests {
    use super::relaunch_args;

    fn args(list: &[&str]) -> impl Iterator<Item = String> {
        list.iter()
            .map(|s| s.to_string())
            .collect::<Vec<_>>()
            .into_iter()
    }

    #[test]
    fn relaunch_keeps_the_world_and_drops_one_shot_flags() {
        let out = relaunch_args(
            args(&[
                "w.xml",
                "--bridge",
                "15830",
                "--select",
                "name=a",
                "--screenshot=x.png",
                "--frames",
                "9",
            ]),
            Some("name=b".into()),
        );
        assert_eq!(out, ["w.xml", "--bridge", "15830", "--select", "name=b"]);
        assert_eq!(relaunch_args(args(&["w.xml"]), None), ["w.xml"]);
    }
}
