//! Vitais GENÉRICAS de entidade e procura por nome: `viber.entity_hp`,
//! `viber.entity_damage`, `viber.entity_heal`, `viber.entity_set_max_hp`,
//! `viber.find`/`viber.find_all`. É o que deixa um jogo fazer o SEU combate
//! em Lua sem recorrer ao melee nativo nem à bridge de debug.
//!
//! Leitura por snapshot (`ctx.hp_snapshot`, sem head de frame); escrita por
//! comando (aplicada pós-frame — o HP chega às leituras no frame seguinte,
//! igual ao resto da API). HP ≤ 0 por `entity_damage` EMITE `Kill` e NÃO
//! corre o caminho nativo (cadáver/XP/quests são do melee) — o script decide
//! (topple/despawn/eventos).

use mlua::{Lua, Table};
use mlua::IntoLua;

use super::commands::ScriptCommand;
use super::ctx::ScriptCtx;

/// Instala o grupo `entity` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    // viber.entity_hp(id?) -> ok, cur, max — snapshot do frame. Sem id = self.
    api.set(
        "entity_hp",
        lua.create_function(|lua, id: Option<i64>| {
            let ctx = lua.app_data_ref::<ScriptCtx>();
            let Some(ctx) = ctx.as_ref() else {
                return Ok((false, 0.0, 0.0));
            };
            let bits = match id {
                Some(bits) => bits,
                None => match ctx.entity {
                    Some(e) => e.to_bits() as i64,
                    None => return Ok((false, 0.0, 0.0)),
                },
            };
            match ctx.hp_snapshot.get(&bits) {
                Some((cur, max)) => Ok((true, *cur, *max)),
                None => Ok((false, 0.0, 0.0)),
            }
        })?,
    )?;

    // viber.entity_set_max_hp(max, id?) — cria o `Health` se faltar
    // (current = max); numa entidade existente redimensiona (current clampe).
    api.set(
        "entity_set_max_hp",
        lua.create_function(|lua, (max, id): (f32, Option<i64>)| {
            if !max.is_finite() || max <= 0.0 {
                return Err(mlua::Error::runtime(
                    "viber.entity_set_max_hp: max tem de ser finito e > 0",
                ));
            }
            let entity = resolve_entity(lua, id)?;
            lua.app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                .commands
                .push(ScriptCommand::EntitySetMaxHp { entity, max });
            Ok(())
        })?,
    )?;

    // viber.entity_damage(amount, id?) — dano direto (sem i-frames: esses são
    // do path do player). Sem `Health` = no-op (warn 1× na aplicação).
    api.set(
        "entity_damage",
        lua.create_function(|lua, (amount, id): (f32, Option<i64>)| {
            if !amount.is_finite() {
                return Err(mlua::Error::runtime(
                    "viber.entity_damage: amount não finito (NaN/inf)",
                ));
            }
            let entity = resolve_entity(lua, id)?;
            lua.app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                .commands
                .push(ScriptCommand::EntityDamage { entity, amount });
            Ok(())
        })?,
    )?;

    // viber.entity_heal(amount, id?)
    api.set(
        "entity_heal",
        lua.create_function(|lua, (amount, id): (f32, Option<i64>)| {
            if !amount.is_finite() {
                return Err(mlua::Error::runtime(
                    "viber.entity_heal: amount não finito (NaN/inf)",
                ));
            }
            let entity = resolve_entity(lua, id)?;
            lua.app_data_mut::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new")
                .commands
                .push(ScriptCommand::EntityHeal { entity, amount });
            Ok(())
        })?,
    )?;

    // viber.find(nome) -> bits | nil — exato primeiro, substring depois
    // (mesma semântica do `viber.debug.find`).
    api.set(
        "find",
        lua.create_function(|lua, name: String| {
            let ctx = lua.app_data_ref::<ScriptCtx>();
            let Some(ctx) = ctx.as_ref() else {
                return Ok(mlua::Value::Nil);
            };
            if let Some(bits) = exact_or_substring(&ctx.named_entities, &name) {
                // i64 via IntoLua (Luau não tem inteiros — o Value::Integer
                // do mlua é i32 e os bits podem excedê-lo).
                return Ok(bits.first().copied().unwrap_or(0).into_lua(lua)?);
            }
            Ok(mlua::Value::Nil)
        })?,
    )?;

    // viber.find_all(nome) -> {bits...} — TODAS as correspondências.
    api.set(
        "find_all",
        lua.create_function(|lua, name: String| {
            let ctx = lua.app_data_ref::<ScriptCtx>();
            let out = lua.create_table()?;
            let Some(ctx) = ctx.as_ref() else {
                return Ok(out);
            };
            let mut i = 1u32;
            for key in ctx.named_entities.keys() {
                if key == &name || key.contains(&name) {
                    for bits in &ctx.named_entities[key] {
                        out.raw_set(i, *bits)?;
                        i += 1;
                    }
                }
            }
            Ok(out)
        })?,
    )?;
    Ok(())
}

/// Bits da entidade alvo: `id` explícito OU a entidade corrente.
fn resolve_entity(lua: &Lua, id: Option<i64>) -> mlua::Result<bevy::prelude::Entity> {
    match id {
        Some(bits) => Ok(bevy::prelude::Entity::from_bits(bits as u64)),
        None => lua
            .app_data_ref::<ScriptCtx>()
            .and_then(|ctx| ctx.entity)
            .ok_or_else(|| mlua::Error::runtime("viber.entity_* fora de on_update (sem id)")),
    }
}

/// Chave exata primeiro; senão a primeira que contém (ordem de inserção —
/// HashMap é aleatório, mas o snapshot é reconstruído por frame e o teste
/// de proximidade do chamador decide o resto).
fn exact_or_substring<'a>(
    map: &'a std::collections::HashMap<String, Vec<i64>>,
    name: &str,
) -> Option<&'a Vec<i64>> {
    if let Some(v) = map.get(name) {
        return Some(v);
    }
    map.iter()
        .find(|(k, _)| k.contains(name))
        .map(|(_, v)| v)
}
