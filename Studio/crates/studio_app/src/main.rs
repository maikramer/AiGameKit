//! AiGameKit Studio — front-end gráfico do pipeline de assets.

// UI com `if let` aninhado: um let-chain longo esconde a intenção.
#![allow(clippy::collapsible_if)]

mod app;
mod preview;
mod state;
mod theme;
mod ui;

use std::{path::PathBuf, process::ExitCode};

use bevy::{
    prelude::*,
    render::view::screenshot::{Screenshot, save_to_disk},
    window::{WindowPlugin, WindowResolution},
};
use bevy_egui::EguiPlugin;
use studio_core::project::default_workspace;

use crate::{
    app::StudioPlugin,
    preview::{LoadRequest, PreviewCommand, PreviewPlugin, PreviewState},
    ui::UiPlugin,
};

#[derive(Debug, Default)]
struct Args {
    project: Option<PathBuf>,
    spike_glb: Option<PathBuf>,
    spike_out: Option<PathBuf>,
    spike_wireframe: bool,
    ui_screenshot: Option<PathBuf>,
    auto_run: bool,
}

fn parse_args() -> Args {
    let mut args = Args::default();
    let mut iter = std::env::args().skip(1);
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--project" | "--fixture" => {
                args.project = iter.next().map(PathBuf::from);
            }
            "--spike-glb" => {
                args.spike_glb = iter.next().map(PathBuf::from);
            }
            "--spike-out" => {
                args.spike_out = iter.next().map(PathBuf::from);
            }
            "--spike-wireframe" => {
                args.spike_wireframe = true;
            }
            "--auto-run" => {
                args.auto_run = true;
            }
            "--ui-screenshot" => {
                args.ui_screenshot = iter.next().map(PathBuf::from);
            }
            "--help" | "-h" => {
                println!(
                    "AiGameKit Studio {}\n\
                     uso: studio_app [--project <dir>] [--spike-glb <glb>] [--spike-out <png>] [--ui-screenshot <png>]\n\
                     \n\
                     workspace: {} (AIGAMEKIT_STUDIO_WORKSPACE)\n\
                     cache:     {} (STUDIO_CACHE_DIR)",
                    studio_core::VERSION,
                    default_workspace().display(),
                    cache_root().display(),
                );
                std::process::exit(0);
            }
            other => eprintln!("argumento ignorado: {other}"),
        }
    }
    args
}

fn cache_root() -> PathBuf {
    if let Ok(value) = std::env::var("STUDIO_CACHE_DIR") {
        let value = value.trim();
        if !value.is_empty() {
            return PathBuf::from(value);
        }
    }
    directories::ProjectDirs::from("dev", "AiGameKit", "Studio")
        .map(|dirs| dirs.cache_dir().to_path_buf())
        .unwrap_or_else(|| std::env::temp_dir().join("aigamekit-studio"))
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "studio_app=info,wgpu=warn,naga=warn".into()),
        )
        .init();

    let args = parse_args();
    let cache = cache_root();
    if let Err(err) = std::fs::create_dir_all(cache.join("glb")) {
        eprintln!("cache {}: {err}", cache.display());
        return ExitCode::FAILURE;
    }

    let mut bevy_app = App::new();
    // O reader tem de ser registado ANTES do AssetPlugin.
    bevy_ext_gltf::register_asset_source(&mut bevy_app, vec![cache.clone()]);

    bevy_app
        .add_plugins(
            DefaultPlugins
                .set(WindowPlugin {
                    primary_window: Some(Window {
                        title: "AiGameKit Studio".into(),
                        resolution: WindowResolution::new(1600, 1000),
                        ..default()
                    }),
                    ..default()
                })
                .set(AssetPlugin {
                    file_path: cache.to_string_lossy().into_owned(),
                    ..default()
                }),
        )
        .insert_resource(GlobalAmbientLight {
            color: Color::srgb(0.72, 0.78, 0.95),
            brightness: 900.0,
            ..default()
        })
        .add_plugins(PreviewPlugin {
            cache_root: cache.clone(),
        })
        .add_plugins(StudioPlugin {
            initial_project: args.project.clone(),
            auto_run: args.auto_run,
        });

    if let Some(glb) = args.spike_glb.clone() {
        let out = args
            .spike_out
            .clone()
            .unwrap_or_else(|| std::env::temp_dir().join("studio-spike.png"));
        bevy_app
            .insert_resource(Spike {
                glb,
                out,
                frames: 0,
                requested: false,
                wireframe: args.spike_wireframe,
            })
            .add_systems(Startup, spike_start)
            .add_systems(Update, spike_tick);
    } else {
        bevy_app.add_plugins((EguiPlugin::default(), UiPlugin));
        if let Some(out) = args.ui_screenshot.clone() {
            bevy_app
                .insert_resource(UiShot {
                    out,
                    frames: 0,
                    requested: false,
                })
                .add_systems(Update, ui_shot_tick);
        }
    }

    let exit = bevy_app.run();
    match exit {
        AppExit::Success => ExitCode::SUCCESS,
        AppExit::Error(_) => ExitCode::FAILURE,
    }
}

#[derive(Resource)]
struct UiShot {
    out: PathBuf,
    frames: u32,
    requested: bool,
}

fn ui_shot_tick(
    mut shot: ResMut<UiShot>,
    mut commands: Commands,
    mut exit: MessageWriter<AppExit>,
) {
    shot.frames += 1;
    if !shot.requested {
        if shot.frames > 360 {
            commands
                .spawn(Screenshot::primary_window())
                .observe(save_to_disk(shot.out.display().to_string()));
            shot.requested = true;
            shot.frames = 0;
        }
    } else if shot.frames > 120 {
        exit.write(AppExit::Success);
    }
}

#[derive(Resource)]
struct Spike {
    glb: PathBuf,
    out: PathBuf,
    frames: u32,
    requested: bool,
    wireframe: bool,
}

fn spike_start(mut commands: ResMut<PreviewCommand>, spike: Res<Spike>) {
    commands.load = Some(LoadRequest {
        source: spike.glb.clone(),
        artifact: "spike".into(),
        clip: None,
    });
    if spike.wireframe {
        commands.wireframe = Some(true);
    }
}

fn spike_tick(
    mut spike: ResMut<Spike>,
    preview: Res<PreviewState>,
    mut commands: Commands,
    mut exit: MessageWriter<AppExit>,
) {
    spike.frames += 1;
    if !spike.requested {
        if (preview.loaded.is_some() && spike.frames > 90) || spike.frames > 900 {
            commands
                .spawn(Screenshot::primary_window())
                .observe(save_to_disk(spike.out.display().to_string()));
            spike.requested = true;
            spike.frames = 0;
        }
    } else if spike.frames > 120 {
        exit.write(AppExit::Success);
    }
}
