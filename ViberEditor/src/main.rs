use clap::Parser;
use std::path::PathBuf;

mod app;
mod bridge;
mod doc;
mod scene;
mod ui;
mod viewport;

/// Godot-style editor for Viber worlds.
///
/// Loads the world in-process (Viber's `RunMode::Edit`: the scene renders live, the simulation is
/// frozen) and edits its XML document; ▶ Play (F5) runs the game as a separate process.
#[derive(Parser, Debug)]
#[command(version, about)]
pub struct Cli {
    /// World XML file to edit.
    ///
    /// Defaults to Viber's simple-rpg demo next to this crate.
    world: Option<PathBuf>,

    /// Bridge port for the game started by ▶ Play (default: first free port from 15702).
    #[arg(long)]
    port: Option<u16>,

    /// Path to the `viber` executable used by ▶ Play (default: `viber` on PATH, else Viber's own
    /// target/).
    #[arg(long)]
    viber_bin: Option<PathBuf>,

    /// Bake directory backing the World Doc panel (default: `<world dir>/<stem>.data` when present).
    #[arg(long)]
    bake: Option<PathBuf>,

    /// Bottom dock tab to open at startup (QA/agents): console|logs|worlddoc|files
    #[arg(long)]
    tab: Option<String>,

    /// Editor mode at startup (QA/agents): 3d|ui|script
    #[arg(long)]
    mode: Option<String>,

    /// View options at startup (QA/agents): comma list grid,colliders,ortho
    #[arg(long)]
    views: Option<String>,

    /// Save a screenshot of the editor window to this PNG after `--frames` frames, then exit
    /// (visual QA without a human at the keyboard).
    #[arg(long)]
    screenshot: Option<PathBuf>,

    /// Expose the engine bridge of the EDITOR on this port: agents edit the same open
    /// document (`viber scene --live --port P …`, `viber terrain --live …`) — shared undo,
    /// live sync, visible here
    #[arg(long)]
    bridge: Option<u16>,

    /// Select this node at startup and frame it (`name=well`, `world/village.xml#3`) — the
    /// same selectors as `viber scene`
    #[arg(long)]
    select: Option<String>,

    /// Frames to wait before `--screenshot` (assets and terrain need time to stream in).
    #[arg(long, default_value_t = 600)]
    frames: u32,

    /// UI language: `pt` (default) or `en`. Also toggleable from the toolbar.
    #[arg(long)]
    lang: Option<String>,
}

fn main() -> anyhow::Result<()> {
    app::run(Cli::parse())
}
