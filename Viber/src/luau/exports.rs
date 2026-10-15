//! Props exportadas de scripts — o `@export` do Godot para Luau.
//!
//! Um script declara as propriedades que cada instância pode afinar em
//! comentários de anotação (o código Luau fica intacto, sem parser):
//!
//! ```lua
//! --@export speed: number(0..5) = 1.1      -- m/s a passear
//! --@export lines: string[] = "Bom dia." | "Cuidado com os lobos."
//! --@export mood: enum(calm, grumpy) = calm
//! ```
//!
//! Cada instância guarda o seu valor num atributo `prop-<nome>` do nó XML
//! (`<Entity script="npc.lua" prop-speed="0.8">`) — texto cru, interpretado
//! pelo tipo declarado. Este módulo é a ÚNICA definição do contrato: runtime
//! (`viber.prop`), lint, esquema para agentes (`viber scene schema`) e o
//! Inspector do editor leem daqui.
//!
//! **Sinais** (o `signal` do Godot): o script declara o que emite com
//! `--@signal opened(gold, item) -- doc` e dispara com `viber.emit("opened", …)`.
//! Quem ouve é DADO do emissor: `connect-opened="porta_norte:abrir; @guardas:alarme"`
//! (alvo = nome de entidade ou `@grupo`, método = função global do script do alvo).

use std::fmt;

use serde_json::{Value, json};

/// Prefixo das anotações num script.
pub const EXPORT_MARK: &str = "--@export";
/// Prefixo dos atributos XML que carregam o valor de uma instância.
pub const PROP_ATTR_PREFIX: &str = "prop-";

/// O nome do prop de um atributo `prop-<nome>` (`None` para outros atributos).
pub fn prop_name(attr: &str) -> Option<&str> {
    attr.strip_prefix(PROP_ATTR_PREFIX)
        .filter(|name| valid_name(name))
}

/// O atributo XML que guarda o prop `name`.
pub fn prop_attr(name: &str) -> String {
    format!("{PROP_ATTR_PREFIX}{name}")
}

/// Prefixo das anotações de sinal.
pub const SIGNAL_MARK: &str = "--@signal";
/// Prefixo dos atributos XML que ligam um sinal do emissor a métodos de alvos.
pub const CONNECT_ATTR_PREFIX: &str = "connect-";

/// O sinal de um atributo `connect-<sinal>` (`None` para outros atributos).
pub fn connect_signal(attr: &str) -> Option<&str> {
    attr.strip_prefix(CONNECT_ATTR_PREFIX)
        .filter(|name| valid_name(name))
}

/// O atributo XML que guarda as conexões do sinal `name`.
pub fn connect_attr(name: &str) -> String {
    format!("{CONNECT_ATTR_PREFIX}{name}")
}

/// Alvo de uma conexão: uma entidade por nome, ou todos os membros de um grupo.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ConnTarget {
    Name(String),
    Group(String),
}

impl fmt::Display for ConnTarget {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConnTarget::Name(name) => f.write_str(name),
            ConnTarget::Group(group) => write!(f, "@{group}"),
        }
    }
}

/// Uma ligação `alvo:método` de um sinal.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Connection {
    pub target: ConnTarget,
    pub method: String,
}

impl fmt::Display for Connection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.target, self.method)
    }
}

/// Lê o valor de um `connect-<sinal>`: `alvo:método` separados por `;`
/// (`porta_norte:abrir; @guardas:alarme`). Vazio = sem ligações.
pub fn parse_connections(value: &str) -> Result<Vec<Connection>, String> {
    let mut out = Vec::new();
    for part in value.split(';').map(str::trim).filter(|p| !p.is_empty()) {
        let (target, method) = part
            .rsplit_once(':')
            .ok_or_else(|| format!("`{part}`: esperado `alvo:método` (ou `@grupo:método`)"))?;
        let (target, method) = (target.trim(), method.trim());
        if !is_lua_ident(method) {
            return Err(format!(
                "`{part}`: método `{method}` não é um identificador Lua"
            ));
        }
        let target = match target.strip_prefix('@') {
            Some(group) if valid_group(group) => ConnTarget::Group(group.to_owned()),
            Some(group) => return Err(format!("`{part}`: grupo `{group}` inválido")),
            None if !target.is_empty() => ConnTarget::Name(target.to_owned()),
            None => return Err(format!("`{part}`: alvo vazio")),
        };
        out.push(Connection {
            target,
            method: method.to_owned(),
        });
    }
    Ok(out)
}

