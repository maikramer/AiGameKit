//! Input para scripts: o parser de teclas/rato (partilhado por
//! `set_interaction`, `interacted` e `viber.input.*`) e a tabela
//! `viber.input` (input CRU de teclado e rato, qualquer tecla).

use bevy::input::keyboard::KeyCode;
use bevy::input::mouse::MouseButton;
use mlua::{Lua, Table};

use super::ctx::ScriptCtx;

/// Tecla Lua (`"e"`, `"w"`, `"f5"`, `"arrowup"`/`"up"`, `"space"`, `"lshift"`,
/// `"="`) → [`KeyCode`]. Case-insensitive e tolerante a `_`/`-` (`"arrow_up"`);
/// aceita o teclado TODO — `set_interaction` e `viber.input.*` partilham o
/// parser, portanto uma interação pode viver em qualquer tecla, não só nas
/// seis históricas (`e j f q r space`).
pub fn key_code_from_str(key: &str) -> Option<KeyCode> {
    // Símbolos literais primeiro: a normalização abaixo só guarda
    // alfanuméricos, e estes são o seu próprio nome.
    match key {
        "=" | "+" => return Some(KeyCode::Equal),
        "-" => return Some(KeyCode::Minus),
        "[" => return Some(KeyCode::BracketLeft),
        "]" => return Some(KeyCode::BracketRight),
        ";" => return Some(KeyCode::Semicolon),
        "'" => return Some(KeyCode::Quote),
        "," => return Some(KeyCode::Comma),
        "." => return Some(KeyCode::Period),
        "/" => return Some(KeyCode::Slash),
        "\\" | "`" => return Some(KeyCode::Backquote),
        _ => {}
    }
    // Normalização: minúsculas, só alfanuméricos (`"Arrow_Up"` → `"arrowup"`).
    let norm: String = key
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect();
    let k = norm.as_str();
    // Letras e dígitos de uma só letra.
    if k.len() == 1 {
        let c = k.chars().next().unwrap();
        if c.is_ascii_lowercase() {
            return letter_key(c);
        }
        if c.is_ascii_digit() {
            return digit_key(c);
        }
    }
    // F1..F24 e teclado numérico.
    if let Some(rest) = k.strip_prefix('f') {
        if let Ok(n) = rest.parse::<u8>() {
            return function_key(n);
        }
    }
    if let Some(rest) = k.strip_prefix("numpad") {
        if let Ok(n) = rest.parse::<u8>() {
            return numpad_key(n);
        }
    }
    Some(match k {
        "space" => KeyCode::Space,
        "enter" | "return" => KeyCode::Enter,
        "escape" | "esc" => KeyCode::Escape,
        "tab" => KeyCode::Tab,
        "backspace" => KeyCode::Backspace,
        "capslock" | "caps" => KeyCode::CapsLock,
        "shift" | "lshift" => KeyCode::ShiftLeft,
        "rshift" => KeyCode::ShiftRight,
        "ctrl" | "control" | "lctrl" | "lcontrol" => KeyCode::ControlLeft,
        "rctrl" | "rcontrol" => KeyCode::ControlRight,
        "alt" | "lalt" => KeyCode::AltLeft,
        "ralt" => KeyCode::AltRight,
        "meta" | "lmeta" | "cmd" | "super" => KeyCode::SuperLeft,
        "rmeta" => KeyCode::SuperRight,
        "up" | "arrowup" => KeyCode::ArrowUp,
        "down" | "arrowdown" => KeyCode::ArrowDown,
        "left" | "arrowleft" => KeyCode::ArrowLeft,
        "right" | "arrowright" => KeyCode::ArrowRight,
        "pageup" => KeyCode::PageUp,
        "pagedown" => KeyCode::PageDown,
        "home" => KeyCode::Home,
        "end" => KeyCode::End,
        "insert" => KeyCode::Insert,
        "delete" | "del" => KeyCode::Delete,
        _ => return None,
    })
}

/// Estado de input para `viber.input.*`: justo-pressionado / held / just-largado.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InputState {
    Pressed,
    Down,
    Released,
}

/// Um input resolvido para `viber.input.*`: tecla OU botão do rato
/// (`"mouse1"`/`"lmb"`, `"mouse2"`/`"rmb"`, `"mouse3"`/`"mmb"`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputCode {
    Key(KeyCode),
    Mouse(bevy::input::mouse::MouseButton),
}

/// Nome de input Lua → [`InputCode`] (tecla primeiro; `"left"` é a SETA,
/// não o rato — para o rato usar `"mouse1"`/`"lmb"`).
pub fn input_code_from_str(name: &str) -> Option<InputCode> {
    if let Some(k) = key_code_from_str(name) {
        return Some(InputCode::Key(k));
    }
    let norm: String = name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect();
    let mouse = match norm.as_str() {
        "mouse1" | "mouseleft" | "lmb" => bevy::input::mouse::MouseButton::Left,
        "mouse2" | "mouseright" | "rmb" => bevy::input::mouse::MouseButton::Right,
        "mouse3" | "mousemiddle" | "mmb" => bevy::input::mouse::MouseButton::Middle,
        _ => return None,
    };
    Some(InputCode::Mouse(mouse))
}

