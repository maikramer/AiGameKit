//! Viber CLI — runs and validates AiGameKit declarative world XML.

use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;

use anyhow::{Context, Result};
use bevy::app::PluginGroup;
use clap::{CommandFactory, Parser, Subcommand};

use viber::bridge::{self, client::BridgeClient};
use viber::recipes::ParsedWorld;
use viber::recipes::spawn::{self, PendingWorld};
use viber::{
    animation, hud, music, particles, physics, player, recipes, scaffold, sky, spawner, terrain,
    worldsys, xml,
};

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
        /// Expose the debug bridge (BRP over HTTP; default port 15702)
        #[arg(long, default_missing_value = "15702", num_args = 0..=1)]
        bridge: Option<u16>,
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
    /// Drive a running engine (`viber run --bridge`): screenshot, input, tree, logs
    Debug {
        #[command(subcommand)]
        command: DebugCommand,
    },
}

#[derive(Subcommand)]
enum DebugCommand {
    /// Check if the debug bridge is up
    Probe {
        #[arg(long)]
        port: Option<u16>,
    },
    /// Capture a screenshot of the running window
    Screenshot {
        #[arg(short, long, default_value = "screenshot.png")]
        output: PathBuf,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long, default_value_t = 10_000)]
        timeout_ms: u64,
    },
    /// Dump the entity tree (name/parent/transform/components)
    Tree {
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        json: bool,
    },
    /// Dump recent log messages (the bridge "console")
    Logs {
        #[arg(long)]
        port: Option<u16>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
        #[arg(long)]
        json: bool,
    },
    /// Send a synthetic key event (aliases: w, space, enter, esc, up, ctrl…)
    Key {
        key: String,
        #[arg(long)]
        text: Option<String>,
        #[arg(long)]
        shift: bool,
        #[arg(long)]
        port: Option<u16>,
    },
    /// Type a string as synthetic key events
    Text {
        text: String,
        #[arg(long)]
        port: Option<u16>,
    },
    /// Click at window coordinates (logical pixels)
    Click {
        x: f32,
        y: f32,
        #[arg(long, default_value = "left")]
        button: String,
        #[arg(long)]
        port: Option<u16>,
    },
    /// Move the synthetic cursor
    Move {
        x: f32,
        y: f32,
        #[arg(long)]
        port: Option<u16>,
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
        "  entities: {} (groups {}, primitives {}, point lights {}, directional lights {}, cameras {}, gltf scenes {})",
        summary.entities(),
        summary.groups,
        summary.primitives,
        summary.point_lights,
        summary.directional_lights,
        summary.cameras,
        summary.gltf_scenes
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
    if summary.players > 0 {
        println!("  players: {}", summary.players);
    }
    if summary.static_spawners > 0
        || summary.dynamic_spawners > 0
        || summary.vegetation > 0
        || summary.spawn_exclusions > 0
    {
        println!(
            "  spawn groups: {} static, {} dynamic, {} vegetation ({} exclusion zones)",
            summary.static_spawners,
            summary.dynamic_spawners,
            summary.vegetation,
            summary.spawn_exclusions
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

fn run(path: &Path, bridge_port: Option<u16>) -> Result<()> {
    // Absolute from here on: the asset root and terrain base_dir must not
    // depend on the CWD (bevy resolves relative asset roots against the exe).
    let path = &std::path::absolute(path)?;
    let world = load_world(path)?;
    let title = format!(
        "Viber — {}",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("world")
    );
    // O shader do céu viaja embutido e é escrito no asset root do mundo
    // (materiais custom resolvem "shaders/sky.wgsl" pela asset root).
    let world_dir = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let shaders_dir = world_dir.join("shaders");
    let _ = std::fs::create_dir_all(&shaders_dir);
    let _ = std::fs::write(shaders_dir.join("sky.wgsl"), sky::SKY_WGSL);
    // Asset root: the folder that CONTAINS `assets/` — the world dir itself
    // when it has one (mirrored assets), else its `public/`, else default.
    let asset_root = match world_base_dir(path) {
        Some(dir) if dir.join("assets").is_dir() => dir,
        Some(dir) if dir.join("public").is_dir() => dir.join("public"),
        _ => PathBuf::from("assets"),
    };
    let mut app = bevy::app::App::new();
    let mut plugins = bevy::DefaultPlugins
        .set(bevy::window::WindowPlugin {
            primary_window: Some(bevy::window::Window {
                title,
                ..Default::default()
            }),
            ..Default::default()
        })
        .set(bevy::asset::AssetPlugin {
            file_path: asset_root.to_string_lossy().into_owned(),
            ..Default::default()
        });
    if bridge_port.is_some() {
        // A layer de logs do bridge tem de ser instalada no LogPlugin no boot.
        plugins = plugins.set(bridge::logs::log_plugin_with_bridge());
    }
    app.add_plugins(plugins);
    if let Some(port) = bridge_port {
        app.add_plugins(bridge::BridgePlugin { port });
        eprintln!("viber: debug bridge at http://127.0.0.1:{port} (try `viber debug probe`)");
    }
    app.insert_resource(PendingWorld {
        world,
        base_dir: world_base_dir(path),
    });
    // `worldsys::sun_drive` writes it and `sky::sky_update` reads it; nothing
    // was creating it, so both systems failed parameter validation.
    app.init_resource::<worldsys::SunState>();
    // `sky::spawn_sky` needs `Assets<SkyMaterial>`; without this plugin the
    // startup system panics and leaves `Assets<Mesh>` taken out of the world.
    app.add_plugins(bevy::pbr::MaterialPlugin::<sky::SkyMaterial>::default());
    app.add_plugins(animation::AnimationPlugin);
    app.add_plugins(physics::PhysicsPlugin {
        debug: std::env::var_os("VIBER_PHYSICS_DEBUG").is_some(),
    });
    app.add_plugins(terrain::TerrainPlugin);
    app.add_plugins(terrain::runtime::TerrainFeaturesPlugin);
    app.add_systems(bevy::app::Startup, spawn::startup);
    app.add_systems(
        bevy::app::Update,
        (
            spawn::orbit_camera_follow,
            spawn::auto_orbit,
            spawn::gltf_scene_spawner,
            spawn::orbit_camera_input,
            player::player_movement,
            player::dialogue_interaction,
            hud::hud_prompt_update,
            music::music_driver,
            worldsys::daycycle_drive,
            worldsys::sun_drive,
            worldsys::world_border_clamp,
            sky::sky_follow_camera,
            sky::sky_update,
            worldsys::seat_statics_once,
            hud::hud_toggle,
            particles::particle_emitter_update,
            spawner::instantiate_spawn_groups,
        ),
    );
    app.run();
    Ok(())
}

fn dispatch(command: Command) -> Result<std::process::ExitCode> {
    match command {
        Command::Create { name } => create(&name).map(|_| std::process::ExitCode::SUCCESS),
        Command::Run {
            path,
            bridge,
            release,
            no_cargo,
        } => {
            let world = resolve_world_path(path)?;
            if !no_cargo {
                if let Some(code) = delegate_run_to_cargo(&world, release)? {
                    return Ok(std::process::ExitCode::from(code as u8));
                }
            }
            run(&world, bridge)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("running {}", world.display()))
        }
        Command::Analyze { path, strict } => resolve_world_path(path).and_then(|world| {
            analyze(&world, strict)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("analyzing {}", world.display()))
        }),
        Command::Debug { command } => run_debug(command).map(|_| std::process::ExitCode::SUCCESS),
    }
}

// ---------------------------------------------------------------- debug client

fn print_tree(tree: &serde_json::Value) {
    let Some(entries) = tree.as_array() else {
        println!("{tree}");
        return;
    };
    println!("id         name                     parent     xyz               components");
    for entry in entries {
        let id = entry
            .get("id")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?");
        let name = entry
            .get("name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("-");
        let parent = entry
            .get("parent")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("-");
        let xyz = entry
            .get("translation")
            .and_then(serde_json::Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .map(|v| format!("{v:.1}"))
                    .collect::<Vec<_>>()
                    .join(",")
            })
            .unwrap_or_else(|| "-".into());
        let components = entry
            .get("components")
            .and_then(serde_json::Value::as_array)
            .map(|values| values.len())
            .unwrap_or(0);
        println!("{id:<10} {name:<24} {parent:<10} {xyz:<16} {components}");
    }
}

fn print_logs(logs: &serde_json::Value) {
    let Some(entries) = logs.as_array() else {
        println!("{logs}");
        return;
    };
    for entry in entries {
        let level = entry
            .get("level")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?");
        let target = entry
            .get("target")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?");
        let message = entry
            .get("message")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?");
        println!("[{level:<5}] {target}: {message}");
    }
}

fn run_debug(command: DebugCommand) -> Result<()> {
    match command {
        DebugCommand::Probe { port } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            let pong = client.probe()?;
            println!("bridge OK em {}:{} — {pong}", client.host, client.port);
        }
        DebugCommand::Screenshot {
            output,
            port,
            timeout_ms,
        } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            let source = client.screenshot_to_file(&output, timeout_ms)?;
            println!("✓ screenshot → {} (fonte: {source})", output.display());
        }
        DebugCommand::Tree { port, json } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            let tree = client.tree()?;
            if json {
                println!("{tree:#}");
            } else {
                print_tree(&tree);
            }
        }
        DebugCommand::Logs { port, limit, json } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            let logs = client.logs(limit)?;
            if json {
                println!("{logs:#}");
            } else {
                print_logs(&logs);
            }
        }
        DebugCommand::Key {
            key,
            text,
            shift,
            port,
        } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            client.key(&key, text, shift)?;
        }
        DebugCommand::Text { text, port } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            client.text(&text)?;
        }
        DebugCommand::Click { x, y, button, port } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            client.click(x, y, &button)?;
        }
        DebugCommand::Move { x, y, port } => {
            let client = BridgeClient::localhost(bridge::client::resolve_port(port));
            client.move_cursor(x, y)?;
        }
    }
    Ok(())
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