/// Os grupos de um atributo `groups="guardas aldeia"` (espaços ou vírgulas;
/// repetidos e vazios caem). Nomes inválidos vão para o segundo vetor.
pub fn parse_groups(value: &str) -> (Vec<String>, Vec<String>) {
    let mut groups: Vec<String> = Vec::new();
    let mut bad = Vec::new();
    for group in value
        .split(|c: char| c == ',' || c.is_whitespace())
        .filter(|g| !g.is_empty())
    {
        if !valid_group(group) {
            bad.push(group.to_owned());
        } else if !groups.iter().any(|g| g == group) {
            groups.push(group.to_owned());
        }
    }
    (groups, bad)
}

/// Nome de grupo: como os de prop (minúsculas, dígitos, `_`/`-`, `.`).
pub fn valid_group(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && chars
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '-' | '.'))
}

fn is_lua_ident(name: &str) -> bool {
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Tipo declarado de um prop.
#[derive(Debug, Clone, PartialEq)]
pub enum PropType {
    Number {
        min: Option<f64>,
        max: Option<f64>,
    },
    Int {
        min: Option<i64>,
        max: Option<i64>,
    },
    Bool,
    String,
    /// Lista de textos — `a | b | c` (no XML: `a|b|c`).
    StringList,
    Enum(Vec<String>),
    /// `#rrggbb` / `#rrggbbaa`.
    Color,
    /// Três números (`x y z`).
    Vec3,
    /// Nome de outra entidade do mundo.
    Entity,
}

/// Valor interpretado de um prop.
#[derive(Debug, Clone, PartialEq)]
pub enum PropValue {
    Number(f64),
    Int(i64),
    Bool(bool),
    /// `string`, `enum`, `color` (normalizada) e `entity`.
    Text(String),
    List(Vec<String>),
    Vec3([f64; 3]),
}

/// Uma propriedade exportada por um script.
#[derive(Debug, Clone, PartialEq)]
pub struct ExportDef {
    pub name: String,
    pub ty: PropType,
    /// Default já validado contra o tipo (`None` = `nil`).
    pub default: Option<PropValue>,
    /// O default como o autor o escreveu (para mostrar/emitir).
    pub default_raw: Option<String>,
    /// Descrição (texto depois de `--` na linha da anotação).
    pub doc: String,
    /// Linha (1-based) da anotação no script.
    pub line: usize,
}

/// Anotação mal formada.
#[derive(Debug, Clone, PartialEq)]
pub struct ExportError {
    pub line: usize,
    pub message: String,
}

impl fmt::Display for ExportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "linha {}: {}", self.line, self.message)
    }
}

/// Um sinal declarado por um script (`--@signal nome(a, b) -- doc`).
#[derive(Debug, Clone, PartialEq)]
pub struct SignalDef {
    pub name: String,
    /// Nomes dos argumentos (documentação: o Lua não os verifica).
    pub args: Vec<String>,
    pub doc: String,
    pub line: usize,
}

impl SignalDef {
    pub fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "args": self.args,
            "attr": connect_attr(&self.name),
            "doc": self.doc,
            "line": self.line,
        })
    }
}

/// O esquema de um script: as exportações válidas e as anotações rejeitadas.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ScriptExports {
    pub defs: Vec<ExportDef>,
    pub errors: Vec<ExportError>,
    /// Sinais que o script emite (`--@signal`).
    pub signals: Vec<SignalDef>,
    /// Funções globais do script (`function nome(`, no início da linha) — os
    /// métodos que uma conexão pode chamar.
    pub methods: Vec<String>,
}

/// De onde vem o valor efetivo de um prop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PropSource {
    /// Atributo `prop-*` da instância.
    Instance,
    /// Default da anotação.
    Default,
}

impl PropSource {
    pub fn as_str(self) -> &'static str {
        match self {
            PropSource::Instance => "instance",
            PropSource::Default => "default",
        }
    }
}

/// Valor efetivo de um prop numa instância.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedProp<'a> {
    pub def: &'a ExportDef,
    /// `None` = `nil` (sem default nem valor da instância).
    pub value: Option<PropValue>,
    pub source: PropSource,
    /// O valor da instância não respeita o tipo (caiu para o default).
    pub error: Option<String>,
}

