//! Viber — native Bevy engine for AiGameKit declarative world XML.
//!
//! Library crate exposing the XML loader (`xml`), the recipe IR (`recipes`)
//! and gameplay modules (`terrain`); the `viber` binary is a thin CLI on top.

pub mod ai;
pub mod animation;
pub mod bridge;
pub mod camera;
pub mod combat;
pub mod economy;
pub mod feedback;
pub mod hud;
pub mod luau;
pub mod menus;
pub mod meshopt;
pub mod music;
pub mod particles;
pub mod physics;
pub mod player;
pub mod profiler;
pub mod quests;
pub mod recipes;
pub mod save;
pub mod scaffold;
pub mod sky;
pub mod spawner;
pub mod terrain;
pub mod travel;
pub mod vitals;
pub mod worldsys;
pub mod xml;
