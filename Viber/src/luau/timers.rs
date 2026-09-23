//! Timers para scripts: `viber.after(secs, fn)`, `viber.every(secs, fn)` e
//! `viber.timer_cancel(handle)`. Substitui o padrão manual `st.t += dt`
//! repetido em dezenas de scripts.
//!
//! Estado em registos LUA (não Rust): `viber_timers` (id → entrada),
//! `viber_timer_seq` (contador de handles) e `viber_timer_cancelled` — assim
//! as closures `viber.*` (que só têm a VM) registam e cancelam sem tocar no
//! host, e o tick corre do lado Rust no início do `luau_update`. Timers são
//! world-scoped: correm independentemente do raio de ativação da entidade
//! (donos congelados continuam com timers a vencer — documentado).

use bevy::prelude::*;
use mlua::{Function, Lua, Table};

use super::ctx::ScriptCtx;

/// Instala `viber.after`/`viber.every`/`viber.timer_cancel` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    let register = |periodic: bool| {
        lua.create_function(
            move |lua, (secs, func): (f64, Function)| {
                if !secs.is_finite() || secs < 0.0 {
                    return Err(mlua::Error::runtime(
                        "viber.after/every: segundos têm de ser finitos e >= 0",
                    ));
                }
                let (owner, path, elapsed) = {
                    let ctx = lua
                        .app_data_ref::<ScriptCtx>()
                        .ok_or_else(|| mlua::Error::runtime("viber.after fora de script"))?;
                    (
                        ctx.entity
                            .ok_or_else(|| mlua::Error::runtime("viber.after fora de on_update"))?,
                        ctx.path.clone().unwrap_or_default(),
                        ctx.elapsed,
                    )
                };
                let timers: Table = lua.named_registry_value("viber_timers")?;
                let seq: Table = lua.named_registry_value("viber_timer_seq")?;
                let id: i64 = seq.raw_get("next")?;
                seq.raw_set("next", id + 1)?;
                let entry = lua.create_table()?;
                entry.raw_set("at", elapsed + secs)?;
                if periodic {
                    entry.raw_set("period", secs)?;
                }
                entry.raw_set("func", func)?;
                entry.raw_set("owner", owner.to_bits() as i64)?;
                entry.raw_set("path", path)?;
                timers.raw_set(id, entry)?;
                Ok(id)
            },
        )
    };
    api.set("after", register(false)?)?;
    api.set("every", register(true)?)?;
    // Cancela um handle (devolve true se a entrada existia). Marca SEMPRE no
    // registry de cancelados: o tick pode já ter recolhido a entrada (um
    // `viber.every` cancelado DENTRO da própria callback não renasce).
    api.set(
        "timer_cancel",
        lua.create_function(|lua, id: i64| {
            let timers: Table = lua.named_registry_value("viber_timers")?;
            let existed = timers.raw_get::<Table>(id).is_ok();
            let _ = timers.raw_remove(id);
            let cancelled: Table = lua.named_registry_value("viber_timer_cancelled")?;
            cancelled.raw_set(id, true)?;
            Ok(existed)
        })?,
    )?;
    Ok(())
}

/// Handles dos timers pendentes que satisfazem `pred(owner_bits, path)`.
pub(crate) fn timer_ids_where(lua: &Lua, pred: impl Fn(i64, &str) -> bool) -> Vec<i64> {
    let Ok(timers) = lua.named_registry_value::<Table>("viber_timers") else {
        return Vec::new();
    };
    timers
        .pairs::<i64, Table>()
        .flatten()
        .filter(|(_, entry)| {
            let owner: i64 = entry.raw_get("owner").unwrap_or(0);
            let path: String = entry.raw_get("path").unwrap_or_default();
            pred(owner, &path)
        })
        .map(|(id, _)| id)
        .collect()
}

/// Remove os timers `ids` da fila (e marca-os cancelados, caso o tick do
/// frame já os tenha recolhido).
pub(crate) fn drop_timers(lua: &Lua, ids: &[i64]) {
    let Ok(timers) = lua.named_registry_value::<Table>("viber_timers") else {
        return;
    };
    let cancelled = lua.named_registry_value::<Table>("viber_timer_cancelled").ok();
    for id in ids {
        let _ = timers.raw_remove(*id);
        if let Some(cancelled) = &cancelled {
            let _ = cancelled.raw_set(*id, true);
        }
    }
}

