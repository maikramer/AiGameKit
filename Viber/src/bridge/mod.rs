//! Debug bridge — BRP sobre HTTP (`bevy_remote`) com métodos `viber.*`:
//! screenshots, input sintético, árvore de entidades e ring-buffer de logs.
//! É o equivalente nativo do tooling Chrome DevTools MCP usado no VibeGame.
//!
//! Activar com `viber run --bridge` (porta por omissão: 15702, a porta BRP).
//! Cliente: `viber debug screenshot|click|key|text|move|tree|logs|probe`.

pub mod client;
pub mod logs;

use std::collections::{BTreeMap, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use base64::Engine as _;
use bevy::ecs::message::Messages;
use bevy::ecs::system::In;
use bevy::input::keyboard::{Key, KeyCode, KeyboardInput, NativeKey};
use bevy::input::mouse::{MouseButton, MouseButtonInput};
use bevy::input::{ButtonInput, ButtonState};
use bevy::prelude::*;
use bevy::remote::http::RemoteHttpPlugin;
use bevy::remote::{BrpError, BrpResult, RemotePlugin};
use bevy::render::view::screenshot::{Screenshot, save_to_disk};
use bevy::window::{CursorMoved, PrimaryWindow};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// Porta BRP por omissão (a mesma do `bevy_remote`).
pub const DEFAULT_BRIDGE_PORT: u16 = 15702;

pub const METHOD_PING: &str = "viber.ping";
pub const METHOD_SCREENSHOT: &str = "viber.screenshot";
pub const METHOD_SCREENSHOT_STATUS: &str = "viber.screenshot_status";
pub const METHOD_TREE: &str = "viber.tree";
pub const METHOD_LOGS: &str = "viber.logs";
pub const METHOD_KEY: &str = "viber.input.key";
pub const METHOD_TEXT: &str = "viber.input.text";
pub const METHOD_CLICK: &str = "viber.input.click";
pub const METHOD_MOVE: &str = "viber.input.move";

/// Estado partilhado entre os handlers BRP (PreUpdate) e os sistemas (Update).
#[derive(Resource, Default)]
pub struct BridgeShared {
    pub captures: Arc<Mutex<CaptureStore>>,
    pub logs: Arc<Mutex<VecDeque<logs::LogEntry>>>,
}

impl BridgeShared {
    pub fn new() -> Self {
        Self {
            captures: Arc::new(Mutex::new(CaptureStore::default())),
            logs: logs::global_log_buffer(),
        }
    }
}

/// Um pedido de screenshot em curso: o handler BRP enfileira, o sistema
/// `Update` spawna a captura e o cliente faz polling de `viber.screenshot_status`.
#[derive(Default)]
pub struct CaptureStore {
    next_id: u64,
    pending: Vec<(u64, PathBuf)>,
    captures: BTreeMap<u64, CaptureInfo>,
}

#[derive(Clone, Serialize)]
pub struct CaptureInfo {
    pub id: u64,
    /// `pending` | `captured`
    pub status: String,
    pub path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub png_base64: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl CaptureStore {
    fn take_pending(&mut self) -> Vec<(u64, PathBuf)> {
        std::mem::take(&mut self.pending)
    }

    fn request(&mut self) -> (u64, PathBuf) {
        self.next_id += 1;
        let id = self.next_id;
        let dir = std::env::temp_dir().join(format!("viber-bridge-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join(format!("shot-{id}.png"));
        let info = CaptureInfo {
            id,
            status: "pending".into(),
            path: path.display().to_string(),
            png_base64: None,
            bytes: None,
            error: None,
        };
        self.captures.insert(id, info);
        self.pending.push((id, path.clone()));
        (id, path)
    }

    fn get(&mut self, id: u64) -> Option<&CaptureInfo> {
        self.captures.get(&id)
    }

    fn mark_captured(&mut self, id: u64, bytes: usize, png_base64: String) -> Option<CaptureInfo> {
        let info = self.captures.get_mut(&id)?;
        info.status = "captured".into();
        info.bytes = Some(bytes);
        info.png_base64 = Some(png_base64);
        Some(info.clone())
    }
}

/// Motor de debugging remoto: BRP builtin (`world.query`, `world.spawn_entity`,
/// `world.insert_components`, …) + métodos `viber.*`.
pub struct BridgePlugin {
    pub port: u16,
}

impl Plugin for BridgePlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(BridgeShared::new())
            .add_plugins(
                RemotePlugin::default()
                    .with_method_main(METHOD_PING, ping)
                    .with_method_main(METHOD_SCREENSHOT, screenshot_request)
                    .with_method_main(METHOD_SCREENSHOT_STATUS, screenshot_status)
                    .with_method_main(METHOD_TREE, tree)
                    .with_method_main(METHOD_LOGS, logs_method)
                    .with_method_main(METHOD_KEY, input_key)
                    .with_method_main(METHOD_TEXT, input_text)
                    .with_method_main(METHOD_CLICK, input_click)
                    .with_method_main(METHOD_MOVE, input_move),
            )
            .add_plugins(RemoteHttpPlugin::default().with_port(self.port))
            .add_systems(Update, process_capture_requests);
    }
}

/// Sistema `Update`: transforma pedidos de captura pendentes em entidades
/// `Screenshot` com observer `save_to_disk` (o render escreve o PNG; o status
/// passa a `captured` no próximo `viber.screenshot_status`).
fn process_capture_requests(world: &mut World) {
    let requests = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared.captures.lock().unwrap();
        store.take_pending()
    };
    for (_, path) in requests {
        world
            .spawn(Screenshot::primary_window())
            .observe(save_to_disk(path));
    }
}

// ---------------------------------------------------------------- helpers

fn parse_params<T: DeserializeOwned>(params: Option<Value>) -> BrpResult<T> {
    let value = params.unwrap_or(Value::Null);
    serde_json::from_value(value).map_err(|error| BrpError {
        code: bevy::remote::error_codes::INVALID_PARAMS,
        message: format!("invalid params: {error}"),
        data: None,
    })
}

fn primary_window(world: &mut World) -> Option<Entity> {
    let mut query = world.query_filtered::<Entity, With<PrimaryWindow>>();
    query.single(world).ok()
}

fn invalid(message: String) -> BrpError {
    BrpError {
        code: bevy::remote::error_codes::INVALID_PARAMS,
        message,
        data: None,
    }
}

// ---------------------------------------------------------------- métodos

fn ping(_params: In<Option<Value>>, _world: &mut World) -> BrpResult {
    Ok(json!({
        "pong": true,
        "version": env!("CARGO_PKG_VERSION"),
    }))
}

fn screenshot_request(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let (id, path) = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared.captures.lock().unwrap();
        store.request()
    };
    Ok(json!({ "id": id, "path": path.display().to_string() }))
}