impl ScriptExports {
    pub fn get(&self, name: &str) -> Option<&ExportDef> {
        self.defs.iter().find(|def| def.name == name)
    }

    pub fn signal(&self, name: &str) -> Option<&SignalDef> {
        self.signals.iter().find(|signal| signal.name == name)
    }

    pub fn has_method(&self, name: &str) -> bool {
        self.methods.iter().any(|method| method == name)
    }

    /// O valor efetivo de cada exportação dada a lista de props crus da
    /// instância (`(nome, valor)`, sem o prefixo `prop-`). Um valor inválido
    /// cai para o default e reporta o erro.
    pub fn resolve<'a>(&'a self, props: &[(String, String)]) -> Vec<ResolvedProp<'a>> {
        self.defs
            .iter()
            .map(|def| {
                let raw = props.iter().rev().find(|(name, _)| *name == def.name);
                match raw.map(|(_, raw)| def.ty.parse_value(raw)) {
                    Some(Ok(value)) => ResolvedProp {
                        def,
                        value: Some(value),
                        source: PropSource::Instance,
                        error: None,
                    },
                    Some(Err(error)) => ResolvedProp {
                        def,
                        value: def.default.clone(),
                        source: PropSource::Default,
                        error: Some(error),
                    },
                    None => ResolvedProp {
                        def,
                        value: def.default.clone(),
                        source: PropSource::Default,
                        error: None,
                    },
                }
            })
            .collect()
    }

    /// Props da instância que o script não exporta (typos, props órfãos).
    pub fn unknown<'p>(&self, props: &'p [(String, String)]) -> Vec<&'p str> {
        props
            .iter()
            .map(|(name, _)| name.as_str())
            .filter(|name| self.get(name).is_none())
            .collect()
    }

    /// O esquema em JSON estável (para agentes, `viber scene schema`).
    pub fn to_json(&self) -> Value {
        json!({
            "exports": self.defs.iter().map(ExportDef::to_json).collect::<Vec<_>>(),
            "signals": self.signals.iter().map(SignalDef::to_json).collect::<Vec<_>>(),
            "methods": self.methods,
            "errors": self.errors.iter().map(|e| json!({"line": e.line, "message": e.message})).collect::<Vec<_>>(),
        })
    }
}

impl ExportDef {
    pub fn to_json(&self) -> Value {
        let mut entry = json!({
            "name": self.name,
            "type": self.ty.to_string(),
            "attr": prop_attr(&self.name),
            "default": self.default.as_ref().map(PropValue::to_json),
            "doc": self.doc,
            "line": self.line,
        });
        match &self.ty {
            PropType::Number { min, max } => {
                entry["min"] = json!(min);
                entry["max"] = json!(max);
            }
            PropType::Int { min, max } => {
                entry["min"] = json!(min);
                entry["max"] = json!(max);
            }
            PropType::Enum(options) => entry["options"] = json!(options),
            _ => {}
        }
        entry
    }
}

/// Lê as anotações `--@export` de um script.
pub fn parse_exports(source: &str) -> ScriptExports {
    let mut out = ScriptExports::default();
    for (index, line) in source.lines().enumerate() {
        let line_no = index + 1;
        // Funções globais: só `function nome(` no início da linha (as locais e
        // as de tabelas `function a.b(` não são métodos chamáveis por conexão).
        if let Some(rest) = line.strip_prefix("function ")
            && let Some((name, _)) = rest.split_once('(')
            && is_lua_ident(name.trim())
            && !out.has_method(name.trim())
        {
            out.methods.push(name.trim().to_owned());
        }
        if let Some(rest) = line.trim_start().strip_prefix(SIGNAL_MARK) {
            if !rest.is_empty() && !rest.starts_with(char::is_whitespace) {
                continue;
            }
            match parse_signal(rest.trim(), line_no) {
                Ok(signal) if out.signal(&signal.name).is_some() => out.errors.push(ExportError {
                    line: line_no,
                    message: format!("sinal `{}` declarado duas vezes", signal.name),
                }),
                Ok(signal) => out.signals.push(signal),
                Err(message) => out.errors.push(ExportError {
                    line: line_no,
                    message,
                }),
            }
            continue;
        }
        let Some(rest) = line.trim_start().strip_prefix(EXPORT_MARK) else {
            continue;
        };
        if !rest.is_empty() && !rest.starts_with(char::is_whitespace) {
            continue; // `--@exportfoo` não é anotação
        }
        match parse_annotation(rest.trim(), line_no) {
            Ok(def) if out.get(&def.name).is_some() => out.errors.push(ExportError {
                line: line_no,
                message: format!("prop `{}` declarado duas vezes", def.name),
            }),
            Ok(def) => out.defs.push(def),
            Err(message) => out.errors.push(ExportError {
                line: line_no,
                message,
            }),
        }
    }
    out
}

