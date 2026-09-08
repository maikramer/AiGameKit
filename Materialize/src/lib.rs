//! materialize-cli library — exposes every module so integration tests
//! (`tests/gpu_parity.rs`) can drive the pipeline and compare against the
//! CPU reference implementations.

pub mod analyze;
pub mod app;
pub mod batch;
pub mod cli;
pub mod error;
pub mod gpu;
pub mod io;
pub mod pipeline;
pub mod preset;
pub mod reference;
pub mod seam;
pub mod skill_install;
pub mod vramd;
