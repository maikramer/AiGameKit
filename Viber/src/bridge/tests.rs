//! Testes headless do debug bridge: App mínima + bridge real sobre HTTP
//! (loopback) — sem janela, sem render. Os testes e2e correm num único
//! `#[test]` para não competir pela mesma porta.

use super::client::BridgeClient;
use super::*;
use bevy::MinimalPlugins;
use std::thread::JoinHandle;

const TEST_PORT: u16 = 35702;

/// Bombeia frames até a chamada responder (a resposta só é escrita quando
/// `app.update()` processa o pedido em RemoteLast).
fn settle(app: &mut App, handle: JoinHandle<Result<Value, String>>) -> Value {
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    handle.join().unwrap().expect("bridge call responde")
}

/// Variante para chamadas que DEVEM falhar (devolve a mensagem de erro BRP).
fn settle_err(app: &mut App, handle: JoinHandle<Result<Value, String>>) -> String {
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    handle.join().unwrap().expect_err("chamada devia falhar")
}

fn call_async(method: &'static str, params: Value) -> JoinHandle<Result<Value, String>> {
    std::thread::spawn(move || {
        BridgeClient::localhost(TEST_PORT)
            .call(method, params)
            .map_err(|error| error.to_string())
    })
}

#[test]
fn test_bridge_end_to_end_headless() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(bevy::input::InputPlugin)
        .add_plugins(BridgePlugin { port: TEST_PORT });
    app.world_mut()
        .spawn((Name::new("hero"), Transform::default()));
    // Sem WindowPlugin (headless), registamos o message de cursor à mão.
    app.add_message::<CursorMoved>();
    app.update(); // Startup: liga o servidor HTTP

    // ping
    let pong = settle(&mut app, call_async(METHOD_PING, serde_json::json!({})));
    let _ = "ping responde";
    assert_eq!(pong["pong"], serde_json::json!(true));
    assert!(pong["version"].is_string());

    // tree: hero presente
    let tree = settle(&mut app, call_async(METHOD_TREE, serde_json::json!({})));
    let _ = "tree responde";
    let entries = tree.as_array().expect("tree é lista");
    assert!(
        entries
            .iter()
            .any(|n| n.get("name").and_then(Value::as_str) == Some("hero")),
        "hero na árvore: {tree}"
    );

    // input.key → evento KeyboardInput + ButtonInput
    settle(
        &mut app,
        call_async(METHOD_KEY, serde_json::json!({ "key": "KeyW" })),
    );
    let keyboard = app.world().resource::<Messages<KeyboardInput>>();
    assert!(
        keyboard.len() >= 2,
        "press+release enviados: {}",
        keyboard.len()
    );

    // input.text
    let text = settle(
        &mut app,
        call_async(METHOD_TEXT, serde_json::json!({ "text": "aB" })),
    );
    assert_eq!(text["chars"], serde_json::json!(2));

    // input.click + input.move → CursorMoved
    let click = call_async(METHOD_CLICK, serde_json::json!({ "x": 10.0, "y": 20.0 }));
    let mouse_move = call_async(METHOD_MOVE, serde_json::json!({ "x": 30.0, "y": 40.0 }));
    for _ in 0..600 {
        app.update();
        if click.is_finished() && mouse_move.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    click.join().unwrap().expect("input.click responde");
    mouse_move.join().unwrap().expect("input.move responde");
    let cursor = app.world().resource::<Messages<CursorMoved>>();
    assert!(
        cursor.len() >= 2,
        "CursorMoved de click+move: {}",
        cursor.len()
    );

    // screenshot: pedido em fila fica pending sem render
    let shot = settle(
        &mut app,
        call_async(METHOD_SCREENSHOT, serde_json::json!({})),
    );
    let id = shot["id"].as_u64().expect("capture id");

    let status = settle(
        &mut app,
        call_async(METHOD_SCREENSHOT_STATUS, serde_json::json!({ "id": id })),
    );
    assert_eq!(status["status"], serde_json::json!("pending"));

    let error = settle_err(
        &mut app,
        call_async(METHOD_SCREENSHOT_STATUS, serde_json::json!({ "id": 99999 })),
    );
    assert!(error.contains("unknown capture id"), "erro: {error}");
}

#[test]
fn test_normalize_key_aliases() {
    use super::client::normalize_key;
    assert_eq!(normalize_key("w"), "KeyW");
    assert_eq!(normalize_key("7"), "Digit7");
    assert_eq!(normalize_key("space"), "Space");
    assert_eq!(normalize_key("esc"), "Escape");
    assert_eq!(normalize_key("up"), "ArrowUp");
    assert_eq!(normalize_key("ctrl"), "ControlLeft");
    assert_eq!(normalize_key("KeyW"), "KeyW");
    assert_eq!(normalize_key("F5"), "F5");
}

#[test]
fn test_keycode_for_char_mapping() {
    assert_eq!(keycode_for_char('a'), (KeyCode::KeyA, false));
    assert_eq!(keycode_for_char('A'), (KeyCode::KeyA, true));
    assert_eq!(keycode_for_char('5'), (KeyCode::Digit5, false));
    assert_eq!(keycode_for_char('!'), (KeyCode::Digit1, true));
    assert_eq!(keycode_for_char(' '), (KeyCode::Space, false));
}