/// `nome[(a, b)] [-- doc]`.
fn parse_signal(text: &str, line: usize) -> Result<SignalDef, String> {
    let (body, doc) = split_doc(text);
    let (name, args) = match body.split_once('(') {
        Some((name, rest)) => {
            let inner = rest
                .trim_end()
                .strip_suffix(')')
                .ok_or_else(|| "sinal: falta `)`".to_owned())?;
            let args: Vec<String> = inner
                .split(',')
                .map(str::trim)
                .filter(|a| !a.is_empty())
                .map(str::to_owned)
                .collect();
            if let Some(bad) = args.iter().find(|a| !is_lua_ident(a)) {
                return Err(format!("sinal: argumento `{bad}` não é um identificador"));
            }
            (name.trim(), args)
        }
        None => (body.trim(), Vec::new()),
    };
    if !valid_name(name) {
        return Err(format!(
            "sinal `{name}` inválido (minúsculas, dígitos, `_`/`-`, a começar por letra)"
        ));
    }
    Ok(SignalDef {
        name: name.to_owned(),
        args,
        doc: doc.to_owned(),
        line,
    })
}

/// `nome: tipo [= default] [-- doc]`.
fn parse_annotation(text: &str, line: usize) -> Result<ExportDef, String> {
    let (body, doc) = split_doc(text);
    let (name, rest) = body
        .split_once(':')
        .ok_or_else(|| "esperado `nome: tipo [= default]`".to_owned())?;
    let name = name.trim();
    if !valid_name(name) {
        return Err(format!(
            "nome `{name}` inválido (minúsculas, dígitos, `_`/`-`, a começar por letra)"
        ));
    }
    let (ty_text, default_text) = match rest.split_once('=') {
        Some((ty, default)) => (ty.trim(), Some(default.trim())),
        None => (rest.trim(), None),
    };
    let ty = PropType::parse(ty_text)?;
    let default_raw = default_text.filter(|d| !d.is_empty()).map(str::to_owned);
    let default = match &default_raw {
        Some(raw) => Some(
            ty.parse_value(raw)
                .map_err(|e| format!("default de `{name}`: {e}"))?,
        ),
        None => None,
    };
    Ok(ExportDef {
        name: name.to_owned(),
        ty,
        default,
        default_raw,
        doc: doc.to_owned(),
        line,
    })
}

/// Separa a descrição (`-- …` fora de aspas) do resto da anotação.
fn split_doc(text: &str) -> (&str, &str) {
    let mut quoted = false;
    let bytes = text.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        match b {
            b'"' => quoted = !quoted,
            b'-' if !quoted && bytes.get(i + 1) == Some(&b'-') => {
                return (text[..i].trim(), text[i + 2..].trim());
            }
            _ => {}
        }
    }
    (text.trim(), "")
}

fn valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// Tira aspas `"…"` à volta de um valor (opcionais na anotação).
fn unquote(text: &str) -> &str {
    let text = text.trim();
    text.strip_prefix('"')
        .and_then(|t| t.strip_suffix('"'))
        .unwrap_or(text)
}