fn screenshot_status(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        id: u64,
    }
    let params: Params = parse_params(params.0)?;
    let shared = world.resource::<BridgeShared>();
    let mut store = shared.captures.lock().unwrap();
    let Some(info) = store.get(params.id) else {
        return Err(invalid(format!("unknown capture id {}", params.id)));
    };
    if info.status == "pending" {
        let path = PathBuf::from(&info.path);
        if path.is_file() {
            match std::fs::read(&path) {
                Ok(bytes) => {
                    let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
                    let info = store
                        .mark_captured(params.id, bytes.len(), encoded)
                        .expect("id existe");
                    return Ok(serde_json::to_value(info).expect("serialize"));
                }
                Err(error) => {
                    if let Some(info) = store.captures.get_mut(&params.id) {
                        info.error = Some(error.to_string());
                    }
                }
            }
        }
    }
    let info = store.get(params.id).expect("id existe").clone();
    Ok(serde_json::to_value(info).expect("serialize"))
}

/// Árvore de entidades — o "a11y snapshot" do bridge: id, nome, pai,
/// translation e lista de componentes (nomes reflectidos).
fn tree(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Serialize)]
    struct EntityNode {
        id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        name: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        parent: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        translation: Option<[f32; 3]>,
        components: Vec<String>,
    }

    let mut nodes = Vec::new();
    for entity in world.iter_entities() {
        let components: Vec<String> = entity
            .archetype()
            .components()
            .iter()
            .filter_map(|id| world.components().get_info(*id))
            .map(|info| info.name().to_string())
            .collect();
        nodes.push(EntityNode {
            id: entity.id().to_string(),
            name: entity.get::<Name>().map(|n| n.to_string()),
            parent: entity
                .get::<ChildOf>()
                .map(|child_of| child_of.0.to_string()),
            translation: entity
                .get::<Transform>()
                .map(|t| [t.translation.x, t.translation.y, t.translation.z]),
            components,
        });
    }
    Ok(serde_json::to_value(nodes).expect("serialize"))
}

