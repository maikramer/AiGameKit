//! Play mode (F5): the game runs as a separate `viber run --bridge` process, Godot-style, while
//! the editor keeps its own in-process scene. This module owns that process and the bridge
//! connection the Debugger dock (Console, Logs) talks through.
//!
//! The JSON-RPC transport is Viber's own blocking [`BridgeClient`], driven on the IO task pool so
//! the editor frame never waits on the network.

pub mod launch;
pub mod methods;

use bevy::prelude::*;
use bevy::tasks::{IoTaskPool, Task, block_on, poll_once};
use serde_json::Value;
use std::path::Path;
use std::process::Child;
use std::time::Duration;
use viber::bridge::client::BridgeClient;

use methods::PingResponse;

/// The bridge port the engine itself picks when given no explicit one.
pub const DEFAULT_BRIDGE_PORT: u16 = 15702;
/// How often `viber.ping` is retried while waiting for the engine.
const PING_INTERVAL: Duration = Duration::from_millis(500);
/// How long to wait for the bridge before giving up (cargo builds are slow the first time).
const BRIDGE_TIMEOUT: Duration = Duration::from_secs(180);

/// A failed bridge call (transport or BRP error), as text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrpClientError(pub String);

impl std::fmt::Display for BrpClientError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// Async JSON-RPC client for the engine bridge on `127.0.0.1:<port>`.
#[derive(Debug, Clone, Copy)]
pub struct BrpClient {
    port: u16,
}

impl BrpClient {
    pub fn localhost(port: u16) -> Self {
        Self { port }
    }

    /// Calls `method` on the IO task pool; poll the task with `poll_once`.
    pub fn spawn_call(
        &self,
        method: &str,
        params: Option<Value>,
    ) -> Task<Result<Value, BrpClientError>> {
        let port = self.port;
        let method = method.to_owned();
        IoTaskPool::get().spawn(async move {
            BridgeClient::localhost(port)
                .call(&method, params.unwrap_or(Value::Null))
                .map_err(|error| BrpClientError(format!("{error:#}")))
        })
    }
}

/// Lifecycle of the game process behind the bridge.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum EngineStatus {
    /// No game running — the editor is editing.
    #[default]
    Stopped,
    /// The game was spawned and the bridge has not answered `viber.ping` yet.
    WaitingForBridge,
    /// The bridge answered; the Debugger panels may talk to it.
    Connected { pid: u64, version: String },
    /// The game died, never came up, or timed out.
    Failed(String),
}

/// The running game (if any) and the client for its `viber.*` bridge methods.
#[derive(Resource)]
pub struct ViberBridge {
    pub client: BrpClient,
    pub port: u16,
    pub status: EngineStatus,
    child: Option<Child>,
    ping: Option<Task<Result<Value, BrpClientError>>>,
    retry: Timer,
    waited: Duration,
}

impl ViberBridge {
    pub fn new(port: u16) -> Self {
        Self {
            client: BrpClient::localhost(port),
            port,
            status: EngineStatus::Stopped,
            child: None,
            ping: None,
            retry: Timer::new(PING_INTERVAL, TimerMode::Repeating),
            waited: Duration::ZERO,
        }
    }

    /// Starts the game for `world` (stopping a previous run first).
    pub fn play(&mut self, binary: &Path, world: &Path) -> anyhow::Result<()> {
        self.stop();
        self.child = Some(launch::spawn_engine(binary, world, self.port, false)?);
        self.status = EngineStatus::WaitingForBridge;
        self.waited = Duration::ZERO;
        self.retry.reset();
        Ok(())
    }

    /// Stops the game this editor started, if any.
    pub fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            info!("stopping game (pid {})", child.id());
            let _ = child.kill();
            let _ = child.wait();
        }
        self.ping = None;
        self.status = EngineStatus::Stopped;
    }
}

pub struct BridgePlugin;

impl Plugin for BridgePlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(Update, poll_engine);
    }
}

/// Waits for the game's bridge to answer `viber.ping`, and notices when the process exits.
fn poll_engine(mut bridge: ResMut<ViberBridge>, time: Res<Time>) {
    if let Some(mut child) = bridge.child.take() {
        match child.try_wait() {
            Ok(None) => bridge.child = Some(child),
            Ok(Some(status)) => {
                bridge.ping = None;
                // Closing the game window is the normal way to end a play session.
                bridge.status = if status.success() {
                    EngineStatus::Stopped
                } else {
                    EngineStatus::Failed(format!("game exited: {status}"))
                };
                info!("game process exited: {status}");
                return;
            }
            Err(error) => {
                bridge.status = EngineStatus::Failed(format!("game process check: {error}"));
                return;
            }
        }
    }

    if bridge.status != EngineStatus::WaitingForBridge {
        return;
    }

    if let Some(task) = &mut bridge.ping
        && let Some(result) = block_on(poll_once(task))
    {
        bridge.ping = None;
        // A transport error while the game is still starting up is expected; keep waiting.
        if let Ok(value) = result {
            match serde_json::from_value::<PingResponse>(value) {
                Ok(info) if info.pong => {
                    info!(
                        "game bridge up on port {} (viber {}, pid {})",
                        bridge.port, info.version, info.pid
                    );
                    bridge.status = EngineStatus::Connected {
                        pid: info.pid,
                        version: info.version,
                    };
                    return;
                }
                Ok(_) => warn!("viber.ping answered without pong; retrying"),
                Err(error) => warn!("malformed viber.ping answer: {error}"),
            }
        }
    }

    bridge.waited += time.delta();
    if bridge.waited >= BRIDGE_TIMEOUT {
        bridge.ping = None;
        if let Some(mut child) = bridge.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        bridge.status = EngineStatus::Failed("game bridge did not come up within 180s".into());
        return;
    }

    if bridge.ping.is_none() && bridge.retry.tick(time.delta()).just_finished() {
        bridge.ping = Some(methods::ping(&bridge.client));
    }
}

/// Stops the game this editor started, on any exit path (window close or panic unwind).
impl Drop for ViberBridge {
    fn drop(&mut self) {
        self.stop();
    }
}
