//! Viber — native Bevy engine for AiGameKit declarative world XML.
//!
//! Library crate exposing the XML loader (`xml`), the recipe IR (`recipes`)
//! and gameplay modules (`terrain`); the `viber` binary is a thin CLI on top.

pub mod bridge;
pub mod hud;
pub mod music;
pub mod particles;
pub mod physics;
pub mod player;
pub mod recipes;
pub mod scaffold;
pub mod sky;
pub mod spawner;
pub mod terrain;
pub mod worldsys;
pub mod xml;
