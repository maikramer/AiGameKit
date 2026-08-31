//! Viber CLI — runs and validates AiGameKit declarative world XML.

use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;

use anyhow::{Context, Result};
use bevy::app::PluginGroup;
use clap::{CommandFactory, Parser, Subcommand};

use viber::recipes::ParsedWorld;
use viber::recipes::spawn::{self, PendingWorld};
use viber::{recipes, scaffold, terrain, xml};

/// Native Bevy engine for AiGameKit declarative worlds.
#[derive(Parser)]
#[command(name = "viber", version, about)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Create a new world project (folder + world.xml scaffold)
    Create {
        /// Project folder name, created inside the current directory
        name: String,
    },
    /// Run a world XML file in a Bevy window
    Run {
        /// Path to the world XML file (default: world.xml or worlds/*.xml in the current directory)
        path: Option<PathBuf>,
        /// Delegate with `cargo run --release` when inside a Viber checkout
        #[arg(long)]
        release: bool,
        /// Always use this binary — never delegate to cargo in a checkout
        #[arg(long)]
        no_cargo: bool,
    },
    /// Parse and validate a world XML file without opening a window
    Analyze {
        /// Path to the world XML file (default: world.xml or worlds/*.xml in the current directory)
        path: Option<PathBuf>,
        /// Treat not-implemented (skipped) tags as errors
        #[arg(long)]
        strict: bool,
    },
}

/// Marca o filho delegado para o binário reconstruído correr in-process
/// (sem re-delegar em `cargo run` — evita recursão).
const CARGO_DELEGATE_GUARD: &str = "VIBER_CLI_NO_CARGO_DELEGATE";

fn load_world(path: &Path) -> Result<ParsedWorld> {
    let loaded = xml::include::load_world(path)?;
    recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
}

fn world_base_dir(path: &Path) -> Option<PathBuf> {
    path.parent()
        .map(|p| p.to_path_buf())
        .filter(|p| !p.as_os_str().is_empty())
}

/// Resolve o mundo a usar: caminho explícito ou auto-descoberta no cwd
/// (`world.xml`, depois o primeiro `worlds/*.xml` por ordem alfabética).
fn resolve_world_path(path: Option<PathBuf>) -> Result<PathBuf> {
    let Some(path) = path else {
        let cwd = std::env::current_dir().context("reading the current directory")?;
        let default_world = cwd.join("world.xml");
        if default_world.is_file() {
            return Ok(default_world);
        }
        let worlds_dir = cwd.join("worlds");
        if worlds_dir.is_dir() {
            let mut xmls: Vec<PathBuf> = std::fs::read_dir(&worlds_dir)
                .with_context(|| format!("reading {}", worlds_dir.display()))?
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|p| p.extension().is_some_and(|ext| ext == "xml"))
                .collect();
            xmls.sort();
            if let Some(first) = xmls.first() {
                return Ok(first.clone());
            }
        }
        anyhow::bail!(
            "no world found in {} (looked for world.xml and worlds/*.xml) — pass one: `viber run world.xml`",
            cwd.display()
        );
    };
    Ok(path)
}

/// Procura um checkout do Viber (Cargo.toml do pacote `viber`) subindo a partir
/// de `from` — o análogo do `findEngineRoot` do vibegame-cli.
fn viber_checkout_root(from: &Path) -> Option<PathBuf> {
    let mut dir = Some(from.to_path_buf());
    for _ in 0..24 {
        let Some(current) = dir else { break };
        let cargo_toml = current.join("Cargo.toml");
        if cargo_toml.is_file()
            && current.join("src").join("main.rs").is_file()
            && std::fs::read_to_string(&cargo_toml)
                .is_ok_and(|text| text.contains("[package]") && text.contains("name = \"viber\""))
        {
            return Some(current);
        }
        dir = current.parent().map(Path::to_path_buf);
    }
    None
}

