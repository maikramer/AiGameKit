//! Cliente do debug bridge — JSON-RPC/BRP sobre HTTP/1.1 cru (std, sem deps).
//! Usado pelos subcomandos `viber debug …`.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use base64::Engine as _;
use serde_json::{Value, json};

pub struct BridgeClient {
    pub host: String,
    pub port: u16,
}

impl BridgeClient {
    pub fn localhost(port: u16) -> Self {
        Self {
            host: "127.0.0.1".into(),
            port,
        }
    }

    /// POST JSON-RPC e devolve o campo `result` (bail no `error` BRP).
    pub fn call(&self, method: &str, params: Value) -> Result<Value> {
        let request = json!({
            "jsonrpc": "2.0",
            "id": 0,
            "method": method,
            "params": params,
        });
        let body = serde_json::to_vec(&request)?;
        let mut stream = self.connect()?;
        let http = format!(
            "POST / HTTP/1.1\r\nHost: {}:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            self.host,
            self.port,
            body.len()
        );
        stream.write_all(http.as_bytes())?;
        stream.write_all(&body)?;
        stream.flush()?;

        let mut raw = Vec::new();
        stream.read_to_end(&mut raw)?;
        let (headers, response_body) = split_headers(&raw).context("resposta HTTP sem corpo")?;
        // Com `Connection: close` o corpo é o resto do stream; se vier
        // chunked, descodifica (tamanhos de chunk são em BYTES — decoder
        // byte-a-byte para não desalinhar com UTF-8 no payload).
        let chunked = String::from_utf8_lossy(headers)
            .to_ascii_lowercase()
            .contains("transfer-encoding: chunked");
        let payload = if chunked {
            String::from_utf8(decode_chunked(response_body)?).context("chunk inválido (UTF-8)")?
        } else {
            String::from_utf8_lossy(response_body).into_owned()
        };

        let parsed: Value =
            serde_json::from_str(payload.trim()).context("resposta não é JSON-RPC")?;
        if let Some(error) = parsed.get("error") {
            bail!(
                "bridge error {}: {}",
                error.get("code").and_then(Value::as_i64).unwrap_or(0),
                error.get("message").and_then(Value::as_str).unwrap_or("?")
            );
        }
        Ok(parsed.get("result").cloned().unwrap_or(Value::Null))
    }

    /// Liga ao bridge com retry — o servidor HTTP do `bevy_remote` faz bind
    /// de forma assíncrona (task pool), logo logo após `viber run --bridge` o
    /// primeiro connect pode falhar (EAGAIN/refused).
    fn connect(&self) -> Result<TcpStream> {
        const ATTEMPTS: usize = 20;
        let mut last = None;
        for attempt in 0..ATTEMPTS {
            match TcpStream::connect((self.host.as_str(), self.port)) {
                Ok(stream) => {
                    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
                    return Ok(stream);
                }
                Err(error) => {
                    last = Some(error);
                    if attempt + 1 < ATTEMPTS {
                        std::thread::sleep(Duration::from_millis(100));
                    }
                }
            }
        }
        bail!(
            "a ligar ao bridge {}:{} (a engine corre com `viber run --bridge`?): {}",
            self.host,
            self.port,
            last.expect("pelo menos uma tentativa")
        )
    }

    /// Pede uma captura e faz polling de `viber.screenshot_status` até o PNG
    /// chegar (a captura completa ao fim de ~1-3 frames do render).
    pub fn screenshot(&self, timeout_ms: u64) -> Result<(Vec<u8>, String)> {
        let request = self.call("viber.screenshot", json!({}))?;
        let id = request
            .get("id")
            .and_then(Value::as_u64)
            .context("resposta sem capture id")?;
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        loop {
            if Instant::now() >= deadline {
                bail!("timeout ({timeout_ms} ms) à espera do screenshot");
            }
            std::thread::sleep(Duration::from_millis(50));
            let status = self.call("viber.screenshot_status", json!({ "id": id }))?;
            match status.get("status").and_then(Value::as_str) {
                Some("captured") => {
                    let b64 = status
                        .get("png_base64")
                        .and_then(Value::as_str)
                        .context("captura sem png")?;
                    let bytes = base64::engine::general_purpose::STANDARD.decode(b64)?;
                    let path = status
                        .get("path")
                        .and_then(Value::as_str)
                        .unwrap_or("?")
                        .to_string();
                    return Ok((bytes, path));
                }
                Some("pending") => continue,
                other => bail!("estado inesperado da captura: {:?}", other),
            }
        }
    }

    pub fn screenshot_to_file(&self, output: &Path, timeout_ms: u64) -> Result<String> {
        let (bytes, source_path) = self.screenshot(timeout_ms)?;
        std::fs::write(output, &bytes)
            .with_context(|| format!("a escrever {}", output.display()))?;
        Ok(source_path)
    }

    pub fn probe(&self) -> Result<Value> {
        self.call("viber.ping", json!({}))
    }

    pub fn tree(&self) -> Result<Value> {
        self.call("viber.tree", json!({}))
    }

