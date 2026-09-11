//! Viber CLI — runs and validates AiGameKit declarative world XML.

use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use bevy::app::PluginGroup;
use bevy::ecs::schedule::IntoScheduleConfigs;
use bevy::log::info;
use bevy_kira_audio::AudioApp;
use clap::{CommandFactory, Parser, Subcommand};
use serde_json::Value;

use viber::bridge::{self, client::BridgeClient};
use viber::combat;
use viber::luau;
use viber::profiler::{Group, timed};
use viber::recipes::ParsedWorld;
use viber::recipes::spawn::{self, PendingWorld};
use viber::ui;
use viber::{
    ai, ambient, animation, audit, camera, economy, feedback, grass, harvest, hud, impact, menus,
    meshopt, music, particles, physics, physics_fx, player, postfx, profiler, prop_tint, prune,
    quests, recipes, render_lod, save, scaffold, skills, sky, spawner, terrain, textures, trail,
    travel, vitals, worldsys, xml,
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
        /// Expose the debug bridge (BRP over HTTP). Sem valor, escolhe a
        /// primeira porta LIVRE a partir de 15702 — duas engines de mundos
        /// diferentes (ex.: agentes com worlds/qa-*.xml) deixam de disputar
        /// a 15702. `--bridge 15702` fixa a porta explícita.
        #[arg(long, num_args = 0..=1)]
        bridge: Option<Option<u16>>,
        /// Build with the dev profile instead of release (faster to compile,
        /// several times slower to play — the engine is dominated by Bevy
        /// and Rapier, which only get fast when optimized)
        #[arg(long)]
        debug: bool,
        /// Accepted for compatibility — release is now the default
        #[arg(long, hide = true)]
        release: bool,
        /// Always use this binary — never delegate to cargo in a checkout
        #[arg(long)]
        no_cargo: bool,
    },
    /// Manual target/ housekeeping — the same pruning `viber run` does at
    /// startup (drops the OPPOSITE profile's build/test binaries and
    /// incremental caches; with `--debug` it drops release instead of dev)
    Prune {
        /// Drop target/release (as if the current run were `--debug`)
        #[arg(long)]
        debug: bool,
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
        /// Mundo que identifica a engine alvo — forma curta, antes do
        /// subcomando: `viber debug --world qa-pontes lua '…'`. As variantes
        /// também aceitam `--world` depois, que ganha se ambas vierem.
        #[arg(long)]
        world: Option<PathBuf>,
        #[command(subcommand)]
        command: DebugCommand,
    },
    /// Sessão partilhada de QA: engine única por mundo com lease de uso
    Session {
        #[command(subcommand)]
        command: SessionCommand,
    },
}

