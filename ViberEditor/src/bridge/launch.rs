//! Spawning and lifetime management of the Viber engine process.

use anyhow::{Context, bail};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Child;

/// How many ports above `start` are scanned, matching the engine's own bridge picker.
const PORT_SPAN: u16 = 64;

/// Returns the first port from `start` that can be bound on the loopback interface.
pub fn free_bridge_port(start: u16) -> Option<u16> {
    (start..start + PORT_SPAN).find(|port| TcpListener::bind(("127.0.0.1", *port)).is_ok())
}

/// Locates the `viber` executable, building it from the sibling checkout when needed.
///
/// Order: the `--viber-bin` override, then `viber` on `PATH`, then `Viber/target/{release,debug}`,
/// and finally a one-off `cargo build --release` in the sibling Viber checkout.
pub fn viber_binary(explicit: Option<&Path>) -> anyhow::Result<PathBuf> {
    if let Some(path) = explicit {
        return Ok(path.to_path_buf());
    }
    if let Some(path) = find_in_path("viber") {
        return Ok(path);
    }
    let viber_dir = viber_checkout();
    for profile in ["release", "debug"] {
        let candidate = viber_dir.join("target").join(profile).join("viber");
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    build_viber(&viber_dir)?;
    let binary = viber_dir.join("target").join("release").join("viber");
    if binary.is_file() {
        Ok(binary)
    } else {
        bail!("cargo build finished but {} is missing", binary.display())
    }
}

/// Spawns `viber run <world>` with the debug bridge pinned to `port`.
///
/// `camera_free` adds `--camera free` (editor orbit camera, no player input)
/// — the engine rejects the flag in older builds, so the caller only passes
/// it when the editor chose that mode.
pub fn spawn_engine(
    binary: &Path,
    world: &Path,
    port: u16,
    camera_free: bool,
) -> anyhow::Result<Child> {
    let mut command = std::process::Command::new(binary);
    command
        .arg("run")
        .arg(world)
        .arg("--no-cargo")
        .arg("--bridge")
        .arg(port.to_string());
    if camera_free {
        command.arg("--camera").arg("free");
    }
    command
        // Never re-delegate through cargo; we already picked the binary ourselves.
        .env("VIBER_CLI_NO_CARGO_DELEGATE", "1")
        // Debug render do Rapier disponível para o toggle "colliders" do editor.
        .env("VIBER_PHYSICS_DEBUG", "1")
        .spawn()
        .with_context(|| format!("failed to spawn engine {}", binary.display()))
}

/// The Viber checkout this editor was developed against, next to this crate.
pub fn viber_checkout() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("Viber")
}

fn find_in_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

fn build_viber(viber_dir: &Path) -> anyhow::Result<()> {
    eprintln!("vibereditor: building Viber (release) once…");
    let status = std::process::Command::new("cargo")
        .args(["build", "--release"])
        .current_dir(viber_dir)
        .status()
        .with_context(|| format!("failed to run cargo build in {}", viber_dir.display()))?;
    if !status.success() {
        bail!("cargo build --release failed in {}", viber_dir.display());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn free_bridge_port_is_bindable_and_scan_is_deterministic() {
        let busy = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let start = busy.local_addr().unwrap().port();
        // `start` itself may or may not be taken by our own listener; the scan must skip it.
        let Some(port) = free_bridge_port(start) else {
            panic!("no free port found above {start}");
        };
        assert!((start..start + PORT_SPAN).contains(&port));
        assert!(
            TcpListener::bind(("127.0.0.1", port)).is_ok(),
            "reported port must be free"
        );
    }
}