/// Dentro de um checkout, delega em `cargo run [--release] -- run <world>`
/// para correr o motor a partir do código-fonte (como `vibegame run` reconstrói
/// a engine). Devolve `Ok(None)` quando não há delegação (sem checkout, cargo
/// ausente ou guard activo) — o chamador corre in-process.
fn delegate_run_to_cargo(world: &Path, release: bool) -> Result<Option<i32>> {
    if std::env::var_os(CARGO_DELEGATE_GUARD).is_some() {
        return Ok(None);
    }
    let cwd = std::env::current_dir().context("reading the current directory")?;
    let Some(root) = viber_checkout_root(&cwd) else {
        return Ok(None);
    };
    let world = std::path::absolute(world)?;
    let mut command = StdCommand::new("cargo");
    command.current_dir(&root);
    if release {
        command.arg("--release");
    }
    command.arg("run").arg("--").arg("run").arg(&world);
    command.env(CARGO_DELEGATE_GUARD, "1");
    eprintln!(
        "viber: Viber checkout detected at {} — delegating to `cargo run{}`",
        root.display(),
        if release { " --release" } else { "" }
    );
    match command.status() {
        Ok(status) => Ok(Some(status.code().unwrap_or(1))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            eprintln!("warning: cargo not found on PATH — running the installed binary");
            Ok(None)
        }
        Err(error) => Err(error).context("running cargo run"),
    }
}

fn create(name: &str) -> Result<()> {
    let cwd = std::env::current_dir().context("reading the current directory")?;
    let world_path = scaffold::create_world_project(&cwd.join(name))?;
    println!("✓ Viber world created: {}", world_path.display());
    println!();
    println!("Next steps:");
    println!("  cd {name}");
    println!("  viber analyze world.xml   # headless validation");
    println!("  viber run world.xml       # open the Bevy window");
    Ok(())
}

fn analyze(path: &Path, strict: bool) -> Result<()> {
    let world = load_world(path)?;
    let summary = recipes::summarize(&world);
    println!("Viber world: {}", path.display());
    println!(
        "  entities: {} (groups {}, primitives {}, point lights {}, directional lights {}, cameras {})",
        summary.entities(),
        summary.groups,
        summary.primitives,
        summary.point_lights,
        summary.directional_lights,
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
    if summary.terrain > 0 || summary.ground_features() > 0 {
        println!(
            "  terrain: heightfield {}, ground features {} (pads {}, lakes {}, rivers {}, roads {} + networks {})",
            summary.terrain,
            summary.ground_features(),
            summary.terrain_pads,
            summary.lakes,
            summary.rivers,
            summary.roads,
            summary.road_networks
        );
    }
    if !world.skipped_tags.is_empty() {
        let total: usize = world.skipped_tags.values().sum();
        let mut entries: Vec<_> = world.skipped_tags.iter().collect();
        entries.sort_by_key(|(_, count)| std::cmp::Reverse(**count));
        let top: Vec<String> = entries
            .iter()
            .take(15)
            .map(|(tag, count)| format!("<{tag}>×{count}"))
            .collect();
        println!(
            "  not implemented (skipped): {total} elements across {} tags — {}{}",
            world.skipped_tags.len(),
            top.join(", "),
            if entries.len() > 15 { ", …" } else { "" }
        );
    }
    for warning in &world.warnings {
        eprintln!("warning: {warning}");
    }
    if strict && !world.skipped_tags.is_empty() {
        anyhow::bail!(
            "strict mode: {} not-implemented tags present ({} elements)",
            world.skipped_tags.len(),
            total_skipped(&world)
        );
    }
    println!("OK");
    Ok(())
}

fn total_skipped(world: &ParsedWorld) -> usize {
    world.skipped_tags.values().sum()
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
        .insert_resource(PendingWorld {
            world,
            base_dir: world_base_dir(path),
        })
        .add_plugins(terrain::TerrainPlugin)
        .add_plugins(terrain::runtime::TerrainFeaturesPlugin)
        .add_systems(bevy::app::Startup, spawn::startup)
        .add_systems(
            bevy::app::Update,
            (spawn::orbit_camera_follow, spawn::auto_orbit),
        )
        .run();
    Ok(())
}

fn dispatch(command: Command) -> Result<std::process::ExitCode> {
    match command {
        Command::Create { name } => create(&name).map(|_| std::process::ExitCode::SUCCESS),
        Command::Run {
            path,
            release,
            no_cargo,
        } => {
            let world = resolve_world_path(path)?;
            if !no_cargo {
                if let Some(code) = delegate_run_to_cargo(&world, release)? {
                    return Ok(std::process::ExitCode::from(code as u8));
                }
            }
            run(&world)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("running {}", world.display()))
        }
        Command::Analyze { path, strict } => resolve_world_path(path).and_then(|world| {
            analyze(&world, strict)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("analyzing {}", world.display()))
        }),
    }
}

fn main() -> std::process::ExitCode {
    let cli = Cli::parse();
    let Some(command) = cli.command else {
        let _ = Cli::command().print_help();
        return std::process::ExitCode::SUCCESS;
    };
    match dispatch(command) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("error: {error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}