impl PropType {
    /// `number`, `number(0..5)`, `int(1..)`, `bool`, `string`, `string[]`,
    /// `enum(a, b)`, `color`, `vec3`, `entity`.
    pub fn parse(text: &str) -> Result<PropType, String> {
        let text = text.trim();
        let (head, args) = match text.split_once('(') {
            Some((head, args)) => {
                let args = args
                    .strip_suffix(')')
                    .ok_or_else(|| format!("tipo `{text}` sem `)`"))?;
                (head.trim(), Some(args.trim()))
            }
            None => (text, None),
        };
        let ty = match (head, args) {
            ("number", None) => PropType::Number {
                min: None,
                max: None,
            },
            ("number", Some(range)) => {
                let (min, max) = parse_range(range, |s| s.parse::<f64>().ok())?;
                PropType::Number { min, max }
            }
            ("int", None) => PropType::Int {
                min: None,
                max: None,
            },
            ("int", Some(range)) => {
                let (min, max) = parse_range(range, |s| s.parse::<i64>().ok())?;
                PropType::Int { min, max }
            }
            ("bool", None) => PropType::Bool,
            ("string", None) => PropType::String,
            ("string[]", None) => PropType::StringList,
            ("enum", Some(options)) => {
                let options: Vec<String> = options
                    .split(',')
                    .map(|o| unquote(o).to_owned())
                    .filter(|o| !o.is_empty())
                    .collect();
                if options.is_empty() {
                    return Err("enum() sem opções".to_owned());
                }
                PropType::Enum(options)
            }
            ("color", None) => PropType::Color,
            ("vec3", None) => PropType::Vec3,
            ("entity", None) => PropType::Entity,
            _ => {
                return Err(format!(
                    "tipo `{text}` desconhecido (number, int, bool, string, string[], enum(…), color, vec3, entity)"
                ));
            }
        };
        Ok(ty)
    }

    /// Interpreta um valor cru (atributo `prop-*` ou default da anotação).
    pub fn parse_value(&self, raw: &str) -> Result<PropValue, String> {
        let text = raw.trim();
        match self {
            PropType::Number { min, max } => {
                let value: f64 = unquote(text)
                    .parse()
                    .ok()
                    .filter(|v: &f64| v.is_finite())
                    .ok_or_else(|| format!("`{text}` não é um número"))?;
                check_range(value, *min, *max)?;
                Ok(PropValue::Number(value))
            }
            PropType::Int { min, max } => {
                let value: i64 = unquote(text)
                    .parse()
                    .map_err(|_| format!("`{text}` não é um inteiro"))?;
                check_range(value, *min, *max)?;
                Ok(PropValue::Int(value))
            }
            PropType::Bool => match unquote(text) {
                "true" | "1" | "yes" | "on" => Ok(PropValue::Bool(true)),
                "false" | "0" | "no" | "off" => Ok(PropValue::Bool(false)),
                _ => Err(format!("`{text}` não é bool (true/false)")),
            },
            PropType::String => Ok(PropValue::Text(unquote(text).to_owned())),
            PropType::StringList => Ok(PropValue::List(
                text.split('|')
                    .map(|item| unquote(item).to_owned())
                    .filter(|item| !item.is_empty())
                    .collect(),
            )),
            PropType::Enum(options) => {
                let value = unquote(text);
                if options.iter().any(|o| o == value) {
                    Ok(PropValue::Text(value.to_owned()))
                } else {
                    Err(format!("`{value}` não é uma de: {}", options.join(", ")))
                }
            }
            PropType::Color => parse_color(unquote(text)).map(PropValue::Text),
            PropType::Vec3 => {
                let parts: Vec<f64> = unquote(text)
                    .split(|c: char| c.is_whitespace() || c == ',')
                    .filter(|p| !p.is_empty())
                    .map(|p| p.parse::<f64>().ok().filter(|v| v.is_finite()))
                    .collect::<Option<_>>()
                    .ok_or_else(|| format!("`{text}` não é `x y z`"))?;
                match parts[..] {
                    [x, y, z] => Ok(PropValue::Vec3([x, y, z])),
                    _ => Err(format!("`{text}` não é `x y z` (3 números)")),
                }
            }
            PropType::Entity => {
                let name = unquote(text);
                if name.is_empty() {
                    Err("nome de entidade vazio".to_owned())
                } else {
                    Ok(PropValue::Text(name.to_owned()))
                }
            }
        }
    }
}

