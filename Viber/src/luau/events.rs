//! Fila de eventos engine→Lua ([`ScriptGameEvent`]): estado do jogo que mudou
//! FORA dos scripts. Pull model — produtores empurram para
//! [`ScriptEventQueue`], o `luau_update` faz fan-out para filas por-path e o
//! script drena com `viber.events()`.

use std::collections::HashSet;

use bevy::prelude::*;
use mlua::{Lua, Table};

use super::ctx::ScriptCtx;

/// Teto da fila de eventos por-path (`viber.events`): transbordo descarta o
/// evento NOVO (warn 1× por path) — um script que não drena não cria lag.
pub const EVENT_QUEUE_CAP: usize = 64;
/// Evento engine→Lua: estado do jogo que mudou FORA dos scripts (kill, dano
/// ao herói, level-up, cliques de UI…). É a base do desacoplamento — sem ela,
/// lógica como "ao matar X, Y acontece" só podia viver em Rust.
///
/// Pull model: os produtores empurram para [`ScriptEventQueue`]; o
/// `luau_update` fan-out para as filas por-path (cada script que chamou
/// `viber.events()` pelo menos uma vez) e o script drena com
/// `viber.events()` → array `{type=..., ...}`.
#[derive(Debug, Clone)]
pub enum ScriptGameEvent {
    /// Criatura morreu (`kill_creature` do melee, ou HP de entidade a zero
    /// por `viber.entity_damage`). `name` = nome da entidade; `entity` = bits.
    Kill { name: String, entity: i64 },
    /// O herói apanhou dano (pós i-frames; `hp` = HP após o golpe).
    PlayerHurt { amount: f32, hp: f32 },
    /// O herói morreu (a pousar no ponto de respawn).
    PlayerDied,
    /// Algo entrou no vault (`grant_loot` de colheita, `viber.vault_add` com
    /// `report_collect`, loot de chest…).
    Collect { item: String, amount: u32 },
    /// Quest entregue com sucesso (recompensas já aplicadas).
    QuestDone { id: String },
    /// Subida de nível (deteção no `vitals`).
    LevelUp { level: u32 },
    /// Ação da UI declarativa (`viber.ui.action("buy", "potion")`) — encaminhada
    /// a TODOS os scripts subscritos; os handlers nativos também correm, salvo
    /// as ações reclamadas por `viber.ui.own_action`.
    UiAction { name: String, arg: String },
}

impl ScriptGameEvent {
    /// `type` do evento em Lua.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Kill { .. } => "kill",
            Self::PlayerHurt { .. } => "player_hurt",
            Self::PlayerDied => "player_died",
            Self::Collect { .. } => "collect",
            Self::QuestDone { .. } => "quest_done",
            Self::LevelUp { .. } => "level_up",
            Self::UiAction { .. } => "ui_action",
        }
    }

    /// Converte para a tabela Lua que `viber.events()` devolve. Uma tabela
    /// NOVA por chamada (partilhar referência entre filas deixava um script
    /// mutar o evento de outro).
    pub fn to_table(&self, lua: &Lua) -> mlua::Result<Table> {
        let t = lua.create_table()?;
        t.raw_set("type", self.kind())?;
        match self {
            Self::Kill { name, entity } => {
                t.raw_set("name", name.clone())?;
                t.raw_set("entity", *entity)?;
            }
            Self::PlayerHurt { amount, hp } => {
                t.raw_set("amount", *amount)?;
                t.raw_set("hp", *hp)?;
            }
            Self::PlayerDied => {}
            Self::Collect { item, amount } => {
                t.raw_set("item", item.clone())?;
                t.raw_set("amount", *amount)?;
            }
            Self::QuestDone { id } => {
                t.raw_set("id", id.clone())?;
            }
            Self::LevelUp { level } => {
                t.raw_set("level", *level)?;
            }
            Self::UiAction { name, arg } => {
                t.raw_set("name", name.clone())?;
                t.raw_set("arg", arg.clone())?;
            }
        }
        Ok(t)
    }
}

/// Fila de entrada dos eventos engine→Lua (producers → `luau_update`). Os
/// produtores usam `Option<ResMut>`: apps mínimas de teste sem o recurso
/// continuam a compilar e a correr.
#[derive(Debug, Default, Resource)]
pub struct ScriptEventQueue(pub Vec<ScriptGameEvent>);

impl ScriptEventQueue {
    /// Atalho para produtores.
    pub fn push(&mut self, event: ScriptGameEvent) {
        self.0.push(event);
    }
}

/// Instala `viber.events()` na tabela `viber`.
pub(crate) fn install(lua: &Lua, api: &Table) -> mlua::Result<()> {
    api.set(
        "events",
        lua.create_function(|lua, ()| {
            let path = lua
                .app_data_ref::<ScriptCtx>()
                .and_then(|ctx| ctx.path.clone())
                .ok_or_else(|| {
                    mlua::Error::runtime("viber.events fora de script (sem path)")
                })?;
            let queues: Table = lua.named_registry_value("viber_events")?;
            // 1.ª chamada CRIA a fila (a presença da chave É a subscrição) —
            // erro aqui era o script nunca subscrever e nunca receber nada.
            let queue: Table = match queues.raw_get::<Table>(path.clone()) {
                Ok(q) => q,
                Err(_) => {
                    let fresh = lua.create_table()?;
                    queues.raw_set(path.clone(), fresh.clone())?;
                    fresh
                }
            };
            let count = queue.raw_len();
            let out = lua.create_table()?;
            for i in 1..=count {
                out.raw_set(i, queue.raw_get::<mlua::Value>(i)?)?;
            }
            let fresh = lua.create_table()?;
            queues.raw_set(path, fresh)?;
            Ok(out)
        })?,
    )?;
    Ok(())
}

/// Fan-out (chamado do `luau_update` ANTES dos `on_update`): cada evento vira
/// tabela Lua e entra na fila de CADA script subscrito (quem chamou
/// `viber.events()` pelo menos uma vez — a presença da chave no registry É a
/// subscrição). Transbordo (cap [`EVENT_QUEUE_CAP`]) descarta o evento NOVO e
/// avisa 1× por path — um script que não drena não cria lag na engine.
pub(crate) fn fan_out_events(
    lua: &Lua,
    incoming: &[ScriptGameEvent],
    dropped: &mut HashSet<String>,
) {
    if incoming.is_empty() {
        return;
    }
    let queues: Table = match lua.named_registry_value("viber_events") {
        Ok(t) => t,
        Err(e) => {
            warn!(target: "viber::luau", "fan-out de eventos: {e}");
            return;
        }
    };
    for pair in queues.pairs::<String, Table>() {
        let (path, queue) = match pair {
            Ok(p) => p,
            Err(e) => {
                warn!(target: "viber::luau", "fan-out de eventos: {e}");
                continue;
            }
        };
        for event in incoming {
            if queue.raw_len() >= EVENT_QUEUE_CAP {
                if dropped.insert(path.clone()) {
                    warn!(target: "viber::luau",
                        "viber.events: fila de '{path}' cheia ({EVENT_QUEUE_CAP}) — a descartar eventos novos");
                }
                break;
            }
            let Ok(table) = event.to_table(lua) else {
                continue;
            };
            if let Err(e) = queue.push(table) {
                warn!(target: "viber::luau", "fan-out de eventos: {e}");
                break;
            }
        }
    }
}
