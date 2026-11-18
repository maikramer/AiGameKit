//! Cria um projeto Studio mínimo para desenvolvimento/QA.
//!
//! `cargo run -p studio_core --example make_project -- <dir> <id> <categoria> <prompt>`

use std::path::PathBuf;

use studio_core::{config::StudioConfig, project::StudioProject};

fn main() {
    let mut args = std::env::args().skip(1);
    let dir = PathBuf::from(args.next().unwrap_or_else(|| {
        eprintln!("uso: make_project <dir> <id> <categoria> <prompt>");
        std::process::exit(2);
    }));
    if dir.join(studio_core::project::STUDIO_FILE).is_file() {
        let project = match StudioProject::open(&dir) {
            Ok(project) => project,
            Err(err) => {
                eprintln!("erro: {err}");
                std::process::exit(1);
            }
        };
        if let Err(err) = project.save_config() {
            eprintln!("erro: {err}");
            std::process::exit(1);
        }
        println!("{} (sincronizado)", project.dir.display());
        return;
    }
    let id = args.next().unwrap_or_else(|| "demo".into());
    let category = args.next().unwrap_or_else(|| "prop".into());
    let idea = args
        .next()
        .unwrap_or_else(|| "a small wooden crate with iron bands".into());
    let config = StudioConfig::new(&id, &idea, &category);
    match StudioProject::create(&dir, config) {
        Ok(project) => {
            println!("{}", project.dir.display());
            println!("state: game.yaml + manifest.yaml + studio.toml");
        }
        Err(err) => {
            eprintln!("erro: {err}");
            std::process::exit(1);
        }
    }
}
