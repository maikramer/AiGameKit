//! Grupo `viber.terrain` — edições VIVAS do terreno (Fase 3).
//!
//! Os scripts enfileiram [`crate::terrain::delta::TerrainEdit`]s (o mesmo
//! padrão de todos os setters — nada muda o mundo durante o `on_update`);
//! o `apply_terrain_edits` aplica-as ao overlay no PreUpdate seguinte (no
//! máximo `EDITS_PER_FRAME` por frame) e o plugin de LOD re-mesha as
//! colunas afetadas — mesh E collider trimesh, pelo swap atómico de sempre.
//!
//! Leituras: `viber.ground_below`/`viber.ground_at` já veem as edições (o
//! `TerrainReader` leva o overlay); `viber.terrain.revision()` diz se o
//! mundo mudou desde a última consulta.
//!
//! Determinismo: o bootstrap continua determinístico; as edições são
//! runtime por definição e NÃO entram no save nesta versão.

use mlua::{Lua, Table, Value};

use super::commands::ScriptCommand;
use super::ctx::ScriptCtx;
use crate::terrain::delta::{EDIT_MAX_RADIUS, TerrainEdit};

/// Instala o grupo `terrain` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    let terrain = lua.create_table()?;

    /// Enfileira uma edição (o `apply_terrain_edits` aplica-a no frame
    /// seguinte). Devolve `true` quando o pedido entrou na fila.
    fn enqueue(lua: &Lua, edit: TerrainEdit) -> mlua::Result<bool> {
        let mut ctx = lua
            .app_data_mut::<ScriptCtx>()
            .expect("ScriptCtx app data seeded in LuaScriptHost::new");
        ctx.commands.push(ScriptCommand::TerrainEdit { edit });
        Ok(true)
    }

    terrain.set(
        "lower",
        lua.create_function(|lua, (x, z, radius, depth): (f32, f32, f32, f32)| {
            enqueue(
                lua,
                TerrainEdit::Lower {
                    at: bevy::math::Vec2::new(x, z),
                    radius,
                    depth,
                },
            )
        })?,
    )?;
    terrain.set(
        "raise",
        lua.create_function(|lua, (x, z, radius, height): (f32, f32, f32, f32)| {
            enqueue(
                lua,
                TerrainEdit::Raise {
                    at: bevy::math::Vec2::new(x, z),
                    radius,
                    height,
                },
            )
        })?,
    )?;
    terrain.set(
        "flatten",
        lua.create_function(
            |lua, (x, z, radius, height): (f32, f32, f32, Value)| {
                let h = match height {
                    Value::Nil => None,
                    other => Some(other.as_f32().ok_or_else(|| {
                        mlua::Error::runtime("viber.terrain.flatten: height deve ser número ou nil")
                    })?),
                };
                enqueue(
                    lua,
                    TerrainEdit::Flatten {
                        at: bevy::math::Vec2::new(x, z),
                        radius,
                        height: h,
                    },
                )
            },
        )?,
    )?;
    terrain.set(
        "crater",
        lua.create_function(|lua, (x, z, radius, depth): (f32, f32, f32, f32)| {
            enqueue(
                lua,
                TerrainEdit::Crater {
                    at: bevy::math::Vec2::new(x, z),
                    radius,
                    depth,
                },
            )
        })?,
    )?;

    // viber.terrain.revision() -> u64: a revisão do overlay. Um script que
    // edita e quer saber quando o MUNDO já reflecte a edição compara-a.
    terrain.set(
        "revision",
        lua.create_function(|lua, ()| {
            let ctx = lua
                .app_data_ref::<ScriptCtx>()
                .expect("ScriptCtx app data seeded in LuaScriptHost::new");
            Ok(ctx
                .terrain
                .as_ref()
                .map(|t| t.deltas.revision())
                .unwrap_or(0))
        })?,
    )?;

    // viber.terrain.max_radius() -> m: o teto que a engine aplica — um
    // script que pinte áreas grandes sabe onde está o limite SEM descobri-lo
    // por tentativa.
    terrain.set(
        "max_radius",
        lua.create_function(|_, ()| Ok(EDIT_MAX_RADIUS))?,
    )?;

    api.set("terrain", terrain)?;
    Ok(())
}
