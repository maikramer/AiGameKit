//! Estado de JOGO world-scoped e módulos partilhados:
//! - `viber.game()` → tabela ÚNICA por mundo (globals resetam no hot-reload;
//!   `game()` sobrevive, como `viber.state()` por entidade). É aqui que a
//!   lógica de jogo em Lua guarda o que era recurso Rust noutra vida.
//! - `viber.load("lib/x.lua")` → corre um chunk de `scripts/` em env próprio
//!   UMA vez por mundo e devolve o seu `return` (cache em `viber_modules`) —
//!   a base para fatorar FSM/bibliotecas partilhadas entre scripts.

use mlua::{Lua, Table};
use mlua::IntoLua;

use super::ctx::ScriptCtx;

/// Instala o grupo `game` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    // viber.game() -> tabela world-scoped.
    api.set(
        "game",
        lua.create_function(|lua, ()| {
            let game: Table = lua.named_registry_value("viber_game")?;
            Ok(game)
        })?,
    )?;

    // viber.load(path) -> valor devolvido pelo módulo (cacheado por path).
    api.set(
        "load",
        lua.create_function(|lua, path: String| {
            // Só caminhos RELATIVOS dentro de `scripts/` — `..` ou raiz
            // absoluta liam (e executavam) qualquer ficheiro do disco.
            let relative = std::path::Path::new(&path);
            let inside = relative.components().all(|c| {
                matches!(
                    c,
                    std::path::Component::Normal(_) | std::path::Component::CurDir
                )
            });
            if path.is_empty() || !inside {
                return Err(mlua::Error::runtime(format!(
                    "viber.load('{path}'): só caminhos relativos a scripts/ (sem '..')"
                )));
            }
            let modules: Table = lua.named_registry_value("viber_modules")?;
            if let Ok(cached) = modules.raw_get::<mlua::Value>(path.clone()) {
                if !matches!(cached, mlua::Value::Nil) {
                    return Ok(cached);
                }
            }
            let dir = lua
                .app_data_ref::<ScriptCtx>()
                .and_then(|ctx| ctx.scripts_dir.clone())
                .ok_or_else(|| mlua::Error::runtime("viber.load sem scripts_dir"))?;
            let full = dir.join(&path);
            let code = std::fs::read_to_string(&full).map_err(|e| {
                mlua::Error::runtime(format!("viber.load('{path}'): {e}"))
            })?;
            // Env sandbox próprio (mesma forma do chunk de entidade) — o
            // módulo não pode clobber globals de quem o carrega.
            let env = lua.create_table()?;
            let mt = lua.create_table()?;
            mt.set("__index", lua.globals())?;
            env.set_metatable(Some(mt));
            let chunk = lua
                .load(&code)
                .set_name(format!("={path}"))
                .set_environment(env)
                .into_function()?;
            let value: mlua::Value = chunk.call(())?;
            // Módulo sem `return` (só mexe em `viber.game()`) cacheia `true`,
            // como o `require` — com `nil` o cache falhava e o módulo
            // re-corria a CADA chamada.
            let value = match value {
                mlua::Value::Nil => mlua::Value::Boolean(true),
                other => other,
            };
            modules.raw_set(path, value.clone())?;
            Ok(value)
        })?,
    )?;
    Ok(())
}

/// Serializa o estado de jogo (`viber_game`) para JSON PLANO — só valores
/// primitivos (string/número/bool) são persistíveis; tabelas/funções são
/// IGNORADAS silenciosamente (o save é do jogo, não da VM).
pub fn game_to_json(lua: &Lua) -> std::collections::BTreeMap<String, serde_json::Value> {
    let mut out = std::collections::BTreeMap::new();
    let Ok(game) = lua.named_registry_value::<Table>("viber_game") else {
        return out;
    };
    for pair in game.pairs::<String, mlua::Value>().flatten() {
        let value = match pair.1 {
            mlua::Value::Boolean(b) => serde_json::Value::Bool(b),
            mlua::Value::Integer(i) => serde_json::Value::Number(i.into()),
            mlua::Value::Number(n) => match serde_json::Number::from_f64(n) {
                Some(num) => serde_json::Value::Number(num),
                None => continue,
            },
            mlua::Value::String(st) => serde_json::Value::String(st.to_string_lossy()),
            _ => continue,
        };
        out.insert(pair.0, value);
    }
    out
}

/// Repõe o JSON do save no `viber_game` (load). Chaves não primitivas no
/// ficheiro (editado à mão) são ignoradas — nunca partem o load.
pub fn json_to_game(lua: &Lua, kv: &std::collections::BTreeMap<String, serde_json::Value>) {
    let Ok(game) = lua.named_registry_value::<Table>("viber_game") else {
        return;
    };
    for (key, value) in kv {
        let lv = match value {
            serde_json::Value::Bool(b) => (*b).into_lua(lua),
            serde_json::Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    (i as f64).into_lua(lua)
                } else {
                    n.as_f64().unwrap_or(0.0).into_lua(lua)
                }
            }
            serde_json::Value::String(st) => st.clone().into_lua(lua),
            _ => continue,
        };
        if let Ok(lv) = lv {
            let _ = game.raw_set(key.clone(), lv);
        }
    }
}