#[derive(Subcommand)]
enum DebugCommand {
    /// Check if the debug bridge is up
    Probe {
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo: caminho do XML, nome de
        /// ficheiro ou stem (`qa-pontes`). Com várias engines vivas, `probe`
        /// LISTA-as em vez de escolher uma ao acaso.
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Capture a screenshot of the running window
    Screenshot {
        #[arg(short, long, default_value = "screenshot.png")]
        output: PathBuf,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long, default_value_t = 10_000)]
        timeout_ms: u64,
    },
    /// Dump the entity tree (name/parent/transform/components)
    Tree {
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    /// Dump recent log messages (the bridge "console")
    Logs {
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
        #[arg(long)]
        json: bool,
    },
    /// Profiler snapshot from the running engine (fps, frame time, entities,
    /// active Luau scripts, particle emitters, terrain chunks, LOD swaps).
    ///
    /// Cheap: safe to poll in a loop. `viber.debug.stats()` is NOT — it walks
    /// every entity and costs ~100 ms on a 60k-entity world, which shows up
    /// in the very frame times you are trying to measure.
    Prof {
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long)]
        json: bool,
        /// Number of samples to average. The engine's `fps` field is the
        /// instantaneous frame rate and swings wildly while chunks stream —
        /// a run of samples is the number worth quoting in a report.
        #[arg(long, default_value_t = 1)]
        samples: u32,
        /// Delay between samples, milliseconds
        #[arg(long, default_value_t = 500)]
        interval_ms: u64,
        /// Rich tab dump: systems|world|physics|audio|extras|all (pt aliases:
        /// sistemas|mundo|fisica|audio|extras|tudo). Implies --json shape;
        /// human printers per tab unless --json.
        #[arg(long)]
        tab: Option<String>,
        /// Export the FULL profiler snapshot to a JSON file. Optional path
        /// (default: $TMPDIR/viber-profiles/viber-profile-<epoch>.json).
        #[arg(long, num_args = 0..=1, default_missing_value = "")]
        export: Option<String>,
    },
    /// Execute Luau na engine (`viber.lua`): mover/teleportar o player,
    /// desativar/despawnar entidades, dar itens… API completa: `viber.debug.*`
    /// (ver AGENTS.md). Ex.: `viber debug lua 'return viber.debug.player().x'`
    Lua {
        /// Código Luau (globals persistem entre chamadas — REPL)
        code: Option<String>,
        /// Ler o código de um ficheiro em vez do argumento
        #[arg(short, long)]
        file: Option<PathBuf>,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        /// Imprime a resposta JSON completa (ok/result/applied/warnings)
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
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Type a string as synthetic key events
    Text {
        text: String,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Click at window coordinates (logical pixels)
    Click {
        x: f32,
        y: f32,
        #[arg(long, default_value = "left")]
        button: String,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Move the synthetic cursor
    Move {
        x: f32,
        y: f32,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
    },
}

/// Sessão partilhada de QA: uma engine por mundo + lease de uso para agentes
/// paralelos ("liberado" / "ocupado, aguarde"). Protocolo no AGENTS.md.
#[derive(Subcommand)]
enum SessionCommand {
    /// Estado da sessão do mundo (default: world.xml no cwd)
    Status {
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Reclama exclusividade (exit 3 = ocupado, com quem/quanto falta)
    Claim {
        /// Nome da tarefa/agente (aparece no "ocupado por …")
        #[arg(long, default_value = "anon")]
        owner: String,
        /// TTL em segundos (auto-expira se o agente morrer)
        #[arg(long, default_value_t = viber::session::DEFAULT_TTL_SECS)]
        ttl: u64,
        /// Espera até N segundos pela libertação em vez de falhar logo
        #[arg(long)]
        wait: Option<u64>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Renova o lease do dono
    Touch {
        /// Nome usado no claim
        #[arg(long)]
        owner: Option<String>,
        #[arg(long, default_value_t = viber::session::DEFAULT_TTL_SECS)]
        ttl: u64,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Liberta a sessão (passe o mesmo --owner do claim)
    Release {
        /// Nome usado no claim
        #[arg(long)]
        owner: Option<String>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Sobe a engine partilhada (bloqueia o lease durante o boot)
    Up {
        #[arg(long)]
        world: Option<PathBuf>,
        /// Porta do bridge. Sem valor, procura a primeira livre a partir de
        /// 15702 — duas sessões (mundos diferentes) deixam de colidir.
        #[arg(long)]
        port: Option<u16>,
    },
    /// Lista todas as sessões conhecidas (mundo, porta, estado, dono)
    List,
    /// Desce a engine partilhada (não pode haver lease ativo de outro)
    Down {
        #[arg(long)]
        world: Option<PathBuf>,
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
fn delegate_run_to_cargo(world: &Path, debug: bool, bridge: Option<u16>) -> Result<Option<i32>> {
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
    // `--release` is an argument of `cargo run`, not of `cargo`: emitting it
    // before the subcommand made `viber run --release` fail outright, which
    // is why every checkout run so far was an unoptimized dev build.
    command.arg("run");
    if !debug {
        command.arg("--release");
    }
    command.arg("--").arg("run").arg(&world);
    if let Some(port) = bridge {
        command.arg("--bridge").arg(port.to_string());
    }
    command.arg("--no-cargo");
    command.env(CARGO_DELEGATE_GUARD, "1");
    eprintln!(
        "viber: Viber checkout detected at {} — delegating to `cargo run{}`",
        root.display(),
        if debug { "" } else { " --release" }
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

/// Housekeeping do `target/` (ver `viber::prune`): dentro de um checkout, o
/// arranque do run remove executáveis e caches incrementais do perfil OPOSTO
/// — os binários de teste do Bevy em dev pesam ~2.2 GB cada e reconstruírem-se
/// num relink de segundos. Silencioso quando não há nada a limpar.
fn run_target_housekeeping(active_debug: bool) {
    // O filho delegado (`cargo run -- run … --no-cargo`) não repete: o pai
    // limpou antes de lançar o cargo.
    if std::env::var_os(CARGO_DELEGATE_GUARD).is_some() {
        return;
    }
    let Ok(cwd) = std::env::current_dir() else { return };
    let Some(root) = viber_checkout_root(&cwd) else { return };
    let report = prune::housekeeping(&root, active_debug);
    if let Some(line) = report.describe() {
        eprintln!("viber: prune: {line}");
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
    let path = &std::path::absolute(path)?;
    let world = load_world(path)?;
    let summary = recipes::summarize(&world);
    println!("Viber world: {}", path.display());
    println!(
        "  entities: {} (groups {}, compositions {}, primitives {}, point lights {}, directional lights {}, cameras {}, gltf scenes {})",
        summary.entities(),
        summary.groups,
        summary.compositions,
        summary.primitives,
        summary.point_lights,
        summary.directional_lights,
        summary.cameras,
        summary.gltf_scenes
    );
    if summary.compositions > 0 {
        println!(
            "  compositions: {} root(s), {} part(s) com colisor composto por primitiva",
            summary.compositions, summary.composition_parts
        );
    }
    if !world.prototypes.is_empty() || !world.prototype_instances.is_empty() {
        let instances: usize = world.prototype_instances.values().sum();
        let used: Vec<String> = world
            .prototype_instances
            .iter()
            .map(|(id, count)| format!("{id}×{count}"))
            .collect();
        println!(
            "  prototypes: {} definido(s), {} instância(s){}",
            world.prototypes.len(),
            instances,
            if used.is_empty() {
                String::new()
            } else {
                format!(" — {}", used.join(", "))
            }
        );
    }
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
            "  terrain: heightfield {}, ground features {} (pads {}, lakes {}, rivers {}, cliffs {}, caves {}, arches {}, bridges {}, rock fields {}, roads {} + networks {}, decals {})",
            summary.terrain,
            summary.ground_features(),
            summary.terrain_pads,
            summary.lakes,
            summary.rivers,
            summary.cliffs,
            summary.caves,
            summary.arches,
            summary.bridges,
            summary.rock_fields,
            summary.roads,
            summary.road_networks,
            summary.ground_decals
        );
    }
    if summary.players > 0 {
        println!("  players: {}", summary.players);
    }
    // Report de UI fora do bloco dos players: um mundo pode ter UiRoot sem
    // PlayerGLTF (HUD autoral, câmara orbital) e tinha de aparecer aqui.
    if summary.ui_roots > 0 || summary.ui_stylesheets > 0 {
        println!(
            "  declarative ui: {} root(s), {} elements, {} stylesheet(s)",
            summary.ui_roots, summary.ui_elements, summary.ui_stylesheets
        );
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
    // Auditoria de assets: ausentes, compressões/formatos não suportados,
    // colliders ausentes em glTF — lê só cabeçalhos, sem engine. O config
    // do jogo é obrigatório também aqui: o audit usa os MESMOS paths que o
    // runtime vai usar.
    let world_dir = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let config = viber::config::load(&world_dir)?;
    let (world_dir, asset_roots) = world_asset_dirs(path, &config);
    let report = audit::audit(&world, &world_dir, &asset_roots, &config);
    if report.references > 0 || !report.colliderless.is_empty() {
        println!(
            "  assets: {} referência(s) auditada(s), {} problema(s)",
            report.references,
            report.issues.len()
        );
        for issue in &report.issues {
            let glyph = match issue.severity {
                audit::Severity::Missing => "✗",
                audit::Severity::Warning => "⚠",
                audit::Severity::Info => "ℹ",
            };
            println!("    {glyph} {}", issue.message);
        }
        if !report.colliderless.is_empty() {
            let top: Vec<String> = report.colliderless.iter().take(8).cloned().collect();
            println!(
                "    ℹ {} modelo(s) glTF sem collider (passam através): {}{}",
                report.colliderless.len(),
                top.join(", "),
                if report.colliderless.len() > 8 {
                    ", …"
                } else {
                    ""
                }
            );
        }
    }
    for warning in &world.warnings {
        eprintln!("warning: {warning}");
    }
    for id in &world.unknown_prototypes {
        eprintln!("warning: <Use prototype=\"{id}\"> sem definição — instância ignorada");
    }
    if strict && !world.skipped_tags.is_empty() {
        anyhow::bail!(
            "strict mode: {} not-implemented tags present ({} elements)",
            world.skipped_tags.len(),
            total_skipped(&world)
        );
    }
    if strict && !world.unknown_prototypes.is_empty() {
        anyhow::bail!(
            "strict mode: {} <Use> com protótipo desconhecido: {}",
            world.unknown_prototypes.len(),
            world.unknown_prototypes.join(", ")
        );
    }
    if strict && report.missing_count() > 0 {
        anyhow::bail!(
            "strict mode: {} asset(s) ausente(s) — ver os ✗ na secção assets",
            report.missing_count()
        );
    }
    println!("OK");
    Ok(())
}

/// world_dir (scripts/estilos relativos) + asset roots por ordem de
/// precedência (docs/ASSETS.md): a pasta do mundo SEMPRE primeiro (é lá que
/// os shaders especializados são escritos e os overrides vivem), as roots
/// extra do `config.yaml` do jogo a seguir. A engine não descobre nada por
/// conta própria — quem serve o quê é declarado pelo jogo.
fn world_asset_dirs(path: &Path, config: &viber::config::GameConfig) -> (PathBuf, Vec<PathBuf>) {
    let world_dir = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let roots = config.asset_roots(&world_dir);
    (world_dir, roots)
}

fn total_skipped(world: &ParsedWorld) -> usize {
    world.skipped_tags.values().sum()
}

fn run(path: &Path, bridge_port: Option<u16>) -> Result<()> {
    // Absolute from here on: the asset root and terrain base_dir must not
    // depend on the CWD (bevy resolves relative asset roots against the exe).
    let path = &std::path::absolute(path)?;
    let world = load_world(path)?;
    // O config.yaml do jogo é OBRIGATÓRIO e lido antes de tudo: é dele que
    // vêm as asset roots e os diretórios (docs/ASSETS.md).
    let world_dir_pre = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let config = viber::config::load(&world_dir_pre)?;
    let title = config.title.clone().unwrap_or_else(|| {
        format!(
            "Viber — {}",
            path.file_name().and_then(|n| n.to_str()).unwrap_or("world")
        )
    });
    // Asset roots por ordem de precedência: a pasta do mundo primeiro, as
    // roots extra do config a seguir (`VIBER_ASSET_POOL` continua a ganhar
    // para debug/CI). Calculadas ANTES de escrever o shader: materiais
    // custom resolvem o shader especializado pela 1.ª root, e o `run`
    // escreve-o sempre nela — as roots extra nunca são escritas.
    let (world_dir, asset_roots) = world_asset_dirs(path, &config);
    let asset_root = asset_roots[0].clone();
    // O shader do céu é ESPECIALIZADO por mundo: a config do <Sky>/<DayCycle>/
    // <Weather> é injectada como consts WGSL (o uniform de material custom no
    // Bevy 0.19 nunca re-uploads — ver sky.rs). O mesmo para a água (relógio
    // do glint + vento das ondas) e para o blend de camadas do terreno
    // (world span dos splats) — este último só se algum <Terrain> pedir
    // `layers`; mundos sem camadas nem tocam no ficheiro. O diretório é
    // SEMPRE `shaders/` da 1.ª root: os `Material::fragment_shader()` são fns
    // ESTÁTICAS que lêem `shaders/{sky,water,terrain_chunk}.wgsl` contra as
    // roots — a escrita e a leitura têm de casar (contrato de conteúdo,
    // docs/ASSETS.md).
    let sky_config = sky::SkyConfig::from_world(&world.entities).with_env_override();
    let water_config = terrain::water_material::WaterSurfaceConfig::from_world(&world.entities);
    let layers_config = terrain::layer_material::TerrainChunkConfig::from_world(&world.entities);
    let shaders_dir = asset_root.join("shaders");
    let _ = std::fs::create_dir_all(&shaders_dir);
    for (name, contents) in [
        ("sky.wgsl", sky_config.render_world_shader()),
        ("water.wgsl", water_config.render_world_shader()),
    ] {
        if let Err(e) = std::fs::write(shaders_dir.join(name), contents) {
            eprintln!("viber: falha ao escrever {}/{name}: {e}", shaders_dir.display());
        }
    }
    if let Some(layers_config) = &layers_config {
        if let Err(e) = std::fs::write(
            shaders_dir.join("terrain_chunk.wgsl"),
            layers_config.render_world_shader(),
        ) {
            eprintln!(
                "viber: falha ao escrever {}/terrain_chunk.wgsl: {e}",
                shaders_dir.display()
            );
        }
    }
    // SSR da água (Fase B): shader ESTÁTICO (sem especialização por mundo) —
    // só se escreve com o gate ligado, para não tocar no disco de mundos que
    // não o usam.
    if viber::water_ssr::water_ssr_requested() {
        if let Err(e) = std::fs::write(
            shaders_dir.join("water_ssr.wgsl"),
            viber::water_ssr::WATER_SSR_WGSL,
        ) {
            eprintln!(
                "viber: falha ao escrever {}/water_ssr.wgsl: {e}",
                shaders_dir.display()
            );
        }
    }
    let mut app = bevy::app::App::new();
    // Registered before `AssetPlugin`, which snapshots the sources when it
    // builds. The reader is multi-root (world → extras do config) and expands
    // `EXT_meshopt_compression`, so the engine reads compressed GLBs as
    // authored — no per-example mirror.
    meshopt::register_asset_source(&mut app, asset_roots.clone());
    // O config viaja COMO RESOURCE: ambient (sfx_dir), save (dir), terreno
    // (textures_dir) e o spawn (bgm_dir) leem-no de lá.
    app.insert_resource(config.clone());
    app.insert_resource(save::SaveDir(Some(config.save_dir().to_path_buf())));
    // O modelo do céu também viaja como resource — o IBL (`ibl.rs`) pinta o
    // cubemap com a MESMA radiância que o domo desenha e os probes regionais
    // (`probes.rs`) usam os mesmos coeficientes. Sem ele os dois lêem `None` e
    // ficam presos em `analytic`, qualquer que seja o `<Sky model>`.
    app.insert_resource(sky::SkyModelState::from_config(&sky_config));
    // (a linha do modelo resolvido sai depois de `add_plugins` — o subscriber
    // de tracing só existe a partir daí; ver `viber debug logs`)
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
    // SSR de reflexões raster (Fase B, src/water_ssr.rs): reflexo de cena na
    // água + chão molhado na chuva por raymarch sobre o depth prepass.
    // OPT-IN (`VIBER_WATER_SSR=1`) enquanto não existir acumulação temporal
    // própria do passe — sem ela o reflexo dança com as ondas (medido).
    app.add_plugins(viber::water_ssr::WaterSsrPlugin);
    // Probes regionais (src/probes.rs): bounce de bioma nos pads com tint,
    // parallax-corrected. DEFAULT ON — `VIBER_PROBES=0` desliga.
    app.add_plugins(viber::probes::RegionalProbesPlugin);
    // Céu/dia/noite, IBL e probes regionais resolvem o modelo por AQUI (o
    // subscriber de tracing já existe): é a linha que confirma na QA que o
    // gate `<Sky model>`/`VIBER_SKY_MODEL` chegou ao render e ao IBL.
    info!(
        "sky: modelo {} (attr <Sky model> / VIBER_SKY_MODEL)",
        match sky_config.model {
            sky::SkyModel::Nishita => "nishita",
            sky::SkyModel::Analytic => "analytic",
        }
    );
    // Áudio: backend kira (bevy_kira_audio) com buses tipados — o
    // `AudioMixerSettings` (save/menu/XML) empurra volumes para os canais
    // (crate::music::mixer_sync) e tudo o que está a tocar responde ao vivo.
    // O bevy_audio/rodio do Bevy fica compilado mas sem players — nenhum
    // som nasce por ele.
    app.add_plugins(bevy_kira_audio::AudioPlugin);
    app.add_audio_channel::<viber::music::MusicBus>();
    app.add_audio_channel::<viber::music::SfxBus>();
    if let Some(port) = bridge_port {
        app.add_plugins(bridge::BridgePlugin { port });
        // O ping passa a identificar o mundo servido — o `viber debug
        // --world` valida-o contra o engine.json (registo stale → erro, não
        // mutação do mundo errado).
        app.insert_resource(bridge::BridgeIdentity {
            world: path.display().to_string(),
        });
        // Regista a engine no registo de sessões MESMO fora de `session up`:
        // assim qualquer `viber debug …` descobre a porta sozinho. Se já há
        // uma engine viva registada (sessão de outro agente), não mexe — o
        // descobridor encontra-a a ela.
        let session = viber::session::SessionPaths::for_world(path);
        let register = match session.engine_info() {
            Some(existing) if existing.pid != std::process::id() => {
                !bridge::client::port_alive(existing.port)
            }
            _ => true,
        };
        if register {
            let _ = session.write_engine(&viber::session::EngineInfo {
                pid: std::process::id(),
                port,
                world: path.display().to_string(),
                started_at_ms: std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_millis() as u64)
                    .unwrap_or(0),
                log: String::new(),
            });
        }
        // O agente que lançou a engine lê esta linha: o caminho para APONTAR
        // os seus `viber debug` a ESTA engine (e não à de outro agente).
        eprintln!(
            "viber: debug bridge at http://127.0.0.1:{port} — viber debug --world {} … (ou export VIBER_BRIDGE_PORT={port})",
            path.display()
        );
    }
    app.insert_resource(PendingWorld {
        world,
        base_dir: world_base_dir(path),
    });
    // `worldsys::sun_drive` aims the directional light from it; nothing was
    // creating it, so that system failed parameter validation.
    app.init_resource::<worldsys::SunState>();
    // Sombras de qualidade (passe visual r1): mapas 4096 (dir) / 1536 (pontos)
    // — os defaults (2048/1024) deixavam as sombras das árvores granuladas
    // mesmo ao pé do herói. Custo de VRAM/fill aceito; KTX2 já devolveu a
    // margem (docs/PERFORMANCE.md).
    app.insert_resource(bevy::light::DirectionalLightShadowMap { size: 4096 });
    app.insert_resource(bevy::light::PointLightShadowMap { size: 1536 });
    // `hud_menu_system` corre sempre (main loop), mas o `HudMenuState` só
    // nascia dentro de `build_menu` — mundos sem `TabbedModal` (o HUD é agora
    // declarativo) panicas na validação do `ResMut` todos os frames.
    app.init_resource::<hud::menu::HudMenuState>();
    // `sky::spawn_sky` needs `Assets<SkyMaterial>`; without this plugin the
    // startup system panics and leaves `Assets<Mesh>` taken out of the world.
    // O mesmo para o material de água (`Assets<WaterMaterial>` no bootstrap
    // do terreno).
    app.add_plugins(bevy::pbr::MaterialPlugin::<sky::SkyMaterial>::default());
    app.add_plugins(bevy::pbr::MaterialPlugin::<
        terrain::water_material::WaterMaterial,
    >::default());
    // O material das camadas de terreno POR CHUNK (`layers="…"`): o
    // bootstrap precisa de `Assets<TerrainChunkMaterial>` — sem este plugin
    // um mundo com camadas degradaria para o caminho legado.
    app.add_plugins(bevy::pbr::MaterialPlugin::<
        terrain::layer_material::TerrainChunkMaterial,
    >::default());
    app.add_plugins(animation::AnimationPlugin);
    app.add_plugins(physics::PhysicsPlugin {
        debug: std::env::var_os("VIBER_PHYSICS_DEBUG").is_some(),
    });
    // Pós-processamento (exposição/bloom/SSAO) na câmara do mundo; os
    // `pp-*` das `<BiomeRegion>` conduzem-no. `VIBER_NO_POSTFX=1` desliga.
    app.add_plugins(postfx::PostFxPlugin);
    // IBL vivo do céu (LightProbe + cubemap da paleta da atmosfera, filtrado
    // na GPU) — ambiente/reflexos que seguem a hora do dia. `VIBER_NO_IBL=1`.
    app.add_plugins(viber::ibl::SkyIblPlugin);
    app.add_plugins(terrain::TerrainPlugin);
    app.add_plugins(terrain::runtime::TerrainFeaturesPlugin);
    // Sub-bosque instanciado (a relva que o `<Vegetation>` não consegue ser):
    // tiles de lâminas fundidas a 16 m à volta da câmara, vento no vertex
    // shader, paleta por bioma. `VIBER_GRASS=0` desliga; `VIBER_GRASS_DENSITY`
    // escala as densidades.
    app.add_plugins(grass::GrassPlugin);
    // Escritor único de samplers/mipmaps das texturas carregadas (o registro
    // `WorldTiledTextures` é consumido aqui; escrever o sampler noutro
    // sistema reabria a corrida clamp/REPEAT das texturas de chão).
    app.add_plugins(textures::TexturesPlugin);
    // Fase 2: Luau — scripts do `scripts_dir` do config, com `on_update(dt)`.
    app.add_plugins(luau::LuauScriptPlugin {
        scripts_dir: config.scripts_dir_on(&world_dir),
    });
    app.add_plugins(combat::CombatPlugin);
    // Vitals juice (passe de juice r1): deteção robusta de level-up (qualquer
    // fonte de XP) + fanfarra — bursts magic/sparkle, toast, kick de
    // exposição e SFX.
    app.add_plugins(vitals::VitalsPlugin);
    // Feedback de combate (loop 2): dano flutuante, vignette/i-frames,
    // TargetBar/BossBar reais, respawn, status effects.
    app.add_plugins(feedback::FeedbackPlugin);
    // Economia (loop 4): vault ouro/madeira/pedra, chips vivos, hotbar [1]/[2].
    app.add_plugins(economy::EconomyPlugin);
    // UI & menus (loop 5): toasts visuais, modal [Q], loja [K], loading.
    app.add_plugins(menus::MenusPlugin);
    // Travel/Nota/wayfinding (loop 6): marcos, viagem rápida, waypoint,
    // registry de hostis por região.
    app.add_plugins(travel::TravelPlugin);
    // Save/load & opções (loop 7): save JSON, volumes na tab Opções.
    app.add_plugins(save::SavePlugin);
    // Skills/abilities/bombas (loop 8): dash/cura/golpe forte, passivas,
    // guard/parry, profundidade do melee.
    app.add_plugins(skills::SkillsPlugin);
    // Mundo vivo (loop 9): fog/tint por BiomeRegion, orçamento de luzes,
    // gestos idle de NPC, SFX.
    app.add_plugins(ambient::AmbientPlugin);
    // day_tint para os materiais dos GltfScene (copas/casas/props) — sem
    // isto ficam com albedo de dia sob o luar e leem-se "recortados" à noite
    // (peça exploração, r10; mecanismo documentado no módulo).
    app.add_plugins(prop_tint::PropTintPlugin);
    // Física Fase 3 (loop 10): knockback cinemático + destrutíveis com queda.
    app.add_plugins(physics_fx::PhysicsFxPlugin);
    // Colheita nativa (port do plugin `destructible`): árvores/rochas
    // destrutíveis — ferramenta na mão, quedas, estilhaços, loot no vault.
    app.add_plugins(harvest::HarvestPlugin);
    // Sword trace (ribbon da lâmina) + bursts one-shot de partícula.
    app.add_plugins(trail::TrailPlugin);
    app.add_plugins(particles::BurstPlugin);
    // FX de impacto de combate: recoil (squash-and-stretch) do inimigo
    // atingido + anéis de onda de choque (finisher/slam/bomba/abate).
    app.add_plugins(impact::ImpactFxPlugin);
    // FX de água: splash na entrada/saída e esteira de ondas de quem
    // caminha dentro do lago/rio (a lâmina é estática — o rasto tem de
    // viver no ECS, ver `terrain::water_fx`).
    app.add_plugins(terrain::water_fx::WaterFxPlugin);
    // Trauma da camera shake — o melee (e o dano recebido) somam aqui.
    app.init_resource::<camera::CameraShake>();
    // Solavanco direcional da câmara no impacto (mola; o combate soma impulsos).
    app.init_resource::<camera::CameraKick>();
    // Quests & diálogo (loop 3): 21 quests JSON, flow [E] nos DialogueNPC,
    // QuestTracker, hooks viber.quest_* p/ Luau.
    app.add_plugins(quests::QuestsPlugin);
    // IA (FSM Rust + respawn): criaturas de <DynamicSpawner> SEM script caem
    // aqui — sem este plugin nasciam estátuas eternas e a RespawnQueue
    // nunca drenava.
    app.add_plugins(ai::AiPlugin);
    // LOD de render: culling por distância nas instâncias de spawner +
    // orçamento de sombras. Sem isto as ~9700 cenas glTF do simple-rpg
    // (60k entidades) entram todas nas 4 cascatas de sombra a cada frame.
    // `VIBER_RENDER_LOD=0` desliga o culling + a ladder de LOD: é o A/B
    // honesto para medir o ganho sem trocar de binário.
    if std::env::var("VIBER_RENDER_LOD").as_deref() != Ok("0") {
        app.add_plugins(render_lod::RenderLodPlugin);
    }
    // Profiler: overlay F3 (fps/frame/entidades/scripts ativos) + `viber.profiler`.
    app.add_plugins(profiler::ProfilerPlugin);
    // UI declarativa (XML + folha de estilo + `viber.ui` no Luau): o HUD do
    // mundo é autoria, não código.
    app.add_plugins(ui::UiPlugin);
    // A instalação de `viber.ui` tem de ganhar ao runtime Luau: sem ordem,
    // um script activado no frame 1 via `luau_on_add` podia ver `viber.ui`
    // nil (morria — `Added` dispara 1×) ou queimar o warn-once.
    app.add_systems(
        bevy::app::Update,
        ui::install_ui_script_api
            .before(luau::luau_on_add)
            .before(luau::luau_update),
    );
    // As mutações que os scripts enfileiram via `viber.ui.*` têm de ser
    // aplicadas DEPOIS de `luau_update` as produzir — sem ordem, chegavam um
    // frame tarde (ou em corrida com o produtor). Ordena-se o SET: o
    // `apply_ui_commands` já vive no `UiSet::Script` e re-adicioná-lo aqui
    // duplicava a instância no schedule (pânico "more than one instance").
    // Sem ciclo: a chain luau não referencia UiSets.
    app.configure_sets(
        bevy::app::Update,
        ui::UiSet::Script.after(luau::luau_update),
    );
    // E a UI PUBLICA antes dos scripts lerem: os cliques de um frame chegam
    // ao `on_update` NO MESMO frame, haja como for a ordem arbitrária entre
    // plugins — sem isto, `viber.ui.clicked(…)` perdia cliques por corrida
    // (o publish rodava depois do script e limpava a vista).
    app.configure_sets(
        bevy::app::Update,
        (ui::UiSet::Collect, ui::UiSet::Build, ui::UiSet::Bind).before(luau::luau_update),
    );
    // O espelho `UiModalsOpen`→`MenusOpen` lê o valor que o driver dos modais
    // declarativos escreve neste frame (UiSet::Script) — sem ordem, os
    // consumos de `MenusOpen` (hotbar/movimento/câmara) viam o frame anterior.
    app.add_systems(
        bevy::app::Update,
        menus::mirror_ui_modals_open.after(ui::UiSet::Script),
    );
    // O melee lê o `HarvestContext` do MESMO frame (gate colheita-melee):
    // sem ordem, um press [J] ao ENTRAR no alcance colhia e golpeava com
    // contexto stale, e ao SAIR perdia o press. A ordenação vai pelo
    // `HarvestSet` (o conjunto inteiro da colheita) — re-adicionar o
    // `harvest_context_system` aqui duplicava a instância no schedule e
    // panica ("more than one instance").
    app.add_systems(
        bevy::app::Update,
        combat::player_melee_attack.after(harvest::HarvestSet),
    );
    app.add_systems(bevy::app::Startup, spawn::startup);
    app.add_systems(
        bevy::app::Update,
        (
            timed(Group::Hud, hud::hud_health_sync),
            hud::hud_xp_sync,
            // Balão de diálogo: única via que decrementa o timer e volta a
            // esconder — sem registo, o balão ficava no ecrã para sempre.
            timed(Group::Hud, hud::hud_balloon_update),
            // Modal [Q]: sincroniza abas/conteúdos e trata cliques.
            timed(Group::Hud, hud::menu::hud_menu_system),
        ),
    );
    app.add_systems(
        bevy::app::Update,
        (
            // Deterministic order: the rigid follow skips third-person
            // cameras, the player steers their yaw with A/D, then the
            // third-person camera trails it. All three touch OrbitCamera.
            (
                timed(Group::Camera, spawn::orbit_camera_follow),
                timed(Group::Player, player::player_movement),
                timed(Group::Camera, camera::third_person_camera),
            )
                .chain(),
            timed(Group::Camera, spawn::auto_orbit),
            timed(Group::Spawner, spawn::gltf_scene_spawner),
            timed(Group::Player, player::dialogue_interaction),
            hud::hud_prompt_update,
            hud::compass::hud_compass_update,
            timed(Group::Hud, hud::hud_minimap_update),
            timed(Group::World, music::audio_loop_starter),
            timed(Group::World, music::mixer_sync),
            timed(Group::World, music::music_driver),
            timed(Group::World, worldsys::daycycle_drive),
            timed(Group::World, worldsys::sun_drive),
            // Scheduler do `<Weather cycle>` (chuva alvo determinística) —
            // tem de correr ANTES do `atmosphere_drive` (a intensidade
            // contínua de chuva entra na paleta no mesmo frame; as constraints
            // do ambient.rs só ordenam se o sistema existir).
            timed(Group::World, worldsys::weather_drive),
            // Publica a paleta da hora (AtmosphereState) a partir do sol já
            // apontado por `sun_drive` — céu, névoa, grading e exposure a
            // leem. Sem este registo o recurso fica no default de dia para
            // sempre (céu/névoa congelados, aurora a visualizar-se de dia).
            timed(Group::World, worldsys::atmosphere_drive),
        ),
    );
    // Tuplo dividido: o Bevy limita tuples de sistemas a 20 elementos e o
    // bloco acima cresceu (weather/atmosphere drives). Constraints são
    // explícitas (.after), a separação não muda semântica.
    app.add_systems(
        bevy::app::Update,
        (
            // Clamp da borda DEPOIS do movimento/dash do player (mesmo frame) —
            // sem ordem, o clamp viajava um frame atrás do WASD.
            worldsys::world_border_clamp
                .after(player::player_movement)
                .after(skills::abilities_system),
            sky::sky_follow_camera,
            worldsys::seat_statics_once,
            worldsys::resolve_pending_place,
            hud::hud_toggle,
            timed(Group::Fx, particles::particle_emitter_update),
            timed(Group::Spawner, spawner::instantiate_spawn_groups),
            vitals::debug_damage,
            // Dano recebido também abana a câmara (peso ∝ dano).
            feedback::shake_on_player_hurt,
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
            debug,
            release: _,
            no_cargo,
        } => {
            run_target_housekeeping(debug);
            let world = resolve_world_path(path)?;
            // `--bridge` sem valor escolhe porta livre ANTES da delegação —
            // a porta impressa no arranque tem de ser a que a engine usa de
            // facto (e a que o agente exporta/usa nos comandos debug).
            let bridge = match bridge {
                None => None,
                Some(Some(port)) => Some(port),
                Some(None) => Some(free_bridge_port(bridge::DEFAULT_BRIDGE_PORT)?),
            };
            if !no_cargo {
                if let Some(code) = delegate_run_to_cargo(&world, debug, bridge)? {
                    return Ok(std::process::ExitCode::from(code as u8));
                }
            }
            run(&world, bridge)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("running {}", world.display()))
        }
        Command::Prune { debug } => {
            let cwd = std::env::current_dir().context("reading the current directory")?;
            let root = viber_checkout_root(&cwd)
                .context("not inside a Viber checkout — no target/ to prune")?;
            let report = prune::housekeeping(&root, debug);
            match report.describe() {
                Some(line) => println!("viber: prune: {line}"),
                None => println!("viber: prune: nothing to clean"),
            }
            Ok(std::process::ExitCode::SUCCESS)
        }
        Command::Analyze { path, strict } => resolve_world_path(path).and_then(|world| {
            analyze(&world, strict)
                .map(|_| std::process::ExitCode::SUCCESS)
                .with_context(|| format!("analyzing {}", world.display()))
        }),
        Command::Debug { world, command } => {
            run_debug(command, world).map(|_| std::process::ExitCode::SUCCESS)
        }
        Command::Session { command } => run_session(command),
    }
}

// ---------------------------------------------------------------- session

/// Exit code convencionado para "ocupado, aguarde" — agentes usam-no para
/// decidir fazer outro trabalho em vez de girar.
const EXIT_BUSY: u8 = 3;

fn probe_engine(port: u16) -> bool {
    bridge::client::BridgeClient::localhost(port)
        .probe()
        .is_ok()
}

fn session_paths(world: Option<&PathBuf>) -> Result<(viber::session::SessionPaths, PathBuf)> {
    // Flag vazia (unwrap_or_default nos chamadores) = auto-descoberta.
    let flag = world.filter(|p| !p.as_os_str().is_empty()).cloned();
    let world = resolve_world_path(flag)?;
    Ok((
        viber::session::SessionPaths::for_world(&world),
        std::path::absolute(&world)?,
    ))
}

fn run_session(command: SessionCommand) -> Result<std::process::ExitCode> {
    use std::process::ExitCode;
    match command {
        SessionCommand::Status { world } => {
            let (paths, world_abs) = session_paths(Some(&world.unwrap_or_default()))?;
            let lease = paths.busy();
            let engine = paths.engine_info();
            match (&engine, &lease) {
                (Some(engine), Some((owner, remaining))) => {
                    if probe_engine(engine.port) {
                        println!(
                            "OCUPADO por '{owner}' (expira em ~{} s) — engine viva em :{} ({})",
                            remaining.as_secs(),
                            engine.port,
                            world_abs.display()
                        );
                    } else {
                        println!(
                            "OCUPADO por '{owner}' MAS a engine em :{} não responde — `viber session down && viber session up`",
                            engine.port
                        );
                    }
                }
                (Some(engine), None) => {
                    if probe_engine(engine.port) {
                        println!(
                            "LIBERADO — engine viva em :{} ({})",
                            engine.port,
                            world_abs.display()
                        );
                    } else {
                        println!(
                            "LIBERADO, mas a engine em :{} está MORTA — `viber session down && viber session up`",
                            engine.port
                        );
                    }
                }
                (None, lease) => {
                    if let Some((owner, remaining)) = lease {
                        println!(
                            "SEM engine (não há engine.json) mas OCUPADO por '{owner}' (~{} s)",
                            remaining.as_secs()
                        );
                    } else {
                        println!(
                            "SEM SESSÃO — suba a engine partilhada: `viber session up` ({})",
                            world_abs.display()
                        );
                    }
                }
            }
            Ok(ExitCode::SUCCESS)
        }
        SessionCommand::Claim {
            owner,
            ttl,
            wait,
            world,
        } => {
            let (paths, _) = session_paths(Some(&world.unwrap_or_default()))?;
            match paths.claim(
                &owner,
                Duration::from_secs(ttl),
                wait.map(Duration::from_secs),
            )? {
                viber::session::ClaimOutcome::Acquired { ttl } => {
                    println!(
                        "RECLAMADO por '{owner}' (TTL {ttl:?}) — faça o QA e `viber session release`"
                    );
                    Ok(ExitCode::SUCCESS)
                }
                viber::session::ClaimOutcome::Busy {
                    owner: busy,
                    remaining,
                } => {
                    println!(
                        "OCUPADO por '{busy}' (expira em ~{} s) — aguarde ou faça outro trabalho",
                        remaining.as_secs()
                    );
                    Ok(ExitCode::from(EXIT_BUSY))
                }
            }
        }
        SessionCommand::Touch { owner, ttl, world } => {
            let (paths, _) = session_paths(Some(&world.unwrap_or_default()))?;
            let renewed = paths.touch(owner.as_deref(), Duration::from_secs(ttl))?;
            println!("RENOVADO por {renewed:?}");
            Ok(ExitCode::SUCCESS)
        }
        SessionCommand::Release { owner, world } => {
            let (paths, _) = session_paths(Some(&world.unwrap_or_default()))?;
            if paths.release(owner.as_deref())? {
                println!("LIBERTADO — sessão disponível para o próximo agente");
            } else {
                println!("não havia lease ativo");
            }
            Ok(ExitCode::SUCCESS)
        }
        SessionCommand::List => session_list().map(|_| ExitCode::SUCCESS),
        SessionCommand::Up { world, port } => {
            session_up(world.as_deref(), port).map(|_| ExitCode::SUCCESS)
        }
        SessionCommand::Down { world } => session_down(world.as_deref()).map(|_| ExitCode::SUCCESS),
    }
}

/// Sobe a engine partilhada do mundo: reclama o lease durante o boot,
/// spawna `viber run <mundo> --no-cargo --bridge <porta>` destacado com log
/// em ficheiro e espera o bridge responder. No fim liberta o lease.
/// Primeira porta livre a partir de `start`: nem o SO a tem ocupada, nem
/// outra sessão a reclamou no seu `engine.json`. A ordem é DETERMINÍSTICA
/// (`start..start+span`) — é o contrato documentado no help do CLI e o que o
/// fluxo de QA assume (`VIBER_BRIDGE_PORT=15702 viber debug …` só bate certo
/// se a engine partilhada estiver na primeira porta livre).
///
/// A corrida original (dois `session up` simultâneos escolhem a MESMA
/// primeira porta livre — a sonda bind solta-se antes de qualquer engine
/// nascer — e o perdedor ficava a falar com o mundo do vencedor) já não se
/// resolve com offset aleatório: resolve-a o `session up` confirmando, pelo
/// pid que o `viber.ping` devolve, que a engine que responde é a QUE ELE
/// spawnou — em caso negativo tenta a porta livre seguinte (ver `session_up`).
fn free_bridge_port(start: u16) -> Result<u16> {
    // Pré-filtro barato (TCP, 250 ms) antes do probe HTTP caro (~2 s por
    // órfão) — mesmo padrão do `session_port` no cliente.
    let taken: Vec<u16> = viber::session::SessionPaths::all()
        .iter()
        .filter_map(|(_, paths)| paths.engine_info())
        .filter(|engine| bridge::client::port_alive(engine.port))
        .filter(|engine| probe_engine(engine.port))
        .map(|engine| engine.port)
        .collect();
    let span = 64u16;
    for offset in 0..span {
        // `start` pode estar perto do teto u16 — portas aí acima não existem.
        let Some(port) = start.checked_add(offset) else {
            break;
        };
        if taken.contains(&port) {
            continue;
        }
        if std::net::TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return Ok(port);
        }
    }
    bail!(
        "nenhuma porta livre entre {start} e {}",
        start.saturating_add(span - 1)
    )
}

/// O pid que a engine na porta devolve no `viber.ping` — `None` se a porta
/// não responde (boot a meio, porta morta, listener wedged) ou se o bridge
/// não se identifica (binário mais velho que o ping com pid).
fn bridge_ping_pid(port: u16) -> Option<u32> {
    let pong = BridgeClient::localhost(port).probe().ok()?;
    pong.get("pid")
        .and_then(Value::as_u64)
        .map(|pid| pid as u32)
}

/// Estado de todas as sessões — o mapa que um agente paralelo precisa antes
/// de decidir onde trabalhar.
fn session_list() -> Result<()> {
    let sessions = viber::session::SessionPaths::all();
    if sessions.is_empty() {
        println!("sem sessões — `viber session up` cria a primeira");
        return Ok(());
    }
    for (slug, paths) in sessions {
        let engine = paths.engine_info();
        let state = match &engine {
            Some(engine) if probe_engine(engine.port) => format!("viva :{}", engine.port),
            Some(engine) => format!("MORTA (registada :{})", engine.port),
            None => "sem engine".to_string(),
        };
        let lease = match paths.busy() {
            Some((owner, remaining)) => {
                format!("ocupada por '{owner}' (~{}s)", remaining.as_secs())
            }
            None => "liberada".to_string(),
        };
        let world = engine
            .as_ref()
            .map(|engine| engine.world.clone())
            .unwrap_or_else(|| "?".to_string());
        println!("{slug}: {state} — {lease} — {world}");
    }
    Ok(())
}

/// Sobe a engine partilhada do mundo: reclama o lease durante o boot,
/// spawna `viber run <mundo> --no-cargo --bridge <porta>` destacado com log
/// em ficheiro e espera o bridge responder. No fim liberta o lease.
/// Confirma, pelo pid que o `viber.ping` devolve, que a engine que responde
/// é A QUE ESTE PROCESSO SPAWNOU — dois `session up` em corrida escolhem a
/// mesma primeira porta livre; sem a confirmação, o perdedor registava no
/// engine.json a porta da engine do vencedor (e falava com o mundo errado).
/// Em corrida perdida (ou bridge que não responde — listener wedged a barrar
/// o bind), mata o próprio filho e tenta a porta livre seguinte, até
/// 3 tentativas. Com `--port` explícito não há próxima porta: uma tentativa.
fn session_up(world: Option<&Path>, port: Option<u16>) -> Result<()> {
    let (paths, world_abs) = session_paths(world.map(PathBuf::from).as_ref())?;
    // Claim curto só para serializar boot — falha se outro agente está em QA.
    match paths.claim("session-up", std::time::Duration::from_secs(120), None)? {
        viber::session::ClaimOutcome::Busy { owner, remaining } => bail!(
            "sessão ocupada por '{owner}' (~{} s) — engine provavelmente já viva; veja `viber session status`",
            remaining.as_secs()
        ),
        viber::session::ClaimOutcome::Acquired { .. } => {}
    }
    let result = (|| -> Result<()> {
        if let Some(engine) = paths.engine_info() {
            if probe_engine(engine.port) {
                bail!(
                    "engine já viva em :{} ({}) — use-a em vez de subir outra (GPU!)",
                    engine.port,
                    engine.world
                );
            }
            eprintln!("viber session: engine anterior morta — a substituir");
        }
        // Com porta explícita não há "próxima porta" para tentar em corrida.
        let attempts = if port.is_some() { 1 } else { 3 };
        for attempt in 1..=attempts {
            // Renova o claim de boot — 3 tentativas × 90 s excederiam o TTL.
            paths.touch(Some("session-up"), std::time::Duration::from_secs(120))?;
            let attempt_port = match port {
                Some(port) => port,
                None => free_bridge_port(viber::bridge::DEFAULT_BRIDGE_PORT)?,
            };
            let log = paths.log_file();
            if let Some(parent) = log.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let log_file = std::fs::File::create(&log)
                .with_context(|| format!("a criar log {}", log.display()))?;
            let exe = std::env::current_exe()?;
            println!(
                "viber session: a arrancar {} --bridge {} (log: {})",
                world_abs.display(),
                attempt_port,
                log.display()
            );
            let mut child = std::process::Command::new(exe)
                .arg("run")
                .arg(&world_abs)
                .arg("--no-cargo")
                .arg("--bridge")
                .arg(attempt_port.to_string())
                .stdin(std::process::Stdio::null())
                .stdout(log_file.try_clone()?)
                .stderr(log_file)
                .spawn()
                .context("a spawnar a engine partilhada")?;
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(90);
            let mut timed_out = false;
            loop {
                if let Ok(Some(status)) = child.try_wait() {
                    paths.clear_engine();
                    bail!(
                        "engine saiu durante o boot ({status}) — veja o log {}",
                        log.display()
                    );
                }
                // O ping identifica a engine (pid do processo): só registamos
                // o engine.json quando a que responde é O NOSSO filho. Custo
                // zero no caminho feliz — é o MESMO ping que o wait já pagava.
                if let Some(pid) = bridge_ping_pid(attempt_port) {
                    if pid == child.id() {
                        paths.write_engine(&viber::session::EngineInfo {
                            pid: child.id(),
                            port: attempt_port,
                            world: world_abs.display().to_string(),
                            started_at_ms: std::time::SystemTime::now()
                                .duration_since(std::time::UNIX_EPOCH)
                                .map(|d| d.as_millis() as u64)
                                .unwrap_or(0),
                            log: log.display().to_string(),
                        })?;
                        println!(
                            "viber session: engine viva em :{attempt_port} (pid {}) — `viber session claim` antes de usar",
                            child.id()
                        );
                        return Ok(());
                    }
                    // Outra engine responde nessa porta — a nossa não conseguiu
                    // o bind e fica viva com a janela aberta: matar e tentar a
                    // porta livre seguinte.
                    eprintln!(
                        "viber session: porta {attempt_port} disputada (engine pid {pid} respondeu ao ping) — tentativa {attempt}/{attempts}"
                    );
                    break;
                }
                if std::time::Instant::now() > deadline {
                    timed_out = true;
                    break;
                }
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
            let _ = child.kill();
            let _ = child.wait();
            paths.clear_engine();
            if timed_out {
                // Pode ser um listener wedged (TCP aceita, ping nunca chega) a
                // barrar o bind do NOSSO bridge — nova porta em vez de
                // desistir à primeira.
                eprintln!(
                    "viber session: bridge em :{attempt_port} não respondeu em 90 s — tentativa {attempt}/{attempts}"
                );
            }
        }
        bail!(
            "bridge não arrancou em {attempts} tentativa(s) — veja o log {} (porta disputada por outra engine ou boot falhado)",
            paths.log_file().display()
        )
    })();
    let _ = paths.release(Some("session-up"));
    result
}

/// Desce a engine partilhada (SIGTERM via `kill`; o lease tem de estar livre
/// ou ser nosso).
fn session_down(world: Option<&Path>) -> Result<()> {
    let (paths, _) = session_paths(world.map(PathBuf::from).as_ref())?;
    if let Some((owner, remaining)) = paths.busy() {
        if remaining > std::time::Duration::ZERO {
            bail!(
                "sessão ocupada por '{owner}' (~{} s) — `release` do dono ou espere o TTL",
                remaining.as_secs()
            );
        }
    }
    let Some(engine) = paths.engine_info() else {
        println!("viber session: nenhuma engine registada — nada a fazer");
        return Ok(());
    };
    // Identidade antes do sinal: um PID reutilizado pode ser um processo
    // inocente (`tail -f` do log da engine, um editor com o caminho aberto)
    // — procurar "viber" como SUBSTRING do cmdline inteiro batia neles.
    // Compara o argv[0] (primeiro componente antes do NUL) com o executável
    // atual canónico; fallback: basename exatamente "viber" (a engine é
    // sempre spawnada via `current_exe()` no `session up`). Se o /proc nem
    // existe, o processo já morreu — mantemos o fluxo antigo de limpar os
    // metadados.
    let cmdline = Path::new("/proc")
        .join(engine.pid.to_string())
        .join("cmdline");
    if let Ok(raw) = std::fs::read(&cmdline) {
        let argv0 = raw
            .split(|byte| *byte == 0)
            .next()
            .map(String::from_utf8_lossy)
            .unwrap_or_default()
            .into_owned();
        let our_exe = std::env::current_exe()
            .ok()
            .and_then(|exe| std::fs::canonicalize(exe).ok());
        let is_engine = match our_exe
            .as_deref()
            .map(|exe| std::fs::canonicalize(&argv0).map(|arg| arg == exe))
        {
            // argv[0] absoluto e resolvível: tem de ser O NOSSO binário.
            Some(Ok(same_exe)) => same_exe,
            // argv[0] relativo, apagado desde o spawn, ou current_exe falhou:
            // o basename exato "viber" chega (um inocente com o caminho da
            // engine em qualquer outro argumento já não conta).
            _ => Path::new(&argv0)
                .file_name()
                .is_some_and(|name| name == "viber"),
        };
        if !is_engine {
            bail!(
                "pid {} não parece uma engine viber (argv[0] `{argv0}`; PID reutilizado?) — engine.json mantido; limpe à mão se confirmar",
                engine.pid
            );
        }
    }
    let kill = std::process::Command::new("kill")
        .arg(engine.pid.to_string())
        .status();
    match kill {
        Ok(status) if status.success() => {
            println!("viber session: engine pid {} desligada", engine.pid)
        }
        _ if cmdline.exists() => {
            // O kill falhou mas o processo continua vivo — típico de EPERM
            // (engine de outro utilizador). Não é "já morta": NÃO limpar.
            bail!(
                "sem permissão para sinalizar o pid {} — engine.json mantido",
                engine.pid
            );
        }
        _ => eprintln!(
            "viber session: kill {} falhou (já morta?) — meta-dados limpos na mesma",
            engine.pid
        ),
    }
    paths.clear_engine();
    Ok(())
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

/// Aceita aliases pt/en dos tabs do profiler → id canónico da bridge.
fn normalize_prof_tab(tab: &str) -> String {
    match tab.trim().to_lowercase().as_str() {
        "sistemas" | "systems" => "systems".into(),
        "mundo" | "world" => "world".into(),
        "fisica" | "física" | "physics" => "physics".into(),
        "audio" | "áudio" => "audio".into(),
        "extras" => "extras".into(),
        "tudo" | "all" => "all".into(),
        other => other.into(),
    }
}

/// Impressão humana por tab (`viber debug prof --tab mundo` etc.).
fn print_prof_tab(tab: &str, value: &serde_json::Value) {
    match tab {
        "world" => {
            if let Some(player) = value.get("player") {
                println!(
                    "player {}  pos {:.1} {:.1} {:.1}  yaw {:.0}°  chão {}",
                    player["name"].as_str().unwrap_or("?"),
                    player["pos"]["x"].as_f64().unwrap_or(0.0),
                    player["pos"]["y"].as_f64().unwrap_or(0.0),
                    player["pos"]["z"].as_f64().unwrap_or(0.0),
                    player["yaw_deg"].as_f64().unwrap_or(0.0),
                    if player["grounded"].as_bool() == Some(true) {
                        "sim"
                    } else {
                        "não"
                    },
                );
            } else {
                println!("player (nenhum)");
            }
            if let Some(camera) = value.get("camera") {
                println!(
                    "câmera {}  pos {:.1} {:.1} {:.1}",
                    camera["name"].as_str().unwrap_or("?"),
                    camera["pos"]["x"].as_f64().unwrap_or(0.0),
                    camera["pos"]["y"].as_f64().unwrap_or(0.0),
                    camera["pos"]["z"].as_f64().unwrap_or(0.0),
                );
            }
            println!(
                "entidades {}  próximas {}/{} no raio {:.0} m",
                value["entity_count"].as_u64().unwrap_or(0),
                value["nearby"].as_array().map(|a| a.len()).unwrap_or(0),
                value["nearby_in_radius"].as_u64().unwrap_or(0),
                value["nearby_radius"].as_f64().unwrap_or(0.0),
            );
            for near in value["nearby"].as_array().into_iter().flatten() {
                println!(
                    "  {:>7.1}m  {}  #{}  [{}]",
                    near["dist"].as_f64().unwrap_or(0.0),
                    near["name"].as_str().unwrap_or("?"),
                    near["entity"].as_u64().unwrap_or(0),
                    near["tags"]
                        .as_array()
                        .map(|t| t
                            .iter()
                            .filter_map(|v| v.as_str())
                            .collect::<Vec<_>>()
                            .join(","))
                        .unwrap_or_default(),
                );
            }
        }
        "physics" => {
            let bodies = &value["bodies"];
            println!(
                "corpos {} (fixos {} · din {} · cin {})  sono {}/{} acordados",
                bodies["total"].as_u64().unwrap_or(0),
                bodies["fixed"].as_u64().unwrap_or(0),
                bodies["dynamic"].as_u64().unwrap_or(0),
                bodies["kinematic"].as_u64().unwrap_or(0),
                bodies["sleeping"].as_u64().unwrap_or(0),
                bodies["awake"].as_u64().unwrap_or(0),
            );
            println!(
                "colisores {}  sensores {}  pendentes {}  cct {}",
                value["colliders"]["total"].as_u64().unwrap_or(0),
                value["colliders"]["sensors"].as_u64().unwrap_or(0),
                value["pending_colliders"].as_u64().unwrap_or(0),
                value["cct"].as_u64().unwrap_or(0),
            );
            if let Some(rapier) = value.get("rapier") {
                println!(
                    "rapier corpos {}  colisores {}  juntas {}  dt {:.4}",
                    rapier["bodies"].as_u64().unwrap_or(0),
                    rapier["colliders"].as_u64().unwrap_or(0),
                    rapier["impulse_joints"].as_u64().unwrap_or(0),
                    rapier["timestep"].as_f64().unwrap_or(0.0),
                );
            }
            if let Some(step) = value.get("step") {
                println!(
                    "step {:.2} ms (média {:.2} · p95 {:.2})",
                    step["last_ms"].as_f64().unwrap_or(0.0),
                    step["avg_ms"].as_f64().unwrap_or(0.0),
                    step["p95_ms"].as_f64().unwrap_or(0.0),
                );
            }
            for (shape, count) in value["colliders"]["by_shape"]
                .as_object()
                .into_iter()
                .flatten()
            {
                println!("  {shape}: {count}");
            }
        }
        "audio" => {
            let buses = &value["buses"];
            println!(
                "buses master {:.2}  música {:.2}  sfx {:.2}",
                buses["master"].as_f64().unwrap_or(0.0),
                buses["music"].as_f64().unwrap_or(0.0),
                buses["sfx"].as_f64().unwrap_or(0.0),
            );
            println!(
                "sinks {} total · {} a tocar · {} pausados · {} muted · {} spatial · {} loop",
                value["total"].as_u64().unwrap_or(0),
                value["playing"].as_u64().unwrap_or(0),
                value["paused"].as_u64().unwrap_or(0),
                value["muted"].as_u64().unwrap_or(0),
                value["spatial"].as_u64().unwrap_or(0),
                value["looping"].as_u64().unwrap_or(0),
            );
            for layer in value["layers"].as_array().into_iter().flatten() {
                println!(
                    "  layer {} base {:.2}{}",
                    layer["layer"].as_str().unwrap_or("?"),
                    layer["base_volume"].as_f64().unwrap_or(0.0),
                    if layer["paused"].as_bool() == Some(true) {
                        " [pausa]"
                    } else {
                        ""
                    },
                );
            }
        }
        "systems" => print_prof(value),
        _ => println!("{value:#}"),
    }
}

/// Resumo humano do snapshot `viber.profiler`.
fn print_prof(prof: &serde_json::Value) {
    let get = |key: &str| prof.get(key).and_then(serde_json::Value::as_f64);
    let count = |key: &str| {
        prof.get(key)
            .and_then(serde_json::Value::as_u64)
            .map(|v| v.to_string())
            .unwrap_or_else(|| "—".into())
    };
    let fps = get("fps")
        .map(|v| format!("{v:.0}"))
        .unwrap_or_else(|| "—".into());
    let frame = get("frame_ms_avg")
        .map(|v| format!("{v:.1} ms"))
        .unwrap_or_else(|| "—".into());
    println!("FPS {fps}   frame {frame}");
    println!(
        "entidades {}   partículas {}   terreno {}",
        count("entities"),
        count("particle_emitters"),
        count("terrain_chunks")
    );
    let scripts = prof.get("scripts");
    let total = scripts
        .and_then(|s| s.get("total"))
        .and_then(serde_json::Value::as_u64)
        .map(|v| v.to_string())
        .unwrap_or_else(|| "—".into());
    let active = scripts
        .and_then(|s| s.get("active"))
        .and_then(serde_json::Value::as_u64)
        .map(|v| v.to_string())
        .unwrap_or_else(|| "—".into());
    let uptime = get("uptime_s")
        .map(|v| format!("{v:.0}"))
        .unwrap_or_else(|| "—".into());
    println!("scripts {total} (ativos {active})   uptime {uptime} s");
    if let Some(min) = get("min_fps_window") {
        println!("pior fps (janela ~3 s): {min:.0}");
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

/// Samples the profiler `samples` times and reports the distribution.
///
/// One `prof` call is a snapshot of a single frame; a world that streams
/// terrain and drains a spawner can read 12 fps and 60 fps seconds apart.
/// Averaging (and reporting the worst sample) is what makes a before/after
/// comparison mean anything.
fn print_prof_samples(
    client: &BridgeClient,
    samples: u32,
    interval_ms: u64,
    json: bool,
) -> Result<()> {
    // `--samples 4294967295` não pode tentar pré-alocar ~68 GB (abort do
    // processo): cap razoável + crescimento on-demand.
    let samples = samples.min(10_000);
    let mut fps = Vec::new();
    let mut frame_ms = Vec::new();
    let mut last = Value::Null;
    for index in 0..samples {
        if index > 0 {
            std::thread::sleep(Duration::from_millis(interval_ms));
        }
        let prof = client.prof()?;
        if let Some(value) = prof.get("fps").and_then(Value::as_f64) {
            fps.push(value);
        }
        if let Some(value) = prof.get("frame_ms_avg").and_then(Value::as_f64) {
            frame_ms.push(value);
        }
        last = prof;
    }
    if fps.is_empty() {
        bail!("o profiler não devolveu `fps` em nenhuma amostra");
    }
    let mean = |values: &[f64]| values.iter().sum::<f64>() / values.len() as f64;
    let worst = fps.iter().cloned().fold(f64::INFINITY, f64::min);
    let best = fps.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if json {
        let summary = serde_json::json!({
            "samples": fps.len(),
            "interval_ms": interval_ms,
            "fps_avg": mean(&fps),
            "fps_min": worst,
            "fps_max": best,
            "frame_ms_avg": mean(&frame_ms),
            "last": last,
        });
        println!("{summary:#}");
    } else {
        println!(
            "fps  média {:.1}  |  pior {:.1}  |  melhor {:.1}   ({} amostras a cada {} ms)",
            mean(&fps),
            worst,
            best,
            fps.len(),
            interval_ms
        );
        println!("frame média {:.2} ms", mean(&frame_ms));
        print_prof(&last);
    }
    Ok(())
}

fn run_debug(command: DebugCommand, parent_world: Option<PathBuf>) -> Result<()> {
    // O `--world` pode vir antes do subcomando (parent) ou depois (variante);
    // a forma da variante ganha — é a mais próxima do comando.
    fn merge_world<'a>(
        world: &'a Option<PathBuf>,
        parent: &'a Option<PathBuf>,
    ) -> Option<&'a std::path::Path> {
        world.as_deref().or(parent.as_deref())
    }
    match command {
        DebugCommand::Probe { port, world } => {
            // O probe é a ferramenta de orientação: com várias engines vivas
            // LISTA-as em vez de falhar (os restantes subcomandos falham —
            // mutar a engine errada é pior do que nenhum comando).
            match bridge::client::resolve_target(port, merge_world(&world, &parent_world))? {
                bridge::client::TargetResolution::Port(port) => {
                    let client = BridgeClient::localhost(port);
                    let pong = client.probe()?;
                    println!("bridge OK em {}:{} — {pong}", client.host, client.port);
                }
                bridge::client::TargetResolution::Ambiguous(engines) => {
                    println!(
                        "{} engines vivas — aponta a tua com --world/--port:",
                        engines.len()
                    );
                    println!("{}", bridge::client::format_engines(&engines));
                }
            }
        }
        DebugCommand::Screenshot {
            output,
            port,
            world,
            timeout_ms,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let source = client.screenshot_to_file(&output, timeout_ms)?;
            println!("✓ screenshot → {} (fonte: {source})", output.display());
        }
        DebugCommand::Tree { port, world, json } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let tree = client.tree()?;
            if json {
                println!("{tree:#}");
            } else {
                print_tree(&tree);
            }
        }
        DebugCommand::Logs {
            port,
            world,
            limit,
            json,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let logs = client.logs(limit)?;
            if json {
                println!("{logs:#}");
            } else {
                print_logs(&logs);
            }
        }
        DebugCommand::Prof {
            port,
            world,
            json,
            samples,
            interval_ms,
            tab,
            export,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            if let Some(path) = export {
                let path = (!path.is_empty()).then(|| PathBuf::from(&path));
                let result = client.prof_export(path.as_deref())?;
                println!(
                    "✓ export → {} ({} bytes)",
                    result["path"].as_str().unwrap_or("?"),
                    result["bytes"].as_u64().unwrap_or(0)
                );
                return Ok(());
            }
            if let Some(tab) = tab {
                let tab = normalize_prof_tab(&tab);
                let value = client.prof_tab(&tab)?;
                if json || tab == "all" || tab == "extras" {
                    println!("{value:#}");
                } else {
                    print_prof_tab(&tab, &value);
                }
                return Ok(());
            }
            if samples <= 1 {
                let prof = client.prof()?;
                if json {
                    println!("{prof:#}");
                } else {
                    print_prof(&prof);
                }
            } else {
                print_prof_samples(&client, samples, interval_ms, json)?;
            }
        }
        DebugCommand::Key {
            key,
            text,
            shift,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            client.key(&key, text, shift)?;
        }
        DebugCommand::Text { text, port, world } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            client.text(&text)?;
        }
        DebugCommand::Click {
            x,
            y,
            button,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            client.click(x, y, &button)?;
        }
        DebugCommand::Move { x, y, port, world } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            client.move_cursor(x, y)?;
        }
        DebugCommand::Lua {
            code,
            file,
            port,
            world,
            json,
        } => {
            let source = match (code, file) {
                (Some(code), _) => code,
                (None, Some(path)) => std::fs::read_to_string(&path)
                    .with_context(|| format!("a ler {}", path.display()))?,
                (None, None) => {
                    eprintln!("uso: viber debug lua '<código>' | --file <ficheiro.lua>");
                    return Ok(());
                }
            };
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let response = client.lua(&source)?;
            let ok = response.get("ok").and_then(Value::as_bool).unwrap_or(false);
            if json {
                println!("{response:#}");
            } else if ok {
                match response.get("result") {
                    Some(Value::Null) | None => println!("(nil)"),
                    Some(value) => println!("{value:#}"),
                }
                if let Some(applied) = response.get("applied").and_then(Value::as_u64) {
                    if applied > 0 {
                        eprintln!("({applied} operações aplicadas)");
                    }
                }
                for warning in response
                    .get("warnings")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or_default()
                {
                    eprintln!("aviso: {warning}");
                }
            } else {
                let error = response
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("erro desconhecido");
                for warning in response
                    .get("warnings")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or_default()
                {
                    eprintln!("aviso: {warning}");
                }
                bail!("erro Luau: {error}");
            }
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

#[cfg(test)]
mod tests {
    use super::*;

    /// As três formas de `--bridge`: ausente = sem bridge, nu = porta livre
    /// (auto), com valor = fixa. É o contrato que evita duas engines a
    /// disputar a 15702 quando dois agentes sobem mundos qa-*.xml em paralelo.
    #[test]
    fn test_run_bridge_flag_shapes() {
        let none = Cli::try_parse_from(["viber", "run", "w.xml"]).expect("sem --bridge");
        let bare = Cli::try_parse_from(["viber", "run", "w.xml", "--bridge"]).expect("--bridge nu");
        let fixed = Cli::try_parse_from(["viber", "run", "w.xml", "--bridge", "15999"])
            .expect("--bridge N");
        match (none.command, bare.command, fixed.command) {
            (
                Some(Command::Run { bridge: a, .. }),
                Some(Command::Run { bridge: b, .. }),
                Some(Command::Run { bridge: c, .. }),
            ) => {
                assert_eq!(a, None, "sem flag");
                assert_eq!(b, Some(None), "flag nu = auto");
                assert_eq!(c, Some(Some(15_999)), "flag com valor = fixa");
            }
            _ => panic!("esperava três Command::Run"),
        }
    }

    /// O `--world` do debug nas duas posições: antes do subcomando (forma
    /// curta, a que os agentes escrevem naturalmente) e depois (na variante).
    #[test]
    fn test_debug_world_flag_positions() {
        let before = Cli::try_parse_from(["viber", "debug", "--world", "qa-pontes", "probe"])
            .expect("antes");
        let after = Cli::try_parse_from(["viber", "debug", "probe", "--world", "qa-pontes"])
            .expect("depois");
        match (before.command, after.command) {
            (
                Some(Command::Debug {
                    world: w1,
                    command: sub1,
                }),
                Some(Command::Debug {
                    world: w2,
                    command: sub2,
                }),
            ) => {
                assert_eq!(
                    w1.as_deref(),
                    Some(Path::new("qa-pontes")),
                    "antes do subcomando"
                );
                assert_eq!(w2.as_deref(), None, "sem world no parent");
                assert!(matches!(sub1, DebugCommand::Probe { .. }));
                assert!(
                    matches!(sub2, DebugCommand::Probe { world: Some(_), .. }),
                    "depois do subcomando"
                );
            }
            _ => panic!("esperava dois Command::Debug"),
        }
    }
}
