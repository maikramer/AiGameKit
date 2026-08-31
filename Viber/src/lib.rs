//! Viber — native Bevy engine for AiGameKit declarative world XML.
//!
//! Library crate exposing the XML loader (`xml`), the recipe IR (`recipes`)
//! and gameplay modules (`terrain`); the `viber` binary is a thin CLI on top.

pub mod recipes;
pub mod terrain;
pub mod xml;