/// Snapshot do frame para as callbacks dos timers — o mesmo que o
/// `run_update` semeia (sem ele, a callback via o `origin`/`dt` da ÚLTIMA
/// entidade do frame anterior e `viber.position()`/`move_towards` calculavam
/// a partir de outra criatura).
pub struct TickFrame<F: Fn(Entity) -> Option<Vec3>> {
    pub elapsed: f64,
    pub dt: f32,
    pub player: Option<Vec3>,
    /// Posição no mundo do dono; `None` = dono morto (o timer é descartado).
    pub origin_of: F,
}

/// Tick (início do `luau_update`, ANTES dos `on_update`): executa os timers
/// vencidos com o ctx seedado ao dono (o `viber.state()` funciona dentro da
/// callback). Erros são pcall-style (`warn_once` por path) — um timer a falhar
/// nunca derruba o frame; `every` cancelado não re-agenda.
pub fn tick<F: Fn(Entity) -> Option<Vec3>>(host: &mut super::host::LuaScriptHost, frame: TickFrame<F>) {
    let elapsed = frame.elapsed;
    let lua = host.lua.clone();
    let Ok(timers) = lua.named_registry_value::<Table>("viber_timers") else {
        return;
    };
    let Ok(cancelled) = lua.named_registry_value::<Table>("viber_timer_cancelled") else {
        return;
    };
    // Fase 1: recolhe os vencidos (a iteração `pairs` tem de acabar antes de
    // as callbacks correrem — podem registar novos timers).
    let pairs: Vec<(i64, Table)> = timers.pairs::<i64, Table>().flatten().collect();
    let mut due: Vec<(i64, f64, Option<f64>, Function, i64, String)> = Vec::new();
    for (id, entry) in pairs {
        let at: f64 = entry.raw_get("at").unwrap_or(f64::INFINITY);
        if at > elapsed {
            continue;
        }
        let period: Option<f64> = entry.raw_get("period").ok();
        let Ok(func) = entry.raw_get::<Function>("func") else {
            let _ = timers.raw_remove(id);
            continue;
        };
        let owner: i64 = entry.raw_get("owner").unwrap_or(0);
        let path: String = entry.raw_get("path").unwrap_or_default();
        let _ = timers.raw_remove(id);
        due.push((id, at, period, func, owner, path));
    }
    // Fase 2: executa (a entrada já saiu da tabela — cancelamentos posteriores
    // não a encontram; o marker em `viber_timer_cancelled` cobre o resto).
    for (id, at, period, func, owner, path) in due {
        if cancelled.raw_get::<bool>(id).unwrap_or(false) {
            let _ = cancelled.raw_remove(id);
            continue;
        }
        // Dono despawnado: o timer morre com ele (um `every` órfão corria
        // para sempre e enfileirava comandos contra uma entidade morta).
        let Some(entity) = Entity::try_from_bits(owner as u64) else {
            continue;
        };
        let Some(origin) = (frame.origin_of)(entity) else {
            continue;
        };
        if let Some(mut ctx) = lua.app_data_mut::<ScriptCtx>() {
            ctx.entity = Some(entity);
            ctx.path = Some(path.clone());
            ctx.origin = origin;
            ctx.player = frame.player;
            ctx.dt = frame.dt;
            ctx.elapsed = elapsed;
        }
        let result = {
            let _budget = host.budget_guard();
            func.call::<()>(())
        };
        if let Err(e) = result {
            host.warn_once(&path, &e);
        }
        if let Some(p) = period {
            // Re-agenda salvo cancelado DURANTE a própria callback.
            if cancelled.raw_get::<bool>(id).unwrap_or(false) {
                let _ = cancelled.raw_remove(id);
                continue;
            }
            // Cadência presa ao agendamento (`at + p`), não ao frame em que
            // venceu — `elapsed + p` somava o atraso do frame a cada disparo.
            // Depois de um engasgo longo retoma a partir de agora (sem rajada).
            let next = if at + p > elapsed { at + p } else { elapsed + p };
            if let Ok(entry) = lua.create_table() {
                let _ = entry.raw_set("at", next);
                let _ = entry.raw_set("period", p);
                let _ = entry.raw_set("func", func.clone());
                let _ = entry.raw_set("owner", owner);
                let _ = entry.raw_set("path", path.clone());
                let _ = timers.raw_set(id, entry);
            }
        }
    }
}
