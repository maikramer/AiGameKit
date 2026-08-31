//! Viber CLI — runs and validates AiGameKit declarative world XML.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use bevy::app::PluginGroup;
use clap::{Parser, Subcommand};

use viber::recipes::ParsedWorld;
use viber::recipes::spawn::{self, PendingWorld};
use viber::{recipes, terrain, xml};

/// Native Bevy engine for AiGameKit declarative worlds.
#[derive(Parser)]
#[command(name = "viber", version, about)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Run a world XML file in a Bevy window
    Run {
        /// Path to the world XML file
        path: PathBuf,
    },
    /// Parse and validate a world XML file without opening a window
    Analyze {
        /// Path to the world XML file
        path: PathBuf,
    },
}

fn load_world(path: &Path) -> Result<ParsedWorld> {
    let loaded = xml::include::load_world(path)?;
    recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
}

fn analyze(path: &Path) -> Result<()> {
    let world = load_world(path)?;
    let summary = recipes::summarize(&world);
    println!("Viber world: {}", path.display());
    println!(
        "  entities: {} (groups {}, primitives {}, point lights {}, cameras {})",
        summary.entities(),
        summary.groups,
        summary.primitives,
        summary.point_lights,
        summary.cameras
    );
    println!(
        "  ambient light: {}",
        if summary.has_ambient {
            "world-defined"
        } else {
            "bevy default"
        }
    );
    for warning in &world.warnings {
        eprintln!("warning: {warning}");
    }
    println!("OK");
    Ok(())
}

fn run(path: &Path) -> Result<()> {
    let world = load_world(path)?;
    let title = format!(
        "Viber — {}",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("world")
    );
    bevy::app::App::new()
        .add_plugins(bevy::DefaultPlugins.set(bevy::window::WindowPlugin {
            primary_window: Some(bevy::window::Window {
                title,
                ..Default::default()
            }),
            ..Default::default()
        }))
        .insert_resource(PendingWorld(world))
        .add_plugins(terrain::TerrainPlugin)
        .add_systems(bevy::app::Startup, spawn::startup)
        .add_systems(
            bevy::app::Update,
            (spawn::orbit_camera_follow, spawn::auto_orbit),
        )
        .run();
    Ok(())
}

fn main() -> std::process::ExitCode {
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Run { path } => run(&path).with_context(|| format!("running {}", path.display())),
        Command::Analyze { path } => {
            analyze(&path).with_context(|| format!("analyzing {}", path.display()))
        }
    };
    match result {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}
