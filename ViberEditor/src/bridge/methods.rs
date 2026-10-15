//! Typed wrappers around the Viber bridge's custom BRP methods, used by the Debugger dock while
//! the game runs (F5).
//!
//! The engine registers these next to the builtin BRP methods when run with `--bridge`; see
//! `Viber/src/bridge/mod.rs` for the authoritative parameter and response shapes.

use bevy::tasks::Task;
use serde::Deserialize;
use serde_json::{Value, json};

use super::{BrpClient, BrpClientError};

/// `viber.ping` answer.
#[derive(Debug, Deserialize, PartialEq)]
pub struct PingResponse {
    pub pong: bool,
    pub version: String,
    pub pid: u64,
    #[serde(default)]
    pub world: Option<String>,
}

/// One entry of the `viber.logs` ring buffer.
#[derive(Debug, Deserialize, Clone, PartialEq)]
pub struct LogEntry {
    pub time: f64,
    pub level: String,
    pub target: String,
    pub message: String,
}

/// `viber.lua` answer from the engine's Luau REPL.
#[derive(Debug, Deserialize, PartialEq)]
pub struct LuaEval {
    pub ok: bool,
    #[serde(default)]
    pub result: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub applied: Option<Value>,
}

pub fn ping(client: &BrpClient) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call("viber.ping", None)
}

pub fn logs(client: &BrpClient, limit: usize) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call("viber.logs", Some(json!({ "limit": limit })))
}

/// A árvore de entidades da engine a correr (dock Debugger do editor).
pub fn entity_tree(client: &BrpClient, max_depth: u32) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call("viber.tree", Some(json!({ "max_depth": max_depth })))
}

/// Liga/desliga o wireframe de colliders do jogo a correr (o launch do Play
/// define `VIBER_PHYSICS_DEBUG=1` para o plugin existir).
pub fn colliders_toggle(client: &BrpClient, on: bool) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call(
        "viber.profiler.extra_toggle",
        Some(json!({ "id": "colliders", "on": on })),
    )
}

/// Recarrega um script Luau no jogo a correndo (`viber.scripts.reload`).
pub fn scripts_reload(client: &BrpClient, path: &str) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call("viber.scripts.reload", Some(json!({ "path": path })))
}

pub fn lua_eval(client: &BrpClient, code: &str) -> Task<Result<Value, BrpClientError>> {
    client.spawn_call("viber.lua", Some(json!({ "code": code })))
}

/// Freezes the running game's simulation (`viber.debug.step(0)` — pauses and stays paused).
pub fn pause_game(client: &BrpClient) -> Task<Result<Value, BrpClientError>> {
    lua_eval(client, "return viber.debug.step(0)")
}

/// Resumes a game frozen by [`pause_game`] (`viber.debug.play()`).
pub fn resume_game(client: &BrpClient) -> Task<Result<Value, BrpClientError>> {
    lua_eval(client, "return viber.debug.play()")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ping() {
        let value = json!({
            "pong": true,
            "version": "0.1.0",
            "pid": 4242,
            "world": "/home/x/Viber/examples/simple-rpg/world.xml"
        });
        let ping: PingResponse = serde_json::from_value(value).unwrap();
        assert_eq!(
            ping,
            PingResponse {
                pong: true,
                version: "0.1.0".into(),
                pid: 4242,
                world: Some("/home/x/Viber/examples/simple-rpg/world.xml".into()),
            }
        );
    }

    #[test]
    fn parses_ping_without_world() {
        let ping: PingResponse =
            serde_json::from_value(json!({ "pong": true, "version": "0.1", "pid": 1 })).unwrap();
        assert_eq!(ping.world, None);
    }

    #[test]
    fn parses_logs() {
        let value = json!([
            { "time": 1760000000.5, "level": "INFO", "target": "viber::terrain", "message": "chunk up" },
            { "time": 1760000001.0, "level": "WARN", "target": "bevy_render", "message": "slow frame" }
        ]);
        let entries: Vec<LogEntry> = serde_json::from_value(value).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].level, "INFO");
        assert_eq!(entries[1].message, "slow frame");
    }

    #[test]
    fn parses_lua_eval() {
        let ok: LuaEval = serde_json::from_value(json!({
            "ok": true, "result": "42", "applied": false, "warnings": []
        }))
        .unwrap();
        assert!(ok.ok);
        assert_eq!(ok.result.as_deref(), Some("42"));

        let err: LuaEval =
            serde_json::from_value(json!({ "ok": false, "error": "syntax error: }" })).unwrap();
        assert!(!err.ok);
        assert_eq!(err.error.as_deref(), Some("syntax error: }"));
    }
}