impl fmt::Display for PropType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fn range<T: fmt::Display>(
            f: &mut fmt::Formatter<'_>,
            head: &str,
            min: &Option<T>,
            max: &Option<T>,
        ) -> fmt::Result {
            if min.is_none() && max.is_none() {
                return f.write_str(head);
            }
            write!(f, "{head}(")?;
            if let Some(min) = min {
                write!(f, "{min}")?;
            }
            f.write_str("..")?;
            if let Some(max) = max {
                write!(f, "{max}")?;
            }
            f.write_str(")")
        }
        match self {
            PropType::Number { min, max } => range(f, "number", min, max),
            PropType::Int { min, max } => range(f, "int", min, max),
            PropType::Bool => f.write_str("bool"),
            PropType::String => f.write_str("string"),
            PropType::StringList => f.write_str("string[]"),
            PropType::Enum(options) => write!(f, "enum({})", options.join(", ")),
            PropType::Color => f.write_str("color"),
            PropType::Vec3 => f.write_str("vec3"),
            PropType::Entity => f.write_str("entity"),
        }
    }
}

fn parse_range<T: Copy>(
    text: &str,
    parse: impl Fn(&str) -> Option<T>,
) -> Result<(Option<T>, Option<T>), String> {
    let (min, max) = text
        .split_once("..")
        .ok_or_else(|| format!("intervalo `{text}` não é `min..max`"))?;
    let bound = |s: &str| -> Result<Option<T>, String> {
        let s = s.trim();
        if s.is_empty() {
            Ok(None)
        } else {
            parse(s)
                .map(Some)
                .ok_or_else(|| format!("limite `{s}` inválido"))
        }
    };
    Ok((bound(min)?, bound(max)?))
}

fn check_range<T: PartialOrd + fmt::Display>(
    value: T,
    min: Option<T>,
    max: Option<T>,
) -> Result<(), String> {
    if let Some(min) = min
        && value < min
    {
        return Err(format!("{value} abaixo do mínimo {min}"));
    }
    if let Some(max) = max
        && value > max
    {
        return Err(format!("{value} acima do máximo {max}"));
    }
    Ok(())
}

/// `#rgb`, `#rrggbb` ou `#rrggbbaa` → `#rrggbb[aa]` minúsculo.
fn parse_color(text: &str) -> Result<String, String> {
    let hex = text
        .strip_prefix('#')
        .ok_or_else(|| format!("cor `{text}` não é `#rrggbb`"))?;
    if !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("cor `{text}` não é hexadecimal"));
    }
    match hex.len() {
        3 => Ok(format!(
            "#{}",
            hex.chars()
                .flat_map(|c| [c, c])
                .collect::<String>()
                .to_ascii_lowercase()
        )),
        6 | 8 => Ok(format!("#{}", hex.to_ascii_lowercase())),
        _ => Err(format!("cor `{text}` não é `#rrggbb`")),
    }
}

impl PropValue {
    pub fn to_json(&self) -> Value {
        match self {
            PropValue::Number(v) => json!(v),
            PropValue::Int(v) => json!(v),
            PropValue::Bool(v) => json!(v),
            PropValue::Text(v) => json!(v),
            PropValue::List(v) => json!(v),
            PropValue::Vec3(v) => json!(v),
        }
    }

    /// O valor como texto de atributo `prop-*` (o inverso de `parse_value`).
    pub fn to_attr(&self) -> String {
        match self {
            PropValue::Number(v) => v.to_string(),
            PropValue::Int(v) => v.to_string(),
            PropValue::Bool(v) => v.to_string(),
            PropValue::Text(v) => v.clone(),
            PropValue::List(v) => v.join("|"),
            PropValue::Vec3([x, y, z]) => format!("{x} {y} {z}"),
        }
    }

