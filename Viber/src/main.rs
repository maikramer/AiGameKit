//! Viber CLI — runs and validates AiGameKit declarative world XML.

use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use clap::{CommandFactory, Parser, Subcommand};
use serde_json::Value;

use viber::app::{load_world, world_asset_dirs, world_base_dir};
use viber::bridge::{self, client::BridgeClient};
use viber::recipes::ParsedWorld;
use viber::{audit, prune, recipes, scaffold};

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
    /// Open a world in the ViberEditor (GUI editor). No path = the last
    /// world opened by the editor (persisted in ~/.cache/viber)
    Editor {
        /// Path to the world XML (default: the editor's last-opened world)
        path: Option<PathBuf>,
        /// Extra args passed through to the editor binary (--tab, --mode…)
        extra: Vec<String>,
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
        /// Delega com `--features dlss` (binário de jogo com DLSS; requer o
        /// SDK da NVIDIA em build — `DLSS_SDK`, ver src/dlss.rs)
        #[arg(long)]
        dlss: bool,
        /// Modo de câmara: `free` troca a câmara de jogo por uma câmara de
        /// EDITOR (orbit/pan/zoom via bridge `viber.camera.*`) e desliga o
        /// input do jogador — a navegação de mundos do ViberEditor.
        #[arg(long, value_enum)]
        camera: Option<CameraMode>,
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
        /// Print a single stable JSON object instead of text (machine report for agents/CI)
        #[arg(long)]
        json: bool,
    },
    /// Convert a PROCEDURAL world into a static one IN PLACE (the runtime only loads static
    /// worlds): the world XML and its includes are rewritten at the same paths with
    /// `static="true"` (spawners as explicit instances, settled poses, resolved features);
    /// binary data goes to <stem>.data/ (terrain.ahgt + .paint, veg/*.vinst, bake.json).
    /// Includes that are already static are never rewritten. Refuses a static world
    Bake {
        /// Path to the world XML file
        path: PathBuf,
        /// Data directory (default: <world_dir>/<stem>.data)
        #[arg(short, long)]
        out: Option<PathBuf>,
        /// After baking, verify the static world: parse it clean and
        /// recompute the kept vegetation groups against bake.json
        #[arg(long)]
        verify: bool,
    },
    /// Edit a world's XML documents (agents): ls/get/find, set/insert/rm/mv/dup, apply a JSON
    /// batch of ops. JSON output; edits are saved to the exact file each node lives in
    Scene {
        /// World XML (default: world.xml or worlds/*.xml in the current directory)
        #[arg(long, global = true)]
        world: Option<PathBuf>,
        /// Edit the RUNNING engine (bridge `viber.scene.*`): history, live sync, save
        #[arg(long, global = true)]
        live: bool,
        /// Bridge port for --live (default: the engine serving --world)
        #[arg(long, global = true)]
        port: Option<u16>,
        #[command(subcommand)]
        command: viber::scene::cli::SceneCommand,
    },
    /// Terrain editing (agents): brushes on the pre-generated heightmap as undoable scene ops
    /// (the editor's terrain tool uses the same op), and height sampling
    Terrain {
        #[arg(long, global = true)]
        world: Option<PathBuf>,
        #[arg(long, global = true)]
        live: bool,
        #[arg(long, global = true)]
        port: Option<u16>,
        #[command(subcommand)]
        command: viber::scene::terrain::TerrainCommand,
    },
    /// Generator tools (agents and the editor): `list` the `<Scatter>` recipes, `scatter`
    /// regenerates one's output group on the current terrain as one undoable edit
    Tool {
        /// World XML (default: world.xml or worlds/*.xml in the cwd)
        #[arg(long, global = true)]
        world: Option<PathBuf>,
        /// Run in the RUNNING engine (bridge `viber.tool.*`): live sync + undo there
        #[arg(long, global = true)]
        live: bool,
        #[arg(long, global = true)]
        port: Option<u16>,
        #[command(subcommand)]
        command: viber::scene::tools::ToolCommand,
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

/// Modos de câmara do `viber run --camera <MODO>`.
#[derive(clap::ValueEnum, Clone, Copy, Debug)]
enum CameraMode {
    /// Câmara livre de editor (orbit/pan/zoom via `viber.camera.*`).
    Free,
}

#[derive(Subcommand)]
enum DebugCommand {
    /// Lista as engines vivas (portas, mundos, pids) via engine.json das
    /// sessões — o agente vê o QUEM antes de apontar `--world`/`--port`.
    Engines,
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
    /// Captura N frames seguidos e compõe-nos numa ÚNICA folha (grid √N,
    /// row-major, índice carimbado em cada célula, células com o formato do
    /// frame — 4096 no lado comprido) — para ler movimento/flicker sem N
    /// ficheiros. Protocolo de QA temporal: flicker não se vê num frame
    /// isolado.
    Burst {
        #[arg(short, long, default_value = "burst.png")]
        output: PathBuf,
        /// Nº de frames: 4 (2×2), 9 (3×3) ou 16 (4×4)
        #[arg(long, default_value_t = viber::bridge::burst::DEFAULT_FRAMES)]
        frames: u32,
        /// Frames renderizados a saltar entre capturas — 0 = consecutivos;
        /// cada +1 estica o intervalo de tempo coberto (a folha cobre
        /// frames × (skip+1) frames de render).
        #[arg(long, default_value_t = 0)]
        skip: u32,
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long, default_value_t = 60_000)]
        timeout_ms: u64,
        /// Veredicto numérico de flicker: luma média/desvio POR FRAME + a
        /// oscilação máxima entre frames consecutivos (não abre o PNG).
        #[arg(long)]
        stats: bool,
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
        /// Nível mínimo (error|warn|info|debug|trace) — filtro client-side.
        #[arg(long)]
        level: Option<String>,
        /// Substring a casar na mensagem/target — filtro client-side.
        #[arg(long)]
        grep: Option<String>,
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
    /// Schemas dos tipos REFLETIDOS da engine (`registry.schema` do BRP):
    /// campos e tipos de cada componente/struct — o que um agente precisa
    /// para `world.mutate_components` sem adivinhar. `--grep` filtra por
    /// nome do tipo OU campo (o dump cru são MBs).
    Schema {
        /// Substring a casar no nome do tipo ou num nome de campo
        #[arg(long)]
        grep: Option<String>,
        /// Filtra por crate (ex.: `viber`); repetível
        #[arg(long = "crate")]
        crates: Vec<String>,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Todos os métodos BRP disponíveis (`rpc.discover`, OpenRPC): os builtin
    /// do bevy_remote + os `viber.*` — a auto-descoberta completa.
    Methods {
        #[arg(long)]
        grep: Option<String>,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// A API explica-se: assinaturas + descrições de `viber.debug.*` e
    /// enumeração viva de `viber.*`/`viber.ui.*`/`viber.profiler`.
    Api {
        #[arg(long)]
        grep: Option<String>,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    /// Eventos de jogo estruturados do ring do bridge (dano/morte/quest/
    /// toast/ui/travel/levelup). `--since N` devolve só os posteriores a N
    /// (cursor do agente; o último seq do lote é o próximo cursor).
    Events {
        #[arg(long)]
        since: Option<u64>,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    /// QA determinístico: pausa e avança EXATAMENTE N frames à speed 1.
    /// `viber debug step 5` + `burst` = leitura frame a frame sem conjeturas.
    Step {
        frames: u32,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Restaura a speed anterior ao `step` (despausa).
    Play {
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Amostra uma expressão Luau a N Hz durante T segundos — trajetórias,
    /// HP, qualquer leitura do snapshot, sem loop à mão. `--csv` para plot.
    Watch {
        /// Expressão a avaliar por amostra (ex.: "viber.debug.player().x")
        #[arg(short, long)]
        lua: String,
        #[arg(long, default_value_t = 10.0)]
        hz: f32,
        #[arg(long = "for", visible_alias = "for-secs", default_value_t = 5.0)]
        for_secs: f32,
        #[arg(long)]
        csv: bool,
        /// Imprime a coleção completa em JSON (`[{t, value}]`) em vez de linhas
        #[arg(long)]
        json: bool,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Corre um cenário de QA em Luau com helpers `expect`/`expect_near`/
    /// `fail` (falha → exit 1). Cenários vivem em `<mundo>/qa/*.lua`.
    Test {
        path: PathBuf,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Diff de píxeis entre duas capturas (asserção visual numérica, CI).
    /// `--threshold` em % de píxeis mudados (exit 1 acima).
    Diff {
        /// Captura A (ou a ATUAL, quando `--baseline` está presente)
        a: PathBuf,
        /// Captura B (opcional com `--baseline`)
        b: Option<PathBuf>,
        /// ROI x,y,w,h (píxeis) para restringir a comparação
        #[arg(long)]
        roi: Option<String>,
        /// % de píxeis mudados que tolera (default 0 = qualquer diff falha)
        #[arg(long)]
        threshold: Option<f64>,
        /// Diretório de golden images: compara `a` com `<dir>/<stem>.png`;
        /// ausente → semeia o golden (primeiro run de CI)
        #[arg(long)]
        baseline: Option<PathBuf>,
        /// Com `--baseline`: regrava o golden com `a` (aceita a mudança)
        #[arg(long)]
        update: bool,
        #[arg(long)]
        json: bool,
    },
    /// Cast de raio contra a FÍSICA (colliders Rapier) — devolve a primeira
    /// entidade atingida com ponto/normal. Aceita valores negativos em
    /// posição/direção (`raycast 0 30 0 0 -1 0`).
    #[command(allow_negative_numbers = true)]
    Raycast {
        x: f32,
        y: f32,
        z: f32,
        dx: f32,
        dy: f32,
        dz: f32,
        /// Alcance do raio em metros (default 100)
        #[arg(long)]
        max_toi: Option<f64>,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        port: Option<u16>,
        #[arg(long)]
        world: Option<PathBuf>,
    },
    /// Hash do CONTEÚDO do mundo vivo (FNV-1a por entidade, independente de
    /// ordem/ids): dois boots da mesma seed do mesmo binário → o MESMO hash.
    /// A/B de determinismo "mesma seed, mesmo mundo" num só número.
    Hash {
        #[arg(long)]
        port: Option<u16>,
        /// Mundo que identifica a engine alvo (caminho, nome de ficheiro ou
        /// stem) — resolve a porta pelo engine.json da sessão desse mundo.
        #[arg(long)]
        world: Option<PathBuf>,
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

/// O ficheiro com o caminho do último mundo aberto pelo editor.
fn editor_last_world_file() -> Option<std::path::PathBuf> {
    let home = std::env::var_os("HOME").map(std::path::PathBuf::from)?;
    Some(
        home.join(".cache")
            .join("viber")
            .join("editor-last-world.txt"),
    )
}

/// `viber editor [path] [extra…]` — abre o ViberEditor (GUI). Sem `path`,
/// abre o ÚLTIMO mundo aberto pelo editor (persistido em
/// `~/.cache/viber/editor-last-world.txt`). Procura o binário do editor
/// nesta ordem: PATH → checkout vizinho (binário compilado) → checkout
/// vizinho via `cargo run --release`.
fn editor(path: Option<PathBuf>, extra: Vec<String>) -> anyhow::Result<()> {
    let target = match path {
        Some(path) => path,
        None => {
            let stored = editor_last_world_file()
                .and_then(|file| std::fs::read_to_string(file).ok())
                .map(|text| text.trim().to_owned())
                .filter(|text| !text.is_empty());
            match stored {
                Some(stored) => PathBuf::from(stored),
                None => anyhow::bail!(
                    "viber editor: sem caminho e sem último mundo gravado — passa o world.xml (ex.: `viber editor exemplos/simple-rpg/world.xml`) ou abre o editor uma vez com o caminho"
                ),
            }
        }
    };
    if !target.is_file() {
        anyhow::bail!("viber editor: {} não existe", target.display());
    }
    let mut args: Vec<String> = vec![target.display().to_string()];
    args.extend(extra);

    // 1. binário no PATH
    let editor_on_path = std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|dir| dir.join("vibereditor"))
            .find(|candidate| candidate.is_file())
    });
    if let Some(editor) = editor_on_path {
        println!("viber editor: {} {:?}", editor.display(), args);
        std::process::Command::new(editor)
            .args(&args)
            .spawn()
            .map_err(|e| anyhow::anyhow!("viber editor: {e}"))?;
        return Ok(());
    }
    // 2. checkout vizinho com binário compilado
    let checkout = viber_checkout_root(&std::env::current_exe()?);
    if let Some(checkout) = checkout {
        let built = checkout.parent().map(|parent| {
            parent
                .join("ViberEditor")
                .join("target")
                .join("release")
                .join("vibereditor")
        });
        if let Some(built) = built.filter(|built| built.is_file()) {
            println!("viber editor: {} {:?}", built.display(), args);
            std::process::Command::new(built)
                .args(&args)
                .spawn()
                .map_err(|e| anyhow::anyhow!("viber editor: {e}"))?;
            return Ok(());
        }
        // 3. checkout via cargo (build release, pode demorar na 1.ª vez)
        let manifest = checkout
            .parent()
            .map(|parent| parent.join("ViberEditor").join("Cargo.toml"));
        if let Some(manifest) = manifest.filter(|manifest| manifest.is_file()) {
            println!(
                "viber editor: compilando o editor (cargo run --release — só demora na 1.ª vez)…"
            );
            let status = std::process::Command::new("cargo")
                .arg("run")
                .arg("--release")
                .arg("--manifest-path")
                .arg(manifest)
                .args(&args)
                .status()
                .map_err(|e| anyhow::anyhow!("viber editor: cargo: {e}"))?;
            if !status.success() {
                anyhow::bail!("viber editor: o build do editor falhou");
            }
            return Ok(());
        }
    }
    anyhow::bail!(
        "viber editor: editor não encontrado — instala com `./install.sh vibereditor`, compila com `cargo build --release -p vibereditor` (em ViberEditor/) ou põe o binário no PATH"
    )
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
fn delegate_run_to_cargo(
    world: &Path,
    debug: bool,
    bridge: Option<u16>,
    dlss: bool,
    camera_free: bool,
) -> Result<Option<i32>> {
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
    // `--dlss` é EXPLÍCITO de propósito: a feature arrasta o SDK da NVIDIA
    // para o build (DLSS_SDK/VULKAN_SDK, src/dlss.rs) e agentes paralelos
    // não podem apanhar builds surpresa na árvore partilhada.
    if dlss {
        command.arg("--features").arg("dlss");
    }
    command.arg("--").arg("run").arg(&world);
    if let Some(port) = bridge {
        command.arg("--bridge").arg(port.to_string());
    }
    if camera_free {
        command.arg("--camera").arg("free");
    }
    command.arg("--no-cargo");
    command.env(CARGO_DELEGATE_GUARD, "1");
    eprintln!(
        "viber: Viber checkout detected at {} — delegating to `cargo run{}{}`",
        root.display(),
        if debug { "" } else { " --release" },
        if dlss { " --features dlss" } else { "" }
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
///
/// Com `--no-cargo` o perfil ativo é o do PRÓPRIO executável (`target/debug/
/// viber run --no-cargo` corre o debug mesmo sem `--debug`) — senão o
/// arranque limpava o perfil em uso e o próximo build recompilava tudo.
fn run_target_housekeeping(mut active_debug: bool, no_cargo: bool) {
    // O filho delegado (`cargo run -- run … --no-cargo`) não repete: o pai
    // limpou antes de lançar o cargo.
    if std::env::var_os(CARGO_DELEGATE_GUARD).is_some() {
        return;
    }
    let Ok(cwd) = std::env::current_dir() else {
        return;
    };
    let Some(root) = viber_checkout_root(&cwd) else {
        return;
    };
    if no_cargo {
        let exe = std::env::current_exe().and_then(|p| p.canonicalize());
        let debug_dir = root.join("target").join("debug").canonicalize();
        if let (Ok(exe), Ok(debug_dir)) = (exe, debug_dir) {
            active_debug = exe.starts_with(&debug_dir);
        }
    }
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

/// Sentinela do modo `--json`: o objeto com `"error"` JÁ foi impresso em
/// stdout — o dispatch só tem de devolver exit non-zero (sem texto anyhow
/// solto a poluir a saída que o agente está a parsear).
#[derive(Debug)]
struct JsonReported;
impl std::fmt::Display for JsonReported {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("relatório JSON já impresso")
    }
}
impl std::error::Error for JsonReported {}

/// `viber analyze --json` — o mesmo relatório do analyze em TEXTO num único
/// objeto estável em stdout (agentes/CI parseiam JSON, não texto). Exit
/// codes idênticos ao modo texto: erro de parse/IO ou `--strict` violado ⇒
/// non-zero, com a causa no campo `error`. Formato (contrato estável):
/// `{world, static, entities{…}, ambient_light, terrain{…}, ui{…},
/// spawn_groups{…}, prototypes{…}, skipped_tags{…}, assets{…}, infos[],
/// warnings[], unknown_prototypes[], error?}`.
fn analyze_json(path: &Path, strict: bool) -> Result<()> {
    fn reported(object: serde_json::Value) -> anyhow::Error {
        println!(
            "{}",
            serde_json::to_string_pretty(&object).expect("serialize")
        );
        anyhow::Error::new(JsonReported)
    }
    let path = std::path::absolute(path)?;
    let world = match load_world(&path) {
        Ok(world) => world,
        Err(e) => {
            return Err(reported(serde_json::json!({
                "world": path.display().to_string(),
                "error": format!("{e:#}"),
            })));
        }
    };
    let summary = recipes::summarize(&world);
    let world_dir = world_base_dir(&path).unwrap_or_else(|| PathBuf::from("."));
    let audit_report = match viber::config::load(&world_dir) {
        Ok(config) => {
            let (world_dir, asset_roots) = world_asset_dirs(&path, &config);
            audit::audit(&world, &world_dir, &asset_roots, &config)
        }
        Err(e) => {
            return Err(reported(serde_json::json!({
                "world": path.display().to_string(),
                "error": format!("config: {e:#}"),
            })));
        }
    };
    let mut object = serde_json::json!({
        "world": path.display().to_string(),
        "static": world.static_world,
        "entities": {
            "total": summary.entities(),
            "groups": summary.groups,
            "compositions": summary.compositions,
            "composition_parts": summary.composition_parts,
            "primitives": summary.primitives,
            "point_lights": summary.point_lights,
            "directional_lights": summary.directional_lights,
            "cameras": summary.cameras,
            "gltf_scenes": summary.gltf_scenes,
            "players": summary.players,
        },
        "ambient_light": if summary.has_ambient { "world-defined" } else { "bevy default" },
        "terrain": {
            "heightfields": summary.terrain,
            "ground_features": summary.ground_features(),
            "pads": summary.terrain_pads,
            "lakes": summary.lakes,
            "rivers": summary.rivers,
            "cuts": summary.cuts,
            "plateaus": summary.plateaus,
            "cliffs": summary.cliffs,
            "caves": summary.caves,
            "arches": summary.arches,
            "bridges": summary.bridges,
            "rock_fields": summary.rock_fields,
            "roads": summary.roads,
            "road_networks": summary.road_networks,
            "ground_decals": summary.ground_decals,
        },
        "ui": {
            "roots": summary.ui_roots,
            "elements": summary.ui_elements,
            "stylesheets": summary.ui_stylesheets,
        },
        "spawn_groups": {
            "static": summary.static_spawners,
            "dynamic": summary.dynamic_spawners,
            "vegetation": summary.vegetation,
            "exclusions": summary.spawn_exclusions,
        },
        "prototypes": {
            "definitions": world.prototypes.len(),
            "instances": world.prototype_instances.values().sum::<usize>(),
            "used": world.prototype_instances,
        },
        "skipped_tags": world.skipped_tags,
        "assets": {
            "references": audit_report.references,
            "issues": audit_report
                .issues
                .iter()
                .map(|issue| {
                    serde_json::json!({
                        "severity": match issue.severity {
                            audit::Severity::Missing => "missing",
                            audit::Severity::Warning => "warning",
                            audit::Severity::Info => "info",
                        },
                        "message": issue.message,
                    })
                })
                .collect::<Vec<_>>(),
            "colliderless": audit_report.colliderless,
        },
        "infos": world.infos,
        "warnings": world.warnings,
        "unknown_prototypes": world.unknown_prototypes,
    });
    if strict && !world.skipped_tags.is_empty() {
        object["error"] = serde_json::json!(format!(
            "strict mode: {} not-implemented tags present ({} elements)",
            world.skipped_tags.len(),
            total_skipped(&world)
        ));
        return Err(reported(object));
    }
    if strict && !world.unknown_prototypes.is_empty() {
        object["error"] = serde_json::json!(format!(
            "strict mode: {} <Use> com protótipo desconhecido: {}",
            world.unknown_prototypes.len(),
            world.unknown_prototypes.join(", ")
        ));
        return Err(reported(object));
    }
    if strict && audit_report.missing_count() > 0 {
        object["error"] = serde_json::json!(format!(
            "strict mode: {} asset(s) ausente(s)",
            audit_report.missing_count()
        ));
        return Err(reported(object));
    }
    println!("{}", serde_json::to_string_pretty(&object)?);
    Ok(())
}

fn analyze(path: &Path, strict: bool, json: bool) -> Result<()> {
    if json {
        return analyze_json(path, strict);
    }
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
            "  terrain: heightfield {}, ground features {} (pads {}, lakes {}, rivers {}, cuts {}, plateaus {}, cliffs {}, caves {}, arches {}, bridges {}, rock fields {}, roads {} + networks {}, decals {})",
            summary.terrain,
            summary.ground_features(),
            summary.terrain_pads,
            summary.lakes,
            summary.rivers,
            summary.cuts,
            summary.plateaus,
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
    // Findings informativos (nível distinto dos warnings): dicas de workflow —
    // não-fatais, `--strict` NÃO falha por causa deles.
    for info in &world.infos {
        println!("  info: {info}");
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

fn run(path: &Path, bridge_port: Option<u16>, camera_free: bool) -> Result<()> {
    viber::app::build_app(
        path,
        viber::app::AppOptions {
            mode: viber::app::RunMode::Game,
            bridge_port,
            camera_free,
            window_title: None,
        },
    )?
    .run();
    Ok(())
}

fn total_skipped(world: &ParsedWorld) -> usize {
    world.skipped_tags.values().sum()
}

fn dispatch(command: Command) -> Result<std::process::ExitCode> {
    match command {
        Command::Create { name } => create(&name).map(|_| std::process::ExitCode::SUCCESS),
        Command::Terrain {
            world,
            live,
            port,
            command,
        } => {
            let out = if live {
                let (method, params) = viber::scene::terrain::live_request(&command)?;
                let port = bridge::client::resolve_port(port, world.as_deref())?;
                BridgeClient::localhost(port).call(method, params)?
            } else {
                viber::scene::terrain::run_cli(&resolve_world_path(world)?, command)?
            };
            println!("{}", serde_json::to_string_pretty(&out)?);
            Ok(std::process::ExitCode::SUCCESS)
        }
        Command::Tool {
            world,
            live,
            port,
            command,
        } => {
            let out = if live {
                let (method, params) = viber::scene::tools::live_request(&command)?;
                let port = bridge::client::resolve_port(port, world.as_deref())?;
                BridgeClient::localhost(port).call(method, params)?
            } else {
                viber::scene::tools::run_cli(&resolve_world_path(world)?, command)?
            };
            println!("{}", serde_json::to_string_pretty(&out)?);
            Ok(std::process::ExitCode::SUCCESS)
        }
        Command::Scene {
            world,
            live,
            port,
            command,
        } => {
            let out = if live {
                let (method, params) = viber::scene::cli::live_request(command)?;
                let port = bridge::client::resolve_port(port, world.as_deref())?;
                BridgeClient::localhost(port).call(method, params)?
            } else {
                viber::scene::cli::run(&resolve_world_path(world)?, command)?
            };
            println!("{}", serde_json::to_string_pretty(&out)?);
            Ok(std::process::ExitCode::SUCCESS)
        }
        Command::Editor { path, extra } => {
            editor(path, extra).map(|_| std::process::ExitCode::SUCCESS)
        }
        Command::Run {
            path,
            bridge,
            debug,
            release: _,
            no_cargo,
            dlss,
            camera,
        } => {
            run_target_housekeeping(debug, no_cargo);
            let world = resolve_world_path(path)?;
            // `--bridge` sem valor escolhe porta livre ANTES da delegação —
            // a porta impressa no arranque tem de ser a que a engine usa de
            // facto (e a que o agente exporta/usa nos comandos debug).
            let bridge = match bridge {
                None => None,
                Some(Some(port)) => Some(port),
                Some(None) => Some(free_bridge_port(bridge::DEFAULT_BRIDGE_PORT)?),
            };
            let camera_free = matches!(camera, Some(CameraMode::Free));
            if !no_cargo
                && let Some(code) = delegate_run_to_cargo(&world, debug, bridge, dlss, camera_free)?
            {
                return Ok(std::process::ExitCode::from(code as u8));
            }
            run(&world, bridge, camera_free)
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
        Command::Analyze { path, strict, json } => {
            let world = match resolve_world_path(path) {
                Ok(world) => world,
                Err(e) if json => {
                    println!("{}", serde_json::json!({ "error": format!("{e:#}") }));
                    return Ok(std::process::ExitCode::FAILURE);
                }
                Err(e) => return Err(e),
            };
            match analyze(&world, strict, json) {
                Ok(()) => Ok(std::process::ExitCode::SUCCESS),
                // O objeto com `"error"` já foi impresso — só o exit code.
                Err(e) if json && e.is::<JsonReported>() => Ok(std::process::ExitCode::FAILURE),
                Err(e) if json => {
                    println!("{}", serde_json::json!({ "error": format!("{e:#}") }));
                    Ok(std::process::ExitCode::FAILURE)
                }
                Err(e) => Err(e).with_context(|| format!("analyzing {}", world.display())),
            }
        }
        Command::Bake { path, out, verify } => {
            let world = std::path::absolute(&path)?;
            viber::bake::check_bake_target(&world)?;
            let out_dir = out.unwrap_or_else(|| viber::bake::data_dir(&world));
            let report = viber::bake::run_bake(&world, &out_dir)
                .with_context(|| format!("baking {}", world.display()))?;
            println!(
                "viber: bake: {} groups / {} instances — pads {}, lakes {}, rivers {}, roads {}",
                report.groups,
                report.instances,
                report.pads,
                report.lakes,
                report.rivers,
                report.roads
            );
            for warning in &report.warnings {
                eprintln!("viber: bake: aviso: {warning}");
            }
            for data in [&report.ahgt_path, &report.bake_json_path] {
                if !data.as_os_str().is_empty() {
                    println!("viber: bake: {}", data.display());
                }
            }
            if let Some(static_path) = &report.static_world_path {
                println!(
                    "viber: bake: {} (run with `viber run`)",
                    static_path.display()
                );
            }
            if verify {
                let static_path = report
                    .static_world_path
                    .clone()
                    .context("--verify precisa do mundo estático escrito")?;
                let checked = viber::bake::verify_static(&static_path, &report.bake_json_path)?;
                println!(
                    "viber: bake: verify OK — {} vegetation instances match bake.json",
                    checked
                );
            }
            Ok(std::process::ExitCode::SUCCESS)
        }
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

/// Identidade de uma engine antes de lhe mandar um sinal: o argv[0] tem de
/// ter basename `viber` **e** o mundo registado no `engine.json` tem de
/// aparecer nos argumentos.
///
/// As duas condições juntas são mais apertadas do que o basename sozinho (um
/// `viber debug logs` de outro agente não leva o caminho do mundo) e mais
/// largas do que a igualdade com o `current_exe()`, que recusava descer uma
/// engine subida pelo binário instalado a partir de um checkout. Pura para
/// teste.
fn looks_like_engine(args: &[String], world: &Path) -> bool {
    let Some(argv0) = args.first() else {
        return false;
    };
    if Path::new(argv0).file_name() != Some(std::ffi::OsStr::new("viber")) {
        return false;
    }
    let world = std::fs::canonicalize(world).unwrap_or_else(|_| world.to_path_buf());
    args.iter().skip(1).any(|arg| {
        let arg_path = std::fs::canonicalize(arg).unwrap_or_else(|_| PathBuf::from(arg));
        arg_path == world
    })
}

/// Desce a engine partilhada (SIGTERM via `kill`; o lease tem de estar livre
/// ou ser nosso).
fn session_down(world: Option<&Path>) -> Result<()> {
    let (paths, _) = session_paths(world.map(PathBuf::from).as_ref())?;
    if let Some((owner, remaining)) = paths.busy()
        && remaining > std::time::Duration::ZERO
    {
        bail!(
            "sessão ocupada por '{owner}' (~{} s) — `release` do dono ou espere o TTL",
            remaining.as_secs()
        );
    }
    let Some(engine) = paths.engine_info() else {
        println!("viber session: nenhuma engine registada — nada a fazer");
        return Ok(());
    };
    // Identidade antes do sinal: um PID reutilizado pode ser um processo
    // inocente (`tail -f` do log da engine, um editor com o caminho aberto)
    // — procurar "viber" como SUBSTRING do cmdline inteiro batia neles.
    //
    // A regra é "isto É uma engine viber deste mundo", NÃO "isto saiu do MEU
    // binário": a engine partilhada tanto pode ter sido subida pelo `viber`
    // instalado (`~/.local/bin`) como por um `target/release/viber` de um
    // checkout, e exigir igualdade com o `current_exe()` deixava a engine do
    // outro binário impossível de descer (`session down` recusava, a GPU
    // ficava ocupada e ninguém conseguia subir a sua — 2026-09-12). Ver
    // [`looks_like_engine`]. Se o /proc nem existe, o processo já morreu —
    // mantemos o fluxo antigo de limpar os metadados.
    let cmdline = Path::new("/proc")
        .join(engine.pid.to_string())
        .join("cmdline");
    if let Ok(raw) = std::fs::read(&cmdline) {
        let args: Vec<String> = raw
            .split(|byte| *byte == 0)
            .filter(|part| !part.is_empty())
            .map(|part| String::from_utf8_lossy(part).into_owned())
            .collect();
        if !looks_like_engine(&args, Path::new(&engine.world)) {
            bail!(
                "pid {} não parece uma engine viber deste mundo (argv `{}`; PID reutilizado?) — engine.json mantido; limpe à mão se confirmar",
                engine.pid,
                args.join(" ")
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
    print_gpu_spans(prof);
}

/// Passes do render graph, quando a engine corre com `VIBER_PROF_GPU=1`.
/// Sem o gate o array vem vazio e a secção não aparece — é o resto do frame
/// que os sistemas do `timed` não cobrem.
fn print_gpu_spans(prof: &serde_json::Value) {
    let Some(spans) = prof.get("gpu").and_then(serde_json::Value::as_array) else {
        return;
    };
    if spans.is_empty() {
        return;
    }
    println!("render (VIBER_PROF_GPU) — top passes:");
    for span in spans.iter().take(12) {
        let name = span
            .get("name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?");
        let fmt = |key: &str| {
            span.get(key)
                .and_then(serde_json::Value::as_f64)
                .map(|v| format!("{v:.2} ms"))
                .unwrap_or_else(|| "—".into())
        };
        println!(
            "  {name:<40} gpu {:>9}   cpu {:>9}",
            fmt("gpu_ms"),
            fmt("cpu_ms")
        );
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
        DebugCommand::Engines => {
            let engines = bridge::client::list_live_engines();
            if engines.is_empty() {
                println!("(nenhuma engine viva — `viber session up` ou `viber run --bridge`)");
            } else {
                for engine in engines {
                    println!(":{}  {}  (pid {})", engine.port, engine.world, engine.pid);
                }
            }
        }
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
        DebugCommand::Burst {
            output,
            frames,
            skip,
            port,
            world,
            timeout_ms,
            stats,
        } => {
            if !bridge::burst::ALLOWED_FRAMES.contains(&frames) {
                bail!("frames inválido: {frames} — aceites 4 (2×2), 9 (3×3) ou 16 (4×4)");
            }
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let (bytes, source, sheet_w, sheet_h, final_status) =
                client.burst(frames, skip, timeout_ms)?;
            std::fs::write(&output, &bytes)
                .with_context(|| format!("a escrever {}", output.display()))?;
            let grid = bridge::burst::grid_for(frames).expect("validado acima");
            println!(
                "✓ burst → {} (fonte: {source})\n  {} frames em {grid}×{grid}, skip {skip} — cobre ~{} frames de render na folha {}×{}",
                output.display(),
                frames,
                frames as u64 * (skip as u64 + 1),
                if sheet_w > 0 {
                    sheet_w
                } else {
                    bridge::burst::SHEET_SIZE
                },
                if sheet_h > 0 {
                    sheet_h
                } else {
                    bridge::burst::SHEET_SIZE
                }
            );
            if stats {
                let Some(list) = final_status.get("frame_stats").and_then(Value::as_array) else {
                    eprintln!("burst: sem frame_stats (engine antiga?)");
                    return Ok(());
                };
                println!("  frame_stats:");
                for frame in list {
                    println!(
                        "    #{} mean={:.2} std={:.2}",
                        frame.get("index").and_then(Value::as_u64).unwrap_or(0),
                        frame.get("mean").and_then(Value::as_f64).unwrap_or(0.0),
                        frame.get("std").and_then(Value::as_f64).unwrap_or(0.0),
                    );
                }
                if let Some(flicker) = final_status.get("flicker") {
                    println!(
                        "  flicker: max_mean_swing={:.3} max_consecutive_delta={:.3} (mundo parado: ≈0; oscilar = flicker)",
                        flicker
                            .get("max_mean_swing")
                            .and_then(Value::as_f64)
                            .unwrap_or(0.0),
                        flicker
                            .get("max_consecutive_delta")
                            .and_then(Value::as_f64)
                            .unwrap_or(0.0),
                    );
                }
            }
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
            level,
            grep,
            json,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let mut logs = client.logs(limit)?;
            // Filtros client-side (o ring da engine fica inteiro).
            if let Some(level) = level {
                let min = match level.to_ascii_lowercase().as_str() {
                    "error" => 4,
                    "warn" | "warning" => 3,
                    "info" => 2,
                    "debug" => 1,
                    _ => 0,
                };
                let rank = |value: &Value| match value.as_str() {
                    Some("ERROR") => 4,
                    Some("WARN") => 3,
                    Some("INFO") => 2,
                    Some("DEBUG") => 1,
                    _ => 0,
                };
                if let Some(entries) = logs.as_array_mut() {
                    entries.retain(|entry| rank(entry.get("level").unwrap_or(&Value::Null)) >= min);
                }
            }
            if let Some(needle) = grep {
                let needle = needle.to_ascii_lowercase();
                if let Some(entries) = logs.as_array_mut() {
                    entries.retain(|entry| {
                        let message = entry
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_ascii_lowercase();
                        let target = entry
                            .get("target")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_ascii_lowercase();
                        message.contains(&needle) || target.contains(&needle)
                    });
                }
            }
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
                if let Some(applied) = response.get("applied").and_then(Value::as_u64)
                    && applied > 0
                {
                    eprintln!("({applied} operações aplicadas)");
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
        DebugCommand::Schema {
            grep,
            crates,
            json,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let params = if crates.is_empty() {
                serde_json::json!({})
            } else {
                eprintln!(
                    "viber: nota — o filtro de crate não exclui tipos SEM crate no path \
(primitivos/tuplas) e os componentes do Viber não são refletidos (para esses, usa \
`viber.debug.*`); sem `--grep` a lista pode surpreender"
                );
                serde_json::json!({ "with_crates": crates })
            };
            let schema = client.call("registry.schema", params)?;
            let needle = grep.unwrap_or_default().to_ascii_lowercase();
            let Some(types) = schema.as_object() else {
                println!("{schema:#}");
                return Ok(());
            };
            // Sem grep e sem --json: resumo (o dump inteiro são MBs).
            if needle.is_empty() && !json {
                let mut names: Vec<&str> = types.keys().map(String::as_str).collect();
                names.sort();
                eprintln!(
                    "viber: {} tipos refletidos — usa --grep <tipo|campo> para os campos (ou --json para o dump cru)",
                    names.len()
                );
                for name in names {
                    println!("{name}");
                }
                return Ok(());
            }
            // Os FIELDS de uma struct vivem em `properties` (JSON-Schema);
            // cada um é `{"type": "<primitivo>"}` ou
            // `{"type": {"$ref": "#/$defs/<TypePath>"}}`.
            let render_type = |info: &Value| -> String {
                let ty = info.get("type").unwrap_or(&Value::Null);
                if let Some(text) = ty.as_str() {
                    return text.to_string();
                }
                if let Some(reference) = ty.get("$ref").and_then(Value::as_str) {
                    return reference
                        .rsplit('/')
                        .next()
                        .unwrap_or(reference)
                        .to_string();
                }
                if let Some(variants) = ty.as_array() {
                    return variants
                        .iter()
                        .map(|v| {
                            v.as_str()
                                .map(str::to_string)
                                .or_else(|| {
                                    v.get("$ref")
                                        .and_then(Value::as_str)
                                        .map(|r| r.rsplit('/').next().unwrap_or(r).to_string())
                                })
                                .unwrap_or_else(|| "?".into())
                        })
                        .collect::<Vec<_>>()
                        .join("|");
                }
                ty.as_str().unwrap_or("?").to_string()
            };
            let mut matched = serde_json::Map::new();
            for (name, entry) in types {
                let properties = entry.pointer("/properties").and_then(|f| f.as_object());
                let field_hit = properties.is_some_and(|properties| {
                    properties
                        .keys()
                        .any(|field| field.to_ascii_lowercase().contains(&needle))
                });
                if needle.is_empty() || name.to_ascii_lowercase().contains(&needle) || field_hit {
                    matched.insert(name.clone(), entry.clone());
                }
            }
            if json {
                println!("{}", Value::Object(matched.clone()));
            } else if matched.is_empty() {
                eprintln!("viber: nenhum tipo casa com '{needle}'");
            } else {
                let mut names: Vec<&String> = matched.keys().collect();
                names.sort();
                for name in names {
                    let entry = &matched[name];
                    let kind = entry.get("kind").and_then(Value::as_str).unwrap_or("");
                    let is_component = entry.pointer("/component_info").is_some();
                    println!("{name}{}", if is_component { "  [component]" } else { "" });
                    let _ = kind;
                    if let Some(properties) =
                        entry.pointer("/properties").and_then(|f| f.as_object())
                    {
                        for (field, info) in properties {
                            println!("    {field}: {}", render_type(info));
                        }
                    }
                }
            }
        }
        DebugCommand::Methods {
            grep,
            json,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let discover = client.call("rpc.discover", serde_json::json!({}))?;
            let needle = grep.unwrap_or_default().to_ascii_lowercase();
            // O documento OpenRPC traz `methods: [{name, summary?}]`.
            let names: Vec<(String, String)> = discover
                .get("methods")
                .and_then(Value::as_array)
                .map(|methods| {
                    methods
                        .iter()
                        .filter_map(|method| {
                            let name = method.get("name")?.as_str()?.to_string();
                            let summary = method
                                .get("summary")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_string();
                            Some((name, summary))
                        })
                        .collect()
                })
                .unwrap_or_default();
            if json {
                let filtered: Vec<Value> = names
                    .iter()
                    .filter(|(name, summary)| {
                        needle.is_empty()
                            || name.to_ascii_lowercase().contains(&needle)
                            || summary.to_ascii_lowercase().contains(&needle)
                    })
                    .map(|(name, summary)| serde_json::json!({ "name": name, "summary": summary }))
                    .collect();
                println!("{}", Value::Array(filtered));
            } else {
                let mut shown = 0;
                for (name, summary) in &names {
                    if !needle.is_empty() && !name.to_ascii_lowercase().contains(&needle) {
                        continue;
                    }
                    shown += 1;
                    if summary.is_empty() {
                        println!("{name}");
                    } else {
                        println!(
                            "{name}
    {summary}"
                        );
                    }
                }
                eprintln!("viber: {shown}/{} métodos", names.len());
            }
        }
        DebugCommand::Api {
            grep,
            port,
            world,
            json,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let needle = grep.unwrap_or_default();
            let code = if needle.is_empty() {
                "return viber.debug.apidoc()".to_string()
            } else {
                format!("return viber.debug.apidoc() -- grep: {needle}")
            };
            let response = client.lua(&code)?;
            if !response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                let error = response
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("erro desconhecido");
                bail!("erro Luau: {error}");
            }
            let mut doc = response.get("result").cloned().unwrap_or(Value::Null);
            if !needle.is_empty() {
                // Filtro client-side sobre o grupo debug (nome/descrição).
                let needle_l = needle.to_ascii_lowercase();
                if let Some(obj) = doc.get_mut("debug").and_then(Value::as_object_mut) {
                    let kept: Vec<(String, Value)> = obj
                        .iter()
                        .filter(|(name, entry)| {
                            name.to_ascii_lowercase().contains(&needle_l)
                                || entry
                                    .get("description")
                                    .and_then(Value::as_str)
                                    .map(|d| d.to_ascii_lowercase().contains(&needle_l))
                                    .unwrap_or(false)
                        })
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();
                    obj.clear();
                    for (k, v) in kept {
                        obj.insert(k, v);
                    }
                }
            }
            if json {
                println!("{doc:#}");
            } else if let Some(debug) = doc.get("debug").and_then(Value::as_object) {
                for (name, entry) in debug {
                    println!(
                        "viber.debug.{}{}
    {}",
                        name,
                        entry.get("signature").and_then(Value::as_str).unwrap_or(""),
                        entry
                            .get("description")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                    );
                }
                for group in ["game", "ui", "profiler"] {
                    if let Some(list) = doc.get(group).and_then(Value::as_object) {
                        let names: Vec<&str> = list.keys().map(String::as_str).collect();
                        println!("viber.{group}: {}", names.join(", "));
                    }
                }
            } else {
                println!("{doc:#}");
            }
        }
        DebugCommand::Events {
            since,
            port,
            world,
            json,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let response = client.lua(&format!(
                "return viber.debug.events({})",
                since.unwrap_or(0)
            ))?;
            if !response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                let error = response
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("erro desconhecido");
                bail!("erro Luau: {error}");
            }
            let events = response.get("result").cloned().unwrap_or(Value::Null);
            if json {
                println!("{events:#}");
            } else {
                match events.as_array() {
                    Some(list) if list.is_empty() => {
                        println!("(sem eventos desde --since {})", since.unwrap_or(0))
                    }
                    Some(list) => {
                        for event in list {
                            println!(
                                "{} [{:^7}] {}",
                                event.get("seq").and_then(Value::as_u64).unwrap_or(0),
                                event.get("kind").and_then(Value::as_str).unwrap_or("?"),
                                serde_json::to_string(event).unwrap_or_default()
                            );
                        }
                        if let Some(last) = list
                            .last()
                            .and_then(|e| e.get("seq"))
                            .and_then(Value::as_u64)
                        {
                            eprintln!("(próximo cursor: --since {last})");
                        }
                    }
                    None => println!("{events:#}"),
                }
            }
        }
        DebugCommand::Step {
            frames,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let response = client.lua(&format!("viber.debug.step({frames}) return true"))?;
            if !response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                let error = response
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("erro desconhecido");
                bail!("erro Luau: {error}");
            }
            println!(
                "✓ {frames} frame(s) avançado(s) — mundo PAUSADO (`viber debug play` retoma; `step 0` só congela)"
            );
        }
        DebugCommand::Play { port, world } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            client.lua("viber.debug.play() return true")?;
            println!("✓ a correr (speed restaurada)");
        }
        DebugCommand::Watch {
            lua: expression,
            hz,
            for_secs,
            csv,
            json,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let (hz, for_secs) = (f64::from(hz), f64::from(for_secs));
            let period = std::time::Duration::from_secs_f64(1.0 / hz.max(0.1));
            let started = std::time::Instant::now();
            let mut samples: Vec<Value> = Vec::new();
            if !json && !csv {
                println!("# t\tvalue");
            }
            while started.elapsed().as_secs_f64() < for_secs.max(0.1) {
                let tick = std::time::Instant::now();
                let response = client.lua(&format!("return {expression}"))?;
                if !response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                    let error = response
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("erro desconhecido");
                    bail!("erro Luau na amostra: {error}");
                }
                let value = response.get("result").cloned().unwrap_or(Value::Null);
                let t = started.elapsed().as_secs_f64();
                if !json {
                    if csv {
                        println!("{t:.3},{value}");
                    } else {
                        println!("{t:.3}	{value}");
                    }
                }
                samples.push(serde_json::json!({ "t": t, "value": value }));
                let elapsed = tick.elapsed();
                if elapsed < period {
                    std::thread::sleep(period - elapsed);
                }
            }
            if json {
                println!("{}", Value::Array(samples));
            }
        }
        DebugCommand::Test {
            path,
            json,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let code = std::fs::read_to_string(&path)
                .with_context(|| format!("a ler {}", path.display()))?;
            // 1) helpers de QA (globals persistem na REPL)
            client.lua(QA_HELPERS)?;
            // 2) o cenário
            let response = client.lua(&code)?;
            let ok = response.get("ok").and_then(Value::as_bool).unwrap_or(false);
            // 3) relatório dos helpers
            let report = client.lua(
                "return { ok = (#__qa.fails == 0), asserts = __qa.asserts, fails = __qa.fails }",
            )?;
            let report = report.get("result").cloned().unwrap_or(Value::Null);
            let failed = report
                .get("fails")
                .and_then(Value::as_array)
                .map(Vec::len)
                .unwrap_or(0);
            if json {
                let mut out = serde_json::Map::new();
                out.insert("file".into(), Value::String(path.display().to_string()));
                out.insert("chunk_ok".into(), Value::Bool(ok));
                out.insert("report".into(), report);
                println!("{}", Value::Object(out));
            } else if ok && failed == 0 {
                let asserts = report.get("asserts").and_then(Value::as_u64).unwrap_or(0);
                println!("✓ {} — {asserts} assert(s) passou(aram)", path.display());
            } else {
                if !ok {
                    let error = response
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("erro desconhecido");
                    eprintln!("✗ erro Luau: {error}");
                }
                for fail in report
                    .get("fails")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or_default()
                {
                    eprintln!("✗ {fail}");
                }
                bail!("cenário falhou ({failed} assert(s))");
            }
        }
        DebugCommand::Diff {
            a,
            b,
            roi,
            threshold,
            baseline,
            update,
            json,
        } => {
            let roi = roi
                .map(|r| bridge::diff::parse_roi(&r))
                .transpose()
                .map_err(|e| anyhow::anyhow!(e))?;
            let mut other: Option<PathBuf> = None;
            // Modo GOLDEN: `a` é a captura atual, o golden é
            // `<dir>/<stem-de-a>.png` (ou o nome do `b`, se dado).
            let result = if let Some(dir) = baseline {
                let name = b
                    .as_ref()
                    .and_then(|path| path.file_stem())
                    .or_else(|| a.file_stem())
                    .and_then(|stem| stem.to_str())
                    .unwrap_or("golden")
                    .to_string();
                if update {
                    let golden = bridge::diff::update_baseline(&dir, &name, &a)
                        .map_err(|e| anyhow::anyhow!(e))?;
                    println!("✓ baseline atualizada: {}", golden.display());
                    return Ok(());
                }
                match bridge::diff::compare_baseline(&dir, &name, &a)
                    .map_err(|e| anyhow::anyhow!(e))?
                {
                    None => {
                        println!(
                            "✓ baseline semeada: {} (novo golden; volta a correr para comparar)",
                            dir.join(format!("{name}.png")).display()
                        );
                        return Ok(());
                    }
                    Some((result, golden)) => {
                        if !json {
                            eprintln!("viber: golden {}", golden.display());
                        }
                        result
                    }
                }
            } else {
                let b = b.ok_or_else(|| {
                    anyhow::anyhow!("uso: viber debug diff a.png b.png (ou --baseline <dir> a.png)")
                })?;
                other = Some(b.clone());
                bridge::diff::diff_files(&a, &b, roi).map_err(|e| anyhow::anyhow!(e))?
            };
            if json {
                let value = serde_json::to_value(&result).expect("serialize");
                println!("{value:#}");
            } else {
                println!(
                    "{} vs {}: mean_delta={:.3} max_delta={} changed={:.4}% p99={}",
                    a.display(),
                    other
                        .as_deref()
                        .map(|path| path.display().to_string())
                        .unwrap_or_else(|| "(golden)".to_string()),
                    result.mean_delta,
                    result.max_delta,
                    result.changed_pct,
                    result.p99
                );
            }
            let limit = threshold.unwrap_or(0.0);
            if result.changed_pct > limit {
                bail!(
                    "diff acima do limiar: {:.4}% > {limit}%",
                    result.changed_pct
                );
            }
        }
        DebugCommand::Raycast {
            x,
            y,
            z,
            dx,
            dy,
            dz,
            max_toi,
            json,
            port,
            world,
        } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let result = client.call(
                bridge::METHOD_RAYCAST,
                serde_json::json!({
                    "x": x, "y": y, "z": z,
                    "dx": dx, "dy": dy, "dz": dz,
                    "max_toi": max_toi,
                }),
            )?;
            if json {
                println!("{result:#}");
            } else if result.get("hit").and_then(Value::as_bool).unwrap_or(false) {
                println!(
                    "✓ hit {} ({}) toi={:.3} point=({:.2},{:.2},{:.2}) normal=({:.2},{:.2},{:.2})",
                    result.get("name").and_then(Value::as_str).unwrap_or("?"),
                    result.get("entity").and_then(Value::as_u64).unwrap_or(0),
                    result.get("toi").and_then(Value::as_f64).unwrap_or(0.0),
                    result
                        .pointer("/point/0")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                    result
                        .pointer("/point/1")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                    result
                        .pointer("/point/2")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                    result
                        .pointer("/normal/0")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                    result
                        .pointer("/normal/1")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                    result
                        .pointer("/normal/2")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                );
            } else {
                println!("(sem hit)");
            }
        }
        DebugCommand::Hash { port, world } => {
            let port = bridge::client::resolve_port(port, merge_world(&world, &parent_world))?;
            let client = BridgeClient::localhost(port);
            let response = client.lua("return viber.debug.world_hash()")?;
            if !response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                let error = response
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("erro desconhecido");
                bail!("erro Luau: {error}");
            }
            match response.get("result") {
                Some(value) => println!("{value}"),
                None => bail!("engine sem viber.debug.world_hash (binário antigo?)"),
            }
        }
    }
    Ok(())
}

/// Helpers de QA injectados na REPL antes de `viber debug test <ficheiro>`.
const QA_HELPERS: &str = r#"
__qa = { asserts = 0, fails = {} }
function expect(cond, msg)
    __qa.asserts += 1
    if not cond then table.insert(__qa.fails, msg or "expect falhou") end
    return cond
end
function expect_near(a, b, tol, msg)
    __qa.asserts += 1
    tol = tol or 0.01
    if math.abs(a - b) > tol then
        table.insert(__qa.fails, (msg or "expect_near falhou")
            .. string.format(" (%s vs %s ±%s)", tostring(a), tostring(b), tostring(tol)))
        return false
    end
    return true
end
function fail(msg)
    __qa.asserts += 1
    table.insert(__qa.fails, msg or "fail")
    return false
end
return true
"#;

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

    /// `session down` tem de descer a engine partilhada mesmo quando ela foi
    /// subida por OUTRO binário viber (instalado vs `target/release`) — a
    /// regra antiga (igualdade com `current_exe`) deixava a GPU ocupada sem
    /// ninguém a poder libertá-la (2026-09-12).
    #[test]
    fn engine_identity_accepts_any_viber_binary_with_the_right_world() {
        let world = std::path::PathBuf::from("/m/world.xml");
        let args = |list: &[&str]| list.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        assert!(looks_like_engine(
            &args(&[
                "/home/u/.local/bin/viber",
                "run",
                "/m/world.xml",
                "--bridge",
                "15702"
            ]),
            &world
        ));
        assert!(looks_like_engine(
            &args(&["/checkout/target/release/viber", "run", "/m/world.xml"]),
            &world
        ));
    }

    /// E tem de RECUSAR um PID reutilizado: outro programa, ou um viber que
    /// não é a engine deste mundo.
    #[test]
    fn engine_identity_rejects_strangers() {
        let world = std::path::PathBuf::from("/m/world.xml");
        let args = |list: &[&str]| list.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        // `tail -f` do log da engine: o caminho aparece, o binário não é viber.
        assert!(!looks_like_engine(
            &args(&["/usr/bin/tail", "-f", "/m/world.xml"]),
            &world
        ));
        // viber, mas de OUTRO mundo.
        assert!(!looks_like_engine(
            &args(&["/home/u/.local/bin/viber", "run", "/m/outro.xml"]),
            &world
        ));
        // viber sem o mundo nos argumentos (ex. `viber debug logs`).
        assert!(!looks_like_engine(
            &args(&["/home/u/.local/bin/viber", "debug", "logs"]),
            &world
        ));
        assert!(!looks_like_engine(&[], &world));
    }
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