fn logs_method(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        limit: Option<usize>,
    }
    let params: Params = parse_params(params.0)?;
    let shared = world.resource::<BridgeShared>();
    let logs = shared.logs.lock().unwrap();
    let start = logs.len().saturating_sub(params.limit.unwrap_or(100));
    let entries: Vec<&logs::LogEntry> = logs.iter().skip(start).collect();
    Ok(serde_json::to_value(entries).expect("serialize"))
}

// ---------------------------------------------------------------- input

/// Envia um `KeyboardInput` sintético + actualiza `ButtonInput<KeyCode>`.
fn send_key(world: &mut World, key_code: KeyCode, state: ButtonState, text: Option<String>) {
    let window = primary_window(world).unwrap_or(Entity::PLACEHOLDER);
    let logical_key = match (&text, key_code) {
        (Some(t), _) => Key::Character(t.as_str().into()),
        (None, KeyCode::Space) => Key::Space,
        (None, KeyCode::Enter) => Key::Enter,
        (None, KeyCode::Escape) => Key::Escape,
        (None, KeyCode::Tab) => Key::Tab,
        _ => Key::Unidentified(NativeKey::Unidentified),
    };
    let text: Option<_> = text.map(|t| t.into());
    world
        .resource_mut::<Messages<KeyboardInput>>()
        .write(KeyboardInput {
            key_code,
            logical_key,
            state,
            text,
            window,
            repeat: false,
        });
    let mut input = world.resource_mut::<ButtonInput<KeyCode>>();
    match state {
        ButtonState::Pressed => input.press(key_code),
        ButtonState::Released => input.release(key_code),
    }
}

fn send_mouse_button(world: &mut World, button: MouseButton, state: ButtonState) {
    let window = primary_window(world).unwrap_or(Entity::PLACEHOLDER);
    world
        .resource_mut::<Messages<MouseButtonInput>>()
        .write(MouseButtonInput {
            button,
            state,
            window,
        });
    let mut input = world.resource_mut::<ButtonInput<MouseButton>>();
    match state {
        ButtonState::Pressed => input.press(button),
        ButtonState::Released => input.release(button),
    }
}

fn send_cursor(world: &mut World, position: Vec2) {
    let window = primary_window(world).unwrap_or(Entity::PLACEHOLDER);
    world
        .resource_mut::<Messages<CursorMoved>>()
        .write(CursorMoved {
            window,
            position,
            delta: None,
        });
}

fn input_key(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        /// Nome da variante `KeyCode` serde (ex.: `KeyW`, `Space`, `ArrowUp`).
        key: KeyCode,
        /// `click` (omissão) | `press` | `release`
        state: Option<String>,
        /// Texto do keypress (preenche `logical_key`/`text` do evento).
        text: Option<String>,
        /// Com `click`, envolve o char num Shift sintético (para maiúsculas).
        shift: Option<bool>,
    }
    let params: Params = parse_params(params.0)?;
    let state = params.state.as_deref().unwrap_or("click");
    let press = matches!(state, "click" | "press");
    let release = matches!(state, "click" | "release");
    if params.shift == Some(true) {
        send_key(world, KeyCode::ShiftLeft, ButtonState::Pressed, None);
    }
    if press {
        send_key(world, params.key, ButtonState::Pressed, params.text.clone());
    }
    if release {
        send_key(world, params.key, ButtonState::Released, params.text);
    }
    if params.shift == Some(true) {
        send_key(world, KeyCode::ShiftLeft, ButtonState::Released, None);
    }
    Ok(json!({ "sent": state }))
}

fn input_text(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        text: String,
    }
    let params: Params = parse_params(params.0)?;
    for char in params.text.chars() {
        let (key_code, shift) = keycode_for_char(char);
        if shift {
            send_key(world, KeyCode::ShiftLeft, ButtonState::Pressed, None);
        }
        send_key(
            world,
            key_code,
            ButtonState::Pressed,
            Some(char.to_string()),
        );
        send_key(
            world,
            key_code,
            ButtonState::Released,
            Some(char.to_string()),
        );
        if shift {
            send_key(world, KeyCode::ShiftLeft, ButtonState::Released, None);
        }
    }
    Ok(json!({ "chars": params.text.chars().count() }))
}

