//! Props exportadas por instância: `viber.prop(nome)` / `viber.props()`.
//!
//! O script declara o esquema com `--@export` ([`super::exports`]); cada
//! entidade guarda os seus valores em `prop-<nome>` ([`super::ScriptProps`]).
//! O valor devolvido é o da instância, senão o default da anotação, senão
//! `nil`. O top-level corre UMA vez por ficheiro (partilhado por todas as
//! instâncias), por isso ler props aí é erro — o sítio é o `on_update`/hooks.

use std::sync::Arc;

use mlua::{Lua, Table};

use super::ctx::ScriptCtx;
use super::exports::{ResolvedProp, ScriptExports};

/// Instala `viber.prop` e `viber.props` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    // viber.prop(nome) -> valor tipado | nil
    api.set(
        "prop",
        lua.create_function(|lua, name: String| {
            let (exports, props, path) = context(lua, "viber.prop")?;
            if exports.get(&name).is_none() {
                return Err(mlua::Error::runtime(format!(
                    "viber.prop: '{name}' não é exportado por {path} (declare `--@export {name}: <tipo> = <default>`)"
                )));
            }
            let resolved = exports.resolve(&props);
            let Some(prop) = resolved.into_iter().find(|r| r.def.name == name) else {
                return Ok(mlua::Value::Nil);
            };
            warn_invalid(lua, &path, &prop);
            match prop.value {
                Some(value) => value.to_lua(lua),
                None => Ok(mlua::Value::Nil),
            }
        })?,
    )?;

    // viber.props() -> { nome = valor, … } (todos os exports com valor)
    api.set(
        "props",
        lua.create_function(|lua, ()| {
            let (exports, props, path) = context(lua, "viber.props")?;
            let table = lua.create_table()?;
            for prop in exports.resolve(&props) {
                warn_invalid(lua, &path, &prop);
                if let Some(value) = &prop.value {
                    table.set(prop.def.name.as_str(), value.to_lua(lua)?)?;
                }
            }
            Ok(table)
        })?,
    )?;
    Ok(())
}

type PropContext = (Arc<ScriptExports>, Arc<Vec<(String, String)>>, String);

fn context(lua: &Lua, func: &str) -> mlua::Result<PropContext> {
    let ctx = lua
        .app_data_ref::<ScriptCtx>()
        .ok_or_else(|| mlua::Error::runtime(format!("{func}: sem contexto de script")))?;
    if ctx.top_level {
        return Err(mlua::Error::runtime(format!(
            "{func}: o top-level é partilhado por todas as instâncias — leia props no on_update"
        )));
    }
    let path = ctx
        .path
        .clone()
        .ok_or_else(|| mlua::Error::runtime(format!("{func}: fora de um script")))?;
    let exports = ctx.exports.get(&path).cloned().unwrap_or_default();
    let props = ctx.self_props.clone().unwrap_or_default();
    Ok((exports, props, path))
}

/// Um `prop-*` inválido cai para o default — avisa 1× por script+prop+valor.
fn warn_invalid(lua: &Lua, path: &str, prop: &ResolvedProp<'_>) {
    let Some(error) = &prop.error else {
        return;
    };
    let key = format!("{path}:{}:{error}", prop.def.name);
    if let Some(mut ctx) = lua.app_data_mut::<ScriptCtx>()
        && ctx.prop_warned.insert(key)
    {
        bevy::log::warn!(
            "[luau] {path}: prop-{} inválido ({error}) — a usar o default",
            prop.def.name
        );
    }
}