    /// O valor para Lua: número, inteiro, bool, string, lista (tabela 1..n) ou
    /// vec3 (`{x=, y=, z=}`).
    pub fn to_lua(&self, lua: &mlua::Lua) -> mlua::Result<mlua::Value> {
        use mlua::IntoLua;
        match self {
            PropValue::Number(v) => v.into_lua(lua),
            PropValue::Int(v) => v.into_lua(lua),
            PropValue::Bool(v) => v.into_lua(lua),
            PropValue::Text(v) => v.as_str().into_lua(lua),
            PropValue::List(items) => {
                let table = lua.create_table()?;
                for (i, item) in items.iter().enumerate() {
                    table.set(i + 1, item.as_str())?;
                }
                Ok(mlua::Value::Table(table))
            }
            PropValue::Vec3([x, y, z]) => {
                let table = lua.create_table()?;
                table.set("x", *x)?;
                table.set("y", *y)?;
                table.set("z", *z)?;
                Ok(mlua::Value::Table(table))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"
-- npc.lua
--@export speed: number(0..5) = 1.1      -- m/s a passear
--@export prompt: string = "Falar -- já"  -- texto do prompt
--@export lines: string[] = "Bom dia." | "Cuidado, lobos!"
--@export mood: enum(calm, grumpy) = calm
--@export tint: color = #C8A060
--@export home: entity
--@export offset: vec3 = 0 1 0
--@export gold: int(0..) = 25
--@export locked: bool = false
local x = 1 --@export not-at-start: number
function on_update(dt) end
"#;

    #[test]
    fn parses_every_type() {
        let exports = parse_exports(SAMPLE);
        assert!(exports.errors.is_empty(), "{:?}", exports.errors);
        let names: Vec<&str> = exports.defs.iter().map(|d| d.name.as_str()).collect();
        assert_eq!(
            names,
            [
                "speed", "prompt", "lines", "mood", "tint", "home", "offset", "gold", "locked"
            ]
        );
        let speed = exports.get("speed").unwrap();
        assert_eq!(
            speed.ty,
            PropType::Number {
                min: Some(0.0),
                max: Some(5.0)
            }
        );
        assert_eq!(speed.default, Some(PropValue::Number(1.1)));
        assert_eq!(speed.doc, "m/s a passear");
        assert_eq!(speed.line, 3);
        // `--` dentro de aspas não abre a descrição.
        let prompt = exports.get("prompt").unwrap();
        assert_eq!(prompt.default, Some(PropValue::Text("Falar -- já".into())));
        assert_eq!(prompt.doc, "texto do prompt");
        assert_eq!(
            exports.get("lines").unwrap().default,
            Some(PropValue::List(vec![
                "Bom dia.".into(),
                "Cuidado, lobos!".into()
            ]))
        );
        assert_eq!(
            exports.get("tint").unwrap().default,
            Some(PropValue::Text("#c8a060".into()))
        );
        assert_eq!(exports.get("home").unwrap().default, None);
        assert_eq!(
            exports.get("offset").unwrap().default,
            Some(PropValue::Vec3([0.0, 1.0, 0.0]))
        );
        assert_eq!(
            exports.get("gold").unwrap().ty,
            PropType::Int {
                min: Some(0),
                max: None
            }
        );
        assert_eq!(
            exports.get("locked").unwrap().default,
            Some(PropValue::Bool(false))
        );
    }

    #[test]
    fn type_roundtrips_through_display() {
        for text in [
            "number",
            "number(0..5)",
            "int(1..)",
            "int(..9)",
            "bool",
            "string",
            "string[]",
            "enum(a, b)",
            "color",
            "vec3",
            "entity",
        ] {
            assert_eq!(PropType::parse(text).unwrap().to_string(), text);
        }
    }

    #[test]
    fn signals_methods_and_connections() {
        let exports = parse_exports(
            "--@signal opened(gold, item) -- o baú abriu\n--@signal rang\n--@signal Bad\n--@signal rang\nfunction on_update(dt)\nend\nfunction alarm(a)\nend\nlocal function hidden()\nend\n  function indented()\nend\n",
        );
        assert_eq!(exports.signals.len(), 2);
        assert_eq!(exports.signals[0].args, ["gold", "item"]);
        assert_eq!(exports.signals[0].doc, "o baú abriu");
        assert!(exports.signal("rang").is_some());
        assert_eq!(exports.errors.len(), 2);
        assert_eq!(exports.methods, ["on_update", "alarm"]);
        assert_eq!(connect_signal("connect-opened"), Some("opened"));
        assert_eq!(connect_attr("rang"), "connect-rang");
        let conns = parse_connections(" porta_norte:abrir ; @guardas:alarm;").unwrap();
        assert_eq!(conns.len(), 2);
        assert_eq!(conns[0].target, ConnTarget::Name("porta_norte".into()));
        assert_eq!(conns[1].target, ConnTarget::Group("guardas".into()));
        assert_eq!(conns[1].to_string(), "@guardas:alarm");
        // nomes de entidade podem ter `:`? não — o método é o último segmento
        assert!(parse_connections("sem_metodo").is_err());
        assert!(parse_connections("x:1abc").is_err());
        assert!(parse_connections("@Mau:x").is_err());
        assert!(parse_connections("").unwrap().is_empty());
        let (groups, bad) = parse_groups("guardas, aldeia guardas  Mau");
        assert_eq!(groups, ["guardas", "aldeia"]);
        assert_eq!(bad, ["Mau"]);
    }

    #[test]
    fn rejects_bad_annotations() {
        let exports = parse_exports(
            "--@export Speed: number\n--@export x number\n--@export y: float\n--@export z: int = abc\n--@export w: enum()\n--@export a: bool\n--@export a: bool",
        );
        assert!(exports.defs.iter().all(|d| d.name == "a"));
        let lines: Vec<usize> = exports.errors.iter().map(|e| e.line).collect();
        assert_eq!(lines, [1, 2, 3, 4, 5, 7]);
        assert!(exports.errors[5].message.contains("duas vezes"));
    }

    #[test]
    fn values_respect_type_and_range() {
        let ty = PropType::parse("number(0..5)").unwrap();
        assert!(ty.parse_value("6").unwrap_err().contains("máximo"));
        assert!(ty.parse_value("abc").is_err());
        assert!(ty.parse_value("NaN").is_err());
        let int = PropType::parse("int(1..3)").unwrap();
        assert_eq!(int.parse_value("2"), Ok(PropValue::Int(2)));
        assert!(int.parse_value("2.5").is_err());
        assert!(int.parse_value("0").is_err());
        let mood = PropType::parse("enum(calm, grumpy)").unwrap();
        assert!(mood.parse_value("happy").unwrap_err().contains("calm"));
        assert_eq!(
            PropType::Color.parse_value("#abc"),
            Ok(PropValue::Text("#aabbcc".into()))
        );
        assert!(PropType::Color.parse_value("red").is_err());
        assert!(PropType::Vec3.parse_value("1 2").is_err());
        assert_eq!(
            PropType::Vec3.parse_value("1, 2, 3"),
            Ok(PropValue::Vec3([1.0, 2.0, 3.0]))
        );
        assert_eq!(
            PropType::StringList.parse_value("a| b |"),
            Ok(PropValue::List(vec!["a".into(), "b".into()]))
        );
        assert!(PropType::Bool.parse_value("talvez").is_err());
        assert!(PropType::Entity.parse_value(" ").is_err());
    }

    #[test]
    fn resolve_prefers_instance_and_falls_back_on_error() {
        let exports = parse_exports(SAMPLE);
        let props = vec![
            ("speed".to_owned(), "2".to_owned()),
            ("mood".to_owned(), "happy".to_owned()),
            ("sped".to_owned(), "3".to_owned()),
        ];
        let resolved = exports.resolve(&props);
        let speed = resolved.iter().find(|r| r.def.name == "speed").unwrap();
        assert_eq!(speed.value, Some(PropValue::Number(2.0)));
        assert_eq!(speed.source, PropSource::Instance);
        let mood = resolved.iter().find(|r| r.def.name == "mood").unwrap();
        assert_eq!(mood.value, Some(PropValue::Text("calm".into())));
        assert_eq!(mood.source, PropSource::Default);
        assert!(mood.error.is_some());
        assert_eq!(exports.unknown(&props), ["sped"]);
    }

    #[test]
    fn values_roundtrip_through_attr_text() {
        let exports = parse_exports(SAMPLE);
        for def in &exports.defs {
            if let Some(value) = &def.default {
                assert_eq!(def.ty.parse_value(&value.to_attr()).as_ref(), Ok(value));
            }
        }
    }

    #[test]
    fn prop_attr_names() {
        assert_eq!(prop_name("prop-speed"), Some("speed"));
        assert_eq!(prop_name("prop-"), None);
        assert_eq!(prop_name("speed"), None);
        assert_eq!(prop_attr("speed"), "prop-speed");
    }

    #[test]
    fn to_lua_shapes() {
        let lua = mlua::Lua::new();
        let list = PropValue::List(vec!["a".into(), "b".into()])
            .to_lua(&lua)
            .unwrap();
        let table = list.as_table().unwrap();
        assert_eq!(table.get::<String>(2).unwrap(), "b");
        let v = PropValue::Vec3([1.0, 2.0, 3.0]).to_lua(&lua).unwrap();
        assert_eq!(v.as_table().unwrap().get::<f64>("y").unwrap(), 2.0);
        assert!(PropValue::Int(3).to_lua(&lua).unwrap().is_integer());
    }
}