fn input_click(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        button: Option<MouseButton>,
    }
    let params: Params = parse_params(params.0)?;
    let button = params.button.unwrap_or(MouseButton::Left);
    send_cursor(world, Vec2::new(params.x, params.y));
    send_mouse_button(world, button, ButtonState::Pressed);
    send_mouse_button(world, button, ButtonState::Released);
    Ok(json!({ "x": params.x, "y": params.y, "button": format!("{button:?}") }))
}

fn input_move(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
    }
    let params: Params = parse_params(params.0)?;
    send_cursor(world, Vec2::new(params.x, params.y));
    Ok(json!({ "x": params.x, "y": params.y }))
}

/// KeyCode + shift para um char (alfanumérico e espaço; restantes chars
/// chegam como `Unidentified` com o campo `text` preenchido).
fn keycode_for_char(char: char) -> (KeyCode, bool) {
    if let Some(digit) = char.to_digit(10) {
        let variant = match digit {
            0 => KeyCode::Digit0,
            1 => KeyCode::Digit1,
            2 => KeyCode::Digit2,
            3 => KeyCode::Digit3,
            4 => KeyCode::Digit4,
            5 => KeyCode::Digit5,
            6 => KeyCode::Digit6,
            7 => KeyCode::Digit7,
            8 => KeyCode::Digit8,
            _ => KeyCode::Digit9,
        };
        return (variant, false);
    }
    if char.is_ascii_uppercase() {
        return (letter_keycode(char.to_ascii_lowercase()), true);
    }
    if char.is_ascii_lowercase() {
        return (letter_keycode(char), false);
    }
    if char == ' ' {
        return (KeyCode::Space, false);
    }
    match char {
        '!' => (KeyCode::Digit1, true),
        '@' => (KeyCode::Digit2, true),
        '#' => (KeyCode::Digit3, true),
        '$' => (KeyCode::Digit4, true),
        '%' => (KeyCode::Digit5, true),
        '^' => (KeyCode::Digit6, true),
        '&' => (KeyCode::Digit7, true),
        '*' => (KeyCode::Digit8, true),
        '(' => (KeyCode::Digit9, true),
        ')' => (KeyCode::Digit0, true),
        '-' => (KeyCode::Minus, false),
        '_' => (KeyCode::Minus, true),
        '=' => (KeyCode::Equal, false),
        '+' => (KeyCode::Equal, true),
        '.' => (KeyCode::Period, false),
        ',' => (KeyCode::Comma, false),
        '/' => (KeyCode::Slash, false),
        ';' => (KeyCode::Semicolon, false),
        '\'' => (KeyCode::Quote, false),
        '\n' | '\r' => (KeyCode::Enter, false),
        '\t' => (KeyCode::Tab, false),
        _ => (
            KeyCode::Unidentified(bevy::input::keyboard::NativeKeyCode::Unidentified),
            false,
        ),
    }
}

fn letter_keycode(char: char) -> KeyCode {
    match char {
        'a' => KeyCode::KeyA,
        'b' => KeyCode::KeyB,
        'c' => KeyCode::KeyC,
        'd' => KeyCode::KeyD,
        'e' => KeyCode::KeyE,
        'f' => KeyCode::KeyF,
        'g' => KeyCode::KeyG,
        'h' => KeyCode::KeyH,
        'i' => KeyCode::KeyI,
        'j' => KeyCode::KeyJ,
        'k' => KeyCode::KeyK,
        'l' => KeyCode::KeyL,
        'm' => KeyCode::KeyM,
        'n' => KeyCode::KeyN,
        'o' => KeyCode::KeyO,
        'p' => KeyCode::KeyP,
        'q' => KeyCode::KeyQ,
        'r' => KeyCode::KeyR,
        's' => KeyCode::KeyS,
        't' => KeyCode::KeyT,
        'u' => KeyCode::KeyU,
        'v' => KeyCode::KeyV,
        'w' => KeyCode::KeyW,
        'x' => KeyCode::KeyX,
        'y' => KeyCode::KeyY,
        _ => KeyCode::KeyZ,
    }
}

#[cfg(test)]
mod tests;
