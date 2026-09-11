//! Viber — native Bevy engine for AiGameKit declarative world XML.
//!
//! Library crate exposing the XML loader (`xml`), the recipe IR (`recipes`)
//! and gameplay modules (`terrain`); the `viber` binary is a thin CLI on top.

pub mod ai;
pub mod ambient;
pub mod animation;
pub mod audit;
pub mod bridge;
pub mod camera;
pub mod combat;
pub mod config;
pub mod economy;
pub mod feedback;
pub mod grass;
pub mod harvest;
pub mod hot_reload;
pub mod hud;
pub mod ibl;
pub mod impact;
pub mod luau;
pub mod menus;
pub mod meshopt;
pub mod music;
pub mod particles;
pub mod physics;
pub mod physics_fx;
pub mod player;
pub mod postfx;
pub mod probes;
pub mod profiler;
pub mod prop_tint;
pub mod prune;
pub mod quests;
pub mod recipes;
pub mod render_lod;
pub mod rng;
pub mod save;
pub mod scaffold;
pub mod session;
pub mod skills;
pub mod sky;
pub mod sky_nishita;
pub mod spawner;
pub mod terrain;
pub mod textures;
pub mod trail;
pub mod travel;
pub mod ui;
pub mod vitals;
pub mod water_ssr;
pub mod worldsys;
pub mod xml;
