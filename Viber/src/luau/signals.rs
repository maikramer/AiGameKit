//! Sinais e grupos — o par `signal`/`groups` do Godot, com as ligações como DADO.
//!
//! * `viber.emit(sinal, …)`: o script dispara um sinal que declarou
//!   (`--@signal aberto(ouro)`); quem ouve vem dos `connect-<sinal>` do nó emissor
//!   (`connect-aberto="porta_norte:abrir; @guardas:alarme"`), não do código.
//! * `viber.call_group(grupo, método, …)`: chama `método` em todos os membros.
//! * `viber.group(nome)` / `viber.in_group(nome[, id])`: membros de `groups="…"`.
//! * `viber.sender()`: dentro de um handler, quem emitiu (bits) — `nil` fora.
//!
//! As chamadas ficam numa fila (tabela Lua no registry, os argumentos ficam
//! Lua) e são entregues pelo `luau_update` depois dos `on_update` — no MESMO
//! frame, com o contexto do ALVO (`viber.position()`, `viber.prop` são dele).
//! Um handler pode emitir outra vez; cada frame entrega no máximo
//! [`MAX_SIGNAL_ROUNDS`] vagas e o resto fica na fila para o frame seguinte (um
//! ciclo A→B→A não congela a engine).

use mlua::{IntoLua, Lua, MultiValue, Table, Value};

use super::ctx::ScriptCtx;

/// Vagas de entrega por frame (sinais emitidos por handlers = vaga seguinte).
pub const MAX_SIGNAL_ROUNDS: usize = 8;
/// Teto da fila (um script que emite em loop não cresce a memória sem fim).
pub const SIGNAL_QUEUE_CAP: usize = 1024;
const QUEUE_KEY: &str = "viber_signal_queue";

/// Uma chamada pendente.
#[derive(Debug, Clone)]
pub struct QueuedCall {
    /// Emissor (bits) — o `viber.sender()` do handler.
    pub from: Option<i64>,
    pub kind: CallKind,
    /// `table.pack(...)` dos argumentos.
    pub args: Table,
}

#[derive(Debug, Clone, PartialEq)]
pub enum CallKind {
    /// `viber.emit(signal, …)` — os alvos vêm do `SignalConnections` do emissor.
    Emit { signal: String },
    /// `viber.call_group(group, method, …)`.
    Group { group: String, method: String },
}

pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    lua.set_named_registry_value(QUEUE_KEY, lua.create_table()?)?;

    // viber.emit(sinal, ...) — só sinais declarados com `--@signal`.
    api.set(
        "emit",
        lua.create_function(|lua, (signal, args): (String, MultiValue)| {
            let (from, path, declared) = {
                let ctx = lua
                    .app_data_ref::<ScriptCtx>()
                    .ok_or_else(|| mlua::Error::runtime("viber.emit: sem contexto de script"))?;
                let path = ctx.path.clone().unwrap_or_default();
                let declared = ctx
                    .exports
                    .get(&path)
                    .is_some_and(|exports| exports.signal(&signal).is_some());
                (ctx.entity.map(|e| e.to_bits() as i64), path, declared)
            };
            if !declared {
                return Err(mlua::Error::runtime(format!(
                    "viber.emit: '{signal}' não é um sinal de {path} (declare `--@signal {signal}(args)`)"
                )));
            }
            let Some(from) = from else {
                return Err(mlua::Error::runtime(
                    "viber.emit: fora de uma entidade (o top-level não emite)",
                ));
            };
            push(lua, Some(from), "signal", &signal, None, args)
        })?,
    )?;

    // viber.call_group(grupo, método, ...)
    api.set(
        "call_group",
        lua.create_function(|lua, (group, method, args): (String, String, MultiValue)| {
            let from = lua
                .app_data_ref::<ScriptCtx>()
                .and_then(|ctx| ctx.entity)
                .map(|e| e.to_bits() as i64);
            push(lua, from, "group", &group, Some(&method), args)
        })?,
    )?;

    // viber.group(nome) -> {bits…} (ordem estável)
    api.set(
        "group",
        lua.create_function(|lua, name: String| {
            let out = lua.create_table()?;
            if let Some(ctx) = lua.app_data_ref::<ScriptCtx>()
                && let Some(members) = ctx.groups.get(&name)
            {
                for (i, bits) in members.iter().enumerate() {
                    out.raw_set(i + 1, *bits)?;
                }
            }
            Ok(out)
        })?,
    )?;

    // viber.in_group(nome[, id]) -> bool (sem id: a entidade corrente)
    api.set(
        "in_group",
        lua.create_function(|lua, (name, id): (String, Option<i64>)| {
            let Some(ctx) = lua.app_data_ref::<ScriptCtx>() else {
                return Ok(false);
            };
            let Some(bits) = id.or(ctx.entity.map(|e| e.to_bits() as i64)) else {
                return Ok(false);
            };
            Ok(ctx
                .groups
                .get(&name)
                .is_some_and(|members| members.binary_search(&bits).is_ok()))
        })?,
    )?;

    // viber.sender() -> bits | nil
    api.set(
        "sender",
        lua.create_function(|lua, ()| {
            let sender = lua.app_data_ref::<ScriptCtx>().and_then(|ctx| ctx.sender);
            match sender {
                Some(bits) => bits.into_lua(lua),
                None => Ok(Value::Nil),
            }
        })?,
    )?;
    Ok(())
}