/// `viber.input.*` sobre o snapshot do frame: tecla ou rato no estado pedido.
fn input_is_active(ctx: &ScriptCtx, code: InputCode, state: InputState) -> bool {
    match (code, state) {
        (InputCode::Key(k), InputState::Pressed) => ctx.just_pressed.contains(&k),
        (InputCode::Key(k), InputState::Down) => ctx.keys_down.contains(&k),
        (InputCode::Key(k), InputState::Released) => ctx.keys_released.contains(&k),
        (InputCode::Mouse(m), InputState::Pressed) => ctx.mouse_pressed.contains(&m),
        (InputCode::Mouse(m), InputState::Down) => ctx.mouse_down.contains(&m),
        (InputCode::Mouse(m), InputState::Released) => ctx.mouse_released.contains(&m),
    }
}

/// `'a'..='z'` → `KeyCode::KeyA..KeyZ`.
fn letter_key(c: char) -> Option<KeyCode> {
    Some(match c {
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
        'z' => KeyCode::KeyZ,
        _ => return None,
    })
}

/// `'0'..='9'` → `KeyCode::Digit0..Digit9`.
fn digit_key(c: char) -> Option<KeyCode> {
    Some(match c {
        '0' => KeyCode::Digit0,
        '1' => KeyCode::Digit1,
        '2' => KeyCode::Digit2,
        '3' => KeyCode::Digit3,
        '4' => KeyCode::Digit4,
        '5' => KeyCode::Digit5,
        '6' => KeyCode::Digit6,
        '7' => KeyCode::Digit7,
        '8' => KeyCode::Digit8,
        '9' => KeyCode::Digit9,
        _ => return None,
    })
}

/// `1..=24` → `KeyCode::F1..F24`.
fn function_key(n: u8) -> Option<KeyCode> {
    Some(match n {
        1 => KeyCode::F1,
        2 => KeyCode::F2,
        3 => KeyCode::F3,
        4 => KeyCode::F4,
        5 => KeyCode::F5,
        6 => KeyCode::F6,
        7 => KeyCode::F7,
        8 => KeyCode::F8,
        9 => KeyCode::F9,
        10 => KeyCode::F10,
        11 => KeyCode::F11,
        12 => KeyCode::F12,
        13 => KeyCode::F13,
        14 => KeyCode::F14,
        15 => KeyCode::F15,
        16 => KeyCode::F16,
        17 => KeyCode::F17,
        18 => KeyCode::F18,
        19 => KeyCode::F19,
        20 => KeyCode::F20,
        21 => KeyCode::F21,
        22 => KeyCode::F22,
        23 => KeyCode::F23,
        24 => KeyCode::F24,
        _ => return None,
    })
}

/// `0..=9` → `KeyCode::Numpad0..Numpad9`.
fn numpad_key(n: u8) -> Option<KeyCode> {
    Some(match n {
        0 => KeyCode::Numpad0,
        1 => KeyCode::Numpad1,
        2 => KeyCode::Numpad2,
        3 => KeyCode::Numpad3,
        4 => KeyCode::Numpad4,
        5 => KeyCode::Numpad5,
        6 => KeyCode::Numpad6,
        7 => KeyCode::Numpad7,
        8 => KeyCode::Numpad8,
        9 => KeyCode::Numpad9,
        _ => return None,
    })
}

/// Instala `viber.input` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
        // ── Input genérico ──────────────────────────────────────────────
    // Input CRU de teclado e rato (qualquer tecla, não só as de
    // interação). NÃO é gateado por MenusOpen — um script que queira
    // respeitar menus compõe com `viber.ui.is_open()`. Os três estados
    // partilham o parser (`input_code_from_str`) e o snapshot do frame.
    let input = lua.create_table()?;
    for (name, state) in [
        ("pressed", InputState::Pressed),
        ("down", InputState::Down),
        ("released", InputState::Released),
    ] {
        input.set(
            name,
            lua.create_function(move |lua, key: String| {
                let code = input_code_from_str(&key).ok_or_else(|| {
                    mlua::Error::runtime(format!("viber.input.{name}: input desconhecido '{key}'"))
                })?;
                let active = lua
                    .app_data_ref::<ScriptCtx>()
                    .map(|ctx| input_is_active(&ctx, code, state))
                    .unwrap_or(false);
                Ok(active)
            })?,
        )?;
    }
    api.set("input", input)?;
    Ok(())
}
