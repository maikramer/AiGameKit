//! The bake document layer: the read-only model of a baked world directory.
//!
//! A "bake" is the on-disk contract between the Viber engine and its tooling: a `bake.json`
//! describing the world (terrain spec, carved features, spawn groups) plus the baked
//! heightmap (`.ahgt`) it references. This module owns the editor-side types and readers —
//! independent from the engine crate, like the typed BRP answers in [`crate::bridge::methods`].

pub mod ahgt;
pub mod bake;
pub mod minimap;
