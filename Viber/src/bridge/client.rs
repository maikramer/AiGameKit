//! Cliente do debug bridge — JSON-RPC/BRP sobre HTTP/1.1 cru (std, sem deps).
//! Usado pelos subcomandos `viber debug …`.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use base64::Engine as _;
use serde_json::{Value, json};

/// Cap da resposta lida do socket — um peer hostil que goteja bytes não
/// pode fazer OOM do CLI num `read_to_end` sem limite.
const MAX_RESPONSE: u64 = 256 << 20;

/// Timeout de leitura normal — respostas grandes (screenshots em base64)
/// precisam de folga.
const READ_TIMEOUT: Duration = Duration::from_secs(30);
/// Probe da descoberta de sessão: uma engine saudável responde ao ping em
/// ms; uma PENDURADA (TCP aceita, frame congelado — ex.: `while true do end`
/// na REPL) nunca responde, e o timeout normal bloqueava a descoberta 30 s
/// por sessão antes de passar à seguinte.
const PROBE_TIMEOUT: Duration = Duration::from_secs(5);

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
        self.call_timeout(method, params, READ_TIMEOUT)
    }

    /// `call` com timeout de LEITURA próprio (o connect mantém os seus 2 s).
    fn call_timeout(&self, method: &str, params: Value, read_timeout: Duration) -> Result<Value> {
        let request = json!({
            "jsonrpc": "2.0",
            "id": 0,
            "method": method,
            "params": params,
        });
        let body = serde_json::to_vec(&request)?;
        let mut stream = self.connect()?;
        stream.set_read_timeout(Some(read_timeout))?;
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
        stream.take(MAX_RESPONSE).read_to_end(&mut raw)?;
        if raw.len() as u64 >= MAX_RESPONSE {
            bail!(
                "resposta do bridge excede o cap de {} MB",
                MAX_RESPONSE >> 20
            );
        }
        let (headers, response_body) = split_headers(&raw).context("resposta HTTP sem corpo")?;
        // Com `Connection: close` o corpo é o resto do stream; se vier
        // chunked, descodifica (tamanhos de chunk são em BYTES — decoder
        // byte-a-byte para não desalinhar com UTF-8 no payload).
        let chunked = is_chunked(headers);
        let payload = if chunked {
            String::from_utf8(decode_chunked(response_body)?).context("chunk inválido (UTF-8)")?
        } else {
            String::from_utf8_lossy(response_body).into_owned()
        };

        let status = http_status(headers);
        let parsed: Value = serde_json::from_str(payload.trim()).with_context(|| {
            if status == 200 {
                "resposta não é JSON-RPC".to_string()
            } else {
                format!("resposta não é JSON-RPC (bridge devolveu HTTP {status})")
            }
        })?;
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
        const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
        // Resolve 1× fora do loop — o retry existe para o bind assíncrono do
        // servidor, não para pagar DNS/connect bloqueado em cada tentativa.
        let addr = (self.host.as_str(), self.port)
            .to_socket_addrs()?
            .next()
            .context("endereço do bridge não resolve")?;
        let mut last = None;
        for attempt in 0..ATTEMPTS {
            match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
                Ok(stream) => {
                    stream.set_read_timeout(Some(READ_TIMEOUT))?;
                    stream.set_write_timeout(Some(Duration::from_secs(30)))?;
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
            // Um Err transitório num poll não deve abortar enquanto houver
            // deadline — só propaga quando o tempo esgotou.
            let status = match self.call("viber.screenshot_status", json!({ "id": id })) {
                Ok(status) => status,
                Err(error) => {
                    if Instant::now() < deadline {
                        continue;
                    }
                    return Err(error);
                }
            };
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
                Some("error") => {
                    // O servidor falhou a leitura do PNG — falhar já com a
                    // causa em vez de girar até timeout.
                    let cause = status
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("desconhecida");
                    bail!("captura falhou: {cause}");
                }
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

    /// Pede um burst — N frames seguidos compostos numa única folha na
    /// engine (4096 no lado comprido, células com o formato do frame) — e
    /// faz polling de `viber.burst_status` até a folha chegar. `skip` são
    /// frames renderizados entre capturas (`0` = consecutivos). Devolve
    /// (png, caminho do PNG na engine, largura e altura da folha).
    pub fn burst(
        &self,
        frames: u32,
        skip: u32,
        timeout_ms: u64,
    ) -> Result<(Vec<u8>, String, u32, u32, Value)> {
        let request = self.call("viber.burst", json!({ "frames": frames, "skip": skip }))?;
        let id = request
            .get("id")
            .and_then(Value::as_u64)
            .context("resposta sem burst id")?;
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        let mut last_captured = 0u64;
        loop {
            if Instant::now() >= deadline {
                bail!("timeout ({timeout_ms} ms) à espera do burst de {frames} frames");
            }
            std::thread::sleep(Duration::from_millis(50));
            let status = match self.call("viber.burst_status", json!({ "id": id })) {
                Ok(status) => status,
                Err(error) => {
                    if Instant::now() < deadline {
                        continue;
                    }
                    return Err(error);
                }
            };
            // Progresso no stderr (o stdout fica para o resultado final).
            if let Some(count) = status.get("captured").and_then(Value::as_u64) {
                if count > last_captured {
                    last_captured = count;
                    eprintln!("viber: burst {count}/{frames} frames capturados");
                }
            }
            match status.get("status").and_then(Value::as_str) {
                Some("captured") => {
                    let b64 = status
                        .get("png_base64")
                        .and_then(Value::as_str)
                        .context("burst capturado sem png")?;
                    let bytes = base64::engine::general_purpose::STANDARD.decode(b64)?;
                    let path = status
                        .get("path")
                        .and_then(Value::as_str)
                        .unwrap_or("?")
                        .to_string();
                    let sheet = status.get("sheet");
                    let dim = |key: &str| -> u32 {
                        sheet
                            .and_then(|s| s.get(key))
                            .and_then(Value::as_u64)
                            .unwrap_or(0) as u32
                    };
                    return Ok((bytes, path, dim("sheet_w"), dim("sheet_h"), status));
                }
                Some("capturing" | "composing" | "pending") => continue,
                Some("error") => {
                    let cause = status
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("desconhecida");
                    bail!("burst falhou: {cause}");
                }
                other => bail!("estado inesperado do burst: {other:?}"),
            }
        }
    }

    pub fn probe(&self) -> Result<Value> {
        self.call("viber.ping", json!({}))
    }

    /// Probe da descoberta — ping com [`PROBE_TIMEOUT`] curto; devolve true
    /// se o bridge responde. Uma engine pendurada falha cedo em vez de
    /// bloquear a descoberta com o timeout de leitura normal.
    fn probe_quick(&self) -> bool {
        self.call_timeout("viber.ping", json!({}), PROBE_TIMEOUT)
            .is_ok()
    }

    /// Ping rápido que devolve o mundo servido (campo `world` do pong) —
    /// None se o bridge não responder ou for um binário antigo sem o campo.
    fn probe_world(&self) -> Option<String> {
        self.call_timeout("viber.ping", json!({}), PROBE_TIMEOUT)
            .ok()
            .and_then(|pong| {
                pong.get("world")
                    .and_then(Value::as_str)
                    .map(std::borrow::ToOwned::to_owned)
            })
    }

    pub fn tree(&self) -> Result<Value> {
        self.call("viber.tree", json!({}))
    }

    pub fn logs(&self, limit: usize) -> Result<Value> {
        self.call("viber.logs", json!({ "limit": limit }))
    }

    pub fn prof(&self) -> Result<Value> {
        self.call("viber.profiler", json!({}))
    }

    /// Snapshot rico de um tab do profiler (`viber.profiler.tab`):
    /// `systems|world|physics|audio|extras|all`.
    pub fn prof_tab(&self, tab: &str) -> Result<Value> {
        self.call("viber.profiler.tab", json!({ "tab": tab }))
    }

    /// Alterna um extra do profiler; devolve `{"id", "on"}`.
    pub fn prof_extra_toggle(&self, id: &str) -> Result<Value> {
        self.call("viber.profiler.extra_toggle", json!({ "id": id }))
    }

    /// Exporta o snapshot completo do profiler para ficheiro
    /// (`viber.profiler.export`); devolve `{path, bytes}`.
    pub fn prof_export(&self, path: Option<&std::path::Path>) -> Result<Value> {
        let mut params = json!({});
        if let Some(path) = path {
            params["path"] = json!(path.display().to_string());
        }
        self.call("viber.profiler.export", params)
    }

    /// Executa Luau na engine (`viber.lua`) — devolve
    /// `{ ok, result|error, applied, warnings }`.
    pub fn lua(&self, code: &str) -> Result<Value> {
        self.call("viber.lua", json!({ "code": code }))
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

/// Código HTTP do status line ("HTTP/1.1 200 OK" → 200; 0 se ilegível).
fn http_status(headers: &[u8]) -> u16 {
    String::from_utf8_lossy(headers)
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse().ok())
        .unwrap_or(0)
}

/// `Transfer-Encoding: chunked` por linha de header — a busca por substring
/// perdia variantes legais sem espaço após `:` (`Transfer-Encoding:chunked`).
fn is_chunked(headers: &[u8]) -> bool {
    String::from_utf8_lossy(headers)
        .lines()
        .filter_map(|line| line.split_once(':'))
        .any(|(name, value)| {
            name.trim().eq_ignore_ascii_case("transfer-encoding")
                && value.trim().eq_ignore_ascii_case("chunked")
        })
}

/// Decode de corpo HTTP chunked byte-a-byte (tamanhos de chunk são em bytes).
/// Só termina bem com o chunk terminador "0" — EOF sem ele é resposta
/// truncada e devolve ERRO em vez de dados parciais como bons.
fn decode_chunked(body: &[u8]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let Some(pos) = rest.windows(2).position(|window| window == b"\r\n") else {
            bail!("resposta chunked truncada (sem chunk terminador)");
        };
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
        // checked_add — um chunk size tipo "ffffffffffffffff" vindo da rede
        // faria wrap e panic no slice.
        let end = start
            .checked_add(size)
            .ok_or_else(|| anyhow::anyhow!("chunk size inválido"))?;
        if end > rest.len() {
            bail!("resposta chunked truncada");
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

/// Uma engine viva conhecida pelo registo de sessões (`engine.json` + TCP a
/// responder). Candidata da descoberta e alvo do `viber debug --world`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveEngine {
    pub port: u16,
    pub world: String,
    pub pid: u32,
}

/// Todas as engines registadas com a porta a responder a TCP. Sem probe HTTP
/// — isso fica para a confirmação final (uma engine pendurada não deve
/// bloquear o inventário).
pub fn list_live_engines() -> Vec<LiveEngine> {
    crate::session::SessionPaths::all()
        .iter()
        .filter_map(|(_, paths)| paths.engine_info())
        .filter(|engine| port_alive(engine.port))
        // IDENTIDADE, não só TCP: uma porta viva pode servir OUTRO mundo (o
        // registo fica stale quando a sessão morre e outra engine sobe na
        // mesma porta — observado ao vivo: 3 mundos listados no :15702,
        // dois deles mortos). Um ping confirma quem lá está; sem campo
        // `world` (binário antigo) mantém-se — não há como validar.
        .filter(|engine| match BridgeClient::localhost(engine.port).probe_world() {
            Some(served) => same_world(&served, &engine.world),
            None => true,
        })
        .map(|engine| LiveEngine {
            port: engine.port,
            world: engine.world,
            pid: engine.pid,
        })
        .collect()
}


/// Candidatas à descoberta implícita, por ordem de preferência — ou as locais
/// em conflito, quando há ≥2 debaixo do cwd (não há como adivinhar qual é a
/// do chamador; escolher a de porta mais baixa mandava comandos para a engine
/// de outro agente em silêncio).
#[derive(Debug, PartialEq, Eq)]
pub enum Candidates {
    /// A sondar por esta ordem (locais primeiro; senão todas por porta).
    Ordered(Vec<LiveEngine>),
    /// As locais em conflito, ordenadas por porta.
    AmbiguousLocals(Vec<LiveEngine>),
}

/// Escolha pura (sem I/O) — testável sem env nem sockets.
fn pick_engines(cwd: Option<&Path>, engines: &[LiveEngine]) -> Candidates {
    let rank = |engine: &LiveEngine| {
        let local = cwd.is_some_and(|cwd| Path::new(&engine.world).starts_with(cwd));
        (!local, engine.port)
    };
    let mut local: Vec<LiveEngine> = Vec::new();
    let mut remote: Vec<LiveEngine> = Vec::new();
    for engine in engines {
        if rank(engine).0 {
            remote.push(engine.clone());
        } else {
            local.push(engine.clone());
        }
    }
    local.sort_by_key(|engine| engine.port);
    remote.sort_by_key(|engine| engine.port);
    if local.len() >= 2 {
        Candidates::AmbiguousLocals(local)
    } else if local.len() == 1 {
        Candidates::Ordered(local)
    } else {
        Candidates::Ordered(remote)
    }
}

/// Lista `:porta — mundo`, uma engine por linha — corpo dos erros de
/// ambiguidade/alvo inexistente e do `viber debug probe` orientado.
pub fn format_engines(engines: &[LiveEngine]) -> String {
    engines
        .iter()
        .map(|engine| format!("  :{} — {}", engine.port, engine.world))
        .collect::<Vec<_>>()
        .join("\n")
}

fn ambiguity_error(engines: &[LiveEngine]) -> anyhow::Error {
    anyhow::anyhow!(
        "{} engines vivas neste checkout — não sei qual é a tua:\n{}\naponta a tua: `viber debug --world <mundo.xml> …`, `--port N` ou `export VIBER_BRIDGE_PORT=N`",
        engines.len(),
        format_engines(engines)
    )
}

/// Resultado da resolução do alvo de um comando `viber debug`.
pub enum TargetResolution {
    Port(u16),
    /// Só na via implícita (sem `--port`/`--world`/env): há várias engines
    /// locais — o chamador decide (o `probe` lista-as; os restantes falham).
    Ambiguous(Vec<LiveEngine>),
}

/// Resolve o alvo sem falhar na ambiguidade — o `viber debug probe` usa isto
/// para LISTAR as engines em vez de abortar. Ordem: `--port` → `--world` →
/// env `VIBER_BRIDGE_PORT` → descoberta implícita → default.
pub fn resolve_target(flag: Option<u16>, world: Option<&Path>) -> Result<TargetResolution> {
    if let Some(port) = flag {
        return Ok(TargetResolution::Port(port));
    }
    if let Some(world) = world {
        return resolve_world_port(world).map(TargetResolution::Port);
    }
    if let Ok(raw) = std::env::var("VIBER_BRIDGE_PORT") {
        match raw.parse::<u16>() {
            Ok(port) => return Ok(TargetResolution::Port(port)),
            // Um typo não pode cair noutra engine em silêncio (mutaria o
            // mundo errado) — avisa e segue para a descoberta.
            Err(_) => eprintln!("viber: VIBER_BRIDGE_PORT inválido: `{raw}` — a ignorar"),
        }
    }
    let cwd = std::env::current_dir().ok();
    match pick_engines(cwd.as_deref(), &list_live_engines()) {
        Candidates::AmbiguousLocals(engines) => Ok(TargetResolution::Ambiguous(engines)),
        Candidates::Ordered(candidates) => {
            for engine in candidates {
                if BridgeClient::localhost(engine.port).probe_quick() {
                    eprintln!(
                        "viber: bridge descoberto em :{} (sessão: {})",
                        engine.port, engine.world
                    );
                    return Ok(TargetResolution::Port(engine.port));
                }
            }
            Ok(TargetResolution::Port(super::DEFAULT_BRIDGE_PORT))
        }
    }
}

/// Resolve a porta do bridge: flag CLI → flag `--world` → env
/// `VIBER_BRIDGE_PORT` → engine viva registada numa sessão → default.
///
/// O passo da sessão é o que faz `viber debug prof` funcionar sem ninguém
/// exportar `VIBER_BRIDGE_PORT`. Com ≥2 engines vivas debaixo do cwd, a
/// escolha implícita é um ERRO — sem isto os comandos iam para a porta mais
/// baixa, muitas vezes a engine de outro agente.
pub fn resolve_port(flag: Option<u16>, world: Option<&Path>) -> Result<u16> {
    match resolve_target(flag, world)? {
        TargetResolution::Port(port) => Ok(port),
        TargetResolution::Ambiguous(engines) => Err(ambiguity_error(&engines)),
    }
}

/// Porta da engine que serve `query` — a via do `viber debug --world`.
/// Primeiro o caminho exato (o slug da sessão é estável entre grafias de
/// paths com symlinks porque o `for_world` canonicaliza antes de fazer
/// hash); sem registo, casa por nome (ficheiro, stem ou sufixo de path) entre
/// as engines vivas. Zero ou várias correspondências → erro com a lista.
fn resolve_world_port(query: &Path) -> Result<u16> {
    let canonical = std::fs::canonicalize(query)
        .or_else(|_| std::path::absolute(query))
        .unwrap_or_else(|_| query.to_path_buf());
    let paths = crate::session::SessionPaths::for_world(&canonical);
    if let Some(engine) = paths.engine_info() {
        return confirm_engine(&engine);
    }
    let live = list_live_engines();
    let matches: Vec<&LiveEngine> = live
        .iter()
        .filter(|engine| world_name_matches(query, &engine.world))
        .collect();
    match matches.len() {
        1 => confirm_engine_engine(matches[0]),
        0 => anyhow::bail!(
            "nenhuma engine viva serve `{}` — engines vivas:\n{}\n(sobe uma com `viber run {q} --bridge` ou `viber session up --world {q}`)",
            query.display(),
            format_engines(&live),
            q = query.display()
        ),
        _ => anyhow::bail!(
            "`{}` casa {} engines vivas — sê mais específico:\n{}",
            query.display(),
            matches.len(),
            format_engines(&matches.into_iter().cloned().collect::<Vec<_>>())
        ),
    }
}

fn confirm_engine(engine: &crate::session::EngineInfo) -> Result<u16> {
    confirm_engine_engine(&LiveEngine {
        port: engine.port,
        world: engine.world.clone(),
        pid: engine.pid,
    })
}

/// A porta existe e é MESMO a engine do mundo? Valida o `world` devolvido
/// pelo ping contra o registo — apanha a porta roubada por uma engine mais
/// recente com engine.json stale (mutaria o mundo errado).
fn confirm_engine_engine(engine: &LiveEngine) -> Result<u16> {
    if !port_alive(engine.port) {
        anyhow::bail!(
            "a engine de `{}` está registada na porta {} mas não responde — morreu? (`viber session list`)",
            engine.world,
            engine.port
        );
    }
    let client = BridgeClient::localhost(engine.port);
    if let Some(served) = client.probe_world() {
        if !same_world(&served, &engine.world) {
            anyhow::bail!(
                "a porta {} é servida por `{}`, não por `{}` (registo stale — sobe a engine de novo)",
                engine.port,
                served,
                engine.world
            );
        }
    }
    Ok(engine.port)
}

/// `query` identifica `world`? Caminho canónico igual, nome de ficheiro,
/// stem (`qa-pontes` → `qa-pontes.xml`) ou sufixo por componentes
/// (`worlds/qa-pontes.xml` dentro do path completo).
fn world_name_matches(query: &Path, world: &str) -> bool {
    if same_world(&query.to_string_lossy(), world) {
        return true;
    }
    let Some(query_name) = query.file_name().and_then(|n| n.to_str()) else {
        return false;
    };
    let world_path = Path::new(world);
    if world_path.ends_with(query) {
        return true;
    }
    let Some(world_name) = world_path.file_name().and_then(|n| n.to_str()) else {
        return false;
    };
    if query_name.eq_ignore_ascii_case(world_name) {
        return true;
    }
    query.extension().is_none()
        && world_path
            .file_stem()
            .and_then(|s| s.to_str())
            .is_some_and(|stem| query_name.eq_ignore_ascii_case(stem))
}

/// Grafias diferentes do mesmo mundo (symlinks, `..`) — compara canónico
/// quando ambos resolvem; literal caso contrário.
fn same_world(a: &str, b: &str) -> bool {
    if a == b {
        return true;
    }
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(ca), Ok(cb)) => ca == cb,
        _ => false,
    }
}

/// TCP connect com timeout curto — descoberta não pode pagar os 2 s de
/// retry do `connect()` por sessão morta.
pub fn port_alive(port: u16) -> bool {
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    std::net::TcpStream::connect_timeout(&address, std::time::Duration::from_millis(250)).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_port_alive_matches_tcp_listener() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().expect("addr").port();
        assert!(port_alive(port), "porta com listener devia estar viva");
        // Porta efémera provavelmente morta — o importante é ser RÁPIDO e
        // devolver false (sem os retries de 2 s do connect() do cliente).
        let start = std::time::Instant::now();
        let dead = port_alive(9); // discard — quase sempre fechado
        assert!(!dead, "porta 9 não devia estar viva");
        assert!(
            start.elapsed() < std::time::Duration::from_secs(1),
            "pre-check devia ser rápido, levou {:?}",
            start.elapsed()
        );
    }

    fn engine(port: u16, world: &str) -> LiveEngine {
        LiveEngine {
            port,
            world: world.to_string(),
            pid: 0,
        }
    }

    #[test]
    fn test_pick_two_locals_is_ambiguous() {
        // O cenário do agente: dois mundos qa-*.xml do mesmo checkout, cada
        // um na sua engine — escolher a de porta mais baixa era errar. A
        // engine de FORA do cwd não participa na ambiguidade.
        let engines = [
            engine(15702, "/outro/mundo.xml"),
            engine(15703, "/repo/worlds/qa-pontes.xml"),
            engine(15704, "/repo/worlds/qa-sky.xml"),
        ];
        let cwd = Path::new("/repo");
        assert_eq!(
            pick_engines(Some(cwd), &engines),
            Candidates::AmbiguousLocals(vec![
                engine(15703, "/repo/worlds/qa-pontes.xml"),
                engine(15704, "/repo/worlds/qa-sky.xml"),
            ])
        );
    }

    #[test]
    fn test_pick_single_local_wins_over_remotes() {
        let engines = [
            engine(15702, "/outro/mundo.xml"),
            engine(15709, "/repo/world.xml"),
        ];
        assert_eq!(
            pick_engines(Some(Path::new("/repo")), &engines),
            Candidates::Ordered(vec![engine(15709, "/repo/world.xml")])
        );
    }

    #[test]
    fn test_pick_no_locals_uses_lowest_port() {
        let engines = [engine(15705, "/a.xml"), engine(15703, "/b.xml")];
        // Sem cwd conhecido, tudo é remoto — ordem determinística por porta.
        assert_eq!(
            pick_engines(None, &engines),
            Candidates::Ordered(vec![engine(15703, "/b.xml"), engine(15705, "/a.xml")])
        );
    }

    #[test]
    fn test_pick_no_engines() {
        assert_eq!(
            pick_engines(Some(Path::new("/repo")), &[]),
            Candidates::Ordered(vec![])
        );
    }

    #[test]
    fn test_world_name_matches() {
        let world = "/repo/worlds/qa-pontes.xml";
        assert!(world_name_matches(Path::new(world), world), "igual literal");
        assert!(
            world_name_matches(Path::new("qa-pontes"), world),
            "stem sem extensão"
        );
        assert!(
            world_name_matches(Path::new("qa-pontes.xml"), world),
            "nome de ficheiro"
        );
        assert!(
            world_name_matches(Path::new("worlds/qa-pontes.xml"), world),
            "sufixo por componentes"
        );
        assert!(
            !world_name_matches(Path::new("qa-sky"), world),
            "outro mundo"
        );
        assert!(
            !world_name_matches(Path::new("pontes"), world),
            "substring não conta"
        );
        assert!(
            !world_name_matches(Path::new("qa-pontes.xml.bak"), world),
            "extensão diferente"
        );
    }

    #[test]
    fn test_format_engines_lists_port_and_world() {
        let text = format_engines(&[
            engine(15702, "/repo/world.xml"),
            engine(15705, "/repo/worlds/qa-pontes.xml"),
        ]);
        assert_eq!(
            text,
            "  :15702 — /repo/world.xml\n  :15705 — /repo/worlds/qa-pontes.xml"
        );
    }
}