fn push(
    lua: &Lua,
    from: Option<i64>,
    kind: &str,
    name: &str,
    method: Option<&str>,
    args: MultiValue,
) -> mlua::Result<()> {
    let queue: Table = lua.named_registry_value(QUEUE_KEY)?;
    if queue.raw_len() >= SIGNAL_QUEUE_CAP {
        return Err(mlua::Error::runtime(format!(
            "fila de sinais cheia ({SIGNAL_QUEUE_CAP}) — um sinal a emitir-se em ciclo?"
        )));
    }
    let entry = lua.create_table()?;
    if let Some(from) = from {
        entry.raw_set("from", from)?;
    }
    entry.raw_set("kind", kind)?;
    entry.raw_set("name", name)?;
    if let Some(method) = method {
        entry.raw_set("method", method)?;
    }
    let packed = lua.create_table()?;
    let count = args.len();
    for (i, value) in args.into_iter().enumerate() {
        packed.raw_set(i + 1, value)?;
    }
    packed.raw_set("n", count)?;
    entry.raw_set("args", packed)?;
    queue.raw_push(entry)
}

/// Tira TODA a fila (a entrega pode encher a próxima vaga).
pub fn drain(lua: &Lua) -> Vec<QueuedCall> {
    let Ok(queue) = lua.named_registry_value::<Table>(QUEUE_KEY) else {
        return Vec::new();
    };
    if queue.raw_len() == 0 {
        return Vec::new();
    }
    let Ok(fresh) = lua.create_table() else {
        return Vec::new();
    };
    let _ = lua.set_named_registry_value(QUEUE_KEY, fresh);
    queue
        .sequence_values::<Table>()
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let from = entry.raw_get::<Option<i64>>("from").ok().flatten();
            let kind: String = entry.raw_get("kind").ok()?;
            let name: String = entry.raw_get("name").ok()?;
            let args: Table = entry.raw_get("args").ok()?;
            let kind = match kind.as_str() {
                "signal" => CallKind::Emit { signal: name },
                _ => CallKind::Group {
                    group: name,
                    method: entry.raw_get("method").ok()?,
                },
            };
            Some(QueuedCall { from, kind, args })
        })
        .collect()
}

/// Os argumentos empacotados como valores de chamada.
pub fn unpack(args: &Table) -> MultiValue {
    let count: usize = args.raw_get("n").unwrap_or(0);
    (1..=count)
        .map(|i| args.raw_get::<Value>(i).unwrap_or(Value::Nil))
        .collect()
}