    pub fn logs(&self, limit: usize) -> Result<Value> {
        self.call("viber.logs", json!({ "limit": limit }))
    }

    pub fn key(&self, key: &str, text: Option<String>, shift: bool) -> Result<Value> {
        let mut params = json!({ "key": normalize_key(key) });
        if let Some(text) = text {
            params["text"] = json!(text);
        }
        if shift {
            params["shift"] = json!(true);
        }
        self.call("viber.input.key", params)
    }

    pub fn text(&self, text: &str) -> Result<Value> {
        self.call("viber.input.text", json!({ "text": text }))
    }

    pub fn click(&self, x: f32, y: f32, button: &str) -> Result<Value> {
        self.call(
            "viber.input.click",
            json!({ "x": x, "y": y, "button": normalize_mouse(button) }),
        )
    }

    pub fn move_cursor(&self, x: f32, y: f32) -> Result<Value> {
        self.call("viber.input.move", json!({ "x": x, "y": y }))
    }
}

/// Separa headers do corpo na resposta HTTP crua (bytes).
fn split_headers(raw: &[u8]) -> Option<(&[u8], &[u8])> {
    let index = raw.windows(4).position(|window| window == b"\r\n\r\n")?;
    Some((&raw[..index], &raw[index + 4..]))
}

/// Decode de corpo HTTP chunked byte-a-byte (tamanhos de chunk são em bytes).
fn decode_chunked(body: &[u8]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(pos) = rest.windows(2).position(|window| window == b"\r\n") {
        let size_line = &rest[..pos];
        let size_text = std::str::from_utf8(size_line)
            .map_err(|_| anyhow::anyhow!("chunk size inválido"))?
            .split(';')
            .next()
            .unwrap_or("0")
            .trim();
        let size = usize::from_str_radix(size_text, 16).context("chunk size inválido")?;
        if size == 0 {
            break;
        }
        let start = pos + 2;
        let end = start + size;
        if end > rest.len() {
            break;
        }
        out.extend_from_slice(&rest[start..end]);
        rest = &rest[end..];
        if rest.starts_with(b"\r\n") {
            rest = &rest[2..];
        }
    }
    Ok(out)
}

/// Normaliza aliases amigáveis para variantes serde do `KeyCode`.
/// Ex.: `a` → `KeyA`, `1` → `Digit1`, `up` → `ArrowUp`, `esc` → `Escape`.
#[must_use]
pub fn normalize_key(raw: &str) -> String {
    let trimmed = raw.trim();
    let lower = trimmed.to_ascii_lowercase();
    let named: Option<&str> = match lower.as_str() {
        "space" | "spacebar" => Some("Space"),
        "enter" | "return" => Some("Enter"),
        "esc" | "escape" => Some("Escape"),
        "tab" => Some("Tab"),
        "backspace" => Some("Backspace"),
        "delete" | "del" => Some("Delete"),
        "insert" => Some("Insert"),
        "home" => Some("Home"),
        "end" => Some("End"),
        "pageup" => Some("PageUp"),
        "pagedown" => Some("PageDown"),
        "up" | "arrowup" => Some("ArrowUp"),
        "down" | "arrowdown" => Some("ArrowDown"),
        "left" | "arrowleft" => Some("ArrowLeft"),
        "right" | "arrowright" => Some("ArrowRight"),
        "shift" => Some("ShiftLeft"),
        "ctrl" | "control" => Some("ControlLeft"),
        "alt" => Some("AltLeft"),
        "capslock" => Some("CapsLock"),
        "f1" => Some("F1"),
        "f2" => Some("F2"),
        "f3" => Some("F3"),
        "f4" => Some("F4"),
        "f5" => Some("F5"),
        "f6" => Some("F6"),
        "f7" => Some("F7"),
        "f8" => Some("F8"),
        "f9" => Some("F9"),
        "f10" => Some("F10"),
        "f11" => Some("F11"),
        "f12" => Some("F12"),
        _ => None,
    };
    if let Some(name) = named {
        return name.to_string();
    }
    let mut chars = trimmed.chars();
    if let (Some(first), None) = (chars.next(), chars.next()) {
        if first.is_ascii_alphabetic() {
            return format!("Key{}", first.to_ascii_uppercase());
        }
        if let Some(digit) = first.to_digit(10) {
            return format!("Digit{digit}");
        }
    }
    trimmed.to_string()
}

fn normalize_mouse(raw: &str) -> String {
    match raw.to_ascii_lowercase().as_str() {
        "left" | "l" => "Left".into(),
        "right" | "r" => "Right".into(),
        "middle" | "m" => "Middle".into(),
        "back" => "Back".into(),
        "forward" => "Forward".into(),
        other => other.to_string(),
    }
}

/// Resolve a porta do bridge: flag CLI → env `VIBER_BRIDGE_PORT` → default.
pub fn resolve_port(flag: Option<u16>) -> u16 {
    flag.or_else(|| {
        std::env::var("VIBER_BRIDGE_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
    })
    .unwrap_or(super::DEFAULT_BRIDGE_PORT)
}
