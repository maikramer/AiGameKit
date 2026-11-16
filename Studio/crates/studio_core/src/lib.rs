//! Núcleo headless do AiGameKit Studio.
//!
//! Este crate não depende de Bevy: projeto em disco, geração dos YAML do
//! GameAssets, parsing do contrato `gameassets studio` e execução das CLIs
//! (com eventos de progresso por canal).

pub mod check;
pub mod cli;
pub mod config;
pub mod events;
pub mod manifest;
pub mod motion;
pub mod project;
pub mod reroll;
pub mod runner;

/// Versão do contrato `gameassets studio` que este crate entende.
pub const STUDIO_VERSION: u32 = 1;

/// Versão do próprio Studio.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
