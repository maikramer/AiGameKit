//! Contexto injectado por chamada ([`ScriptCtx`] como app data da VM): o
//! snapshot de início-de-frame que as closures `viber.*` leem (entidade,
//! posições, teclas, quest/vault, terreno) e onde os comandos se acumulam.
//! Inclui os registos de superfície partilhados (estradas/água).

use std::collections::HashMap;
use std::sync::Arc;

use bevy::prelude::*;

use crate::terrain::roads::RoadPath;
use crate::terrain::runtime::TerrainReader;
use crate::terrain::water::WaterBody;

use super::commands::ScriptCommand;

/// Per-call context injected into the shared VM (Lua app data) by
/// [`luau_update`]. The `viber` closures read the snapshot and queue commands.
#[derive(Default, Debug)]
pub struct ScriptCtx {
    /// Entity currently running `on_update` (None outside script calls).
    pub entity: Option<Entity>,
    /// Path do script em execução (`viber.events` subscreve por path).
    pub path: Option<String>,
    /// Position snapshot of the current entity (start of frame).
    pub origin: Vec3,
    /// Player position snapshot, when a [`crate::player::Player`] exists.
    pub player: Option<Vec3>,
    /// Seconds since engine startup.
    pub elapsed: f64,
    /// Delta time passed to `on_update`.
    pub dt: f32,
    /// Queued `set_position` commands, drained by [`luau_update`].
    pub pending: Vec<(Entity, Vec3)>,
    /// Queued [`ScriptCommand`]s (drained e aplicado pós-frame).
    pub commands: Vec<ScriptCommand>,
    /// Teclas pressionadas neste frame (para `viber.interacted`).
    pub just_pressed: Vec<KeyCode>,
    /// Teclas PRESSIONADAS (held) neste frame (`viber.input.down`).
    pub keys_down: Vec<KeyCode>,
    /// Teclas largadas NESTE frame (`viber.input.released`).
    pub keys_released: Vec<KeyCode>,
    /// Rato just-pressed / held / just-released neste frame (`viber.input.*`
    /// com `"mouse1"`). Vazio quando não há `ButtonInput<MouseButton>` (apps
    /// mínimas de teste).
    pub mouse_pressed: Vec<bevy::input::mouse::MouseButton>,
    pub mouse_down: Vec<bevy::input::mouse::MouseButton>,
    pub mouse_released: Vec<bevy::input::mouse::MouseButton>,
    /// Snapshot `nome → bits` das entidades nomeadas do mundo (cap
    /// NAMED_SNAPSHOT_CAP) para `viber.find`/`viber.find_all`.
    pub named_entities: std::collections::HashMap<String, Vec<i64>>,
    /// Snapshot `(current, max)` do HP de cada entidade COM `Health` (sem o
    /// player — esse já vive em `player_hp`) para `viber.entity_hp(id)`.
    pub hp_snapshot: std::collections::HashMap<i64, (f32, f32)>,
    /// Posição de MUNDO das entidades nomeadas (`viber.entity_position`,
    /// `viber.nearby`) — o mesmo snapshot de nomes, com o GlobalTransform.
    pub pos_snapshot: std::collections::HashMap<i64, (String, Vec3)>,
    /// Forward do herói no plano (x, z) — `viber.player_forward()`.
    pub player_forward: Vec2,
    /// Dir raiz dos scripts (`<mundo>/scripts`) — `viber.load` resolve
    /// módulos contra ele. Fixo por mundo (semeado no `LuaScriptHost::new`).
    pub scripts_dir: Option<std::path::PathBuf>,
    /// Nomes dos `<Prototype>` do mundo (validação à fila de
    /// `viber.spawn_prototype` — erro de script em vez de falha silenciosa).
    pub prototype_names: Vec<String>,
    /// Definições de quest do mundo (`viber.quest_def`/`quest_defs`) — o
    /// mesmo JSON lido do disco pela engine, exposto para um diálogo/quest
    /// 100 % Lua reusar títulos, linhas e objetivos autorais.
    pub quest_defs: Vec<QuestDefLite>,
    /// Ring of `viber.log` lines (capped) — also read by tests.
    pub logs: Vec<String>,
    /// Snapshot dos estados de quest ("not_taken|active|ready|done") para
    /// `viber.quest_state`, atualizado no início de cada frame.
    pub quest_states: std::collections::HashMap<String, String>,
    /// Snapshot do vault (recursos + itens) para `vault_get`/`item_count`.
    pub vault: std::collections::HashMap<String, u32>,
    /// Hostis vivos por banda do mundo (travel::REGIONS) —
    /// `viber.alive_in_region(idx)`.
    pub alive_regions: [u32; 5],
    /// Range de interação da entidade actual (`viber.set_interaction`) —
    /// `viber.interacted` respeita-o em vez de hardcodar 3,5 m.
    pub interaction_range: Option<f32>,
    /// Vencedor por tecla do frame (`interact::InteractionFocus`): só ele
    /// vê `viber.interacted(tecla)` a `true`.
    pub interaction_focus: std::collections::HashMap<KeyCode, Entity>,
    /// Snapshot do HP do herói `(current, max)` para `viber.player_hp`.
    pub player_hp: Option<(f32, f32)>,
    /// Handle de leitura partilhado do terreno (`viber.ground_below`) —
    /// dois `Arc` clones; o terreno não muda pós-bootstrap, portanto o
    /// snapshot É o terreno (não nasce segunda fonte de altura).
    pub terrain: Option<crate::terrain::runtime::TerrainReader>,
    /// Registos de superfície partilhados (`viber.on_road`, `viber.in_water`).
    ///
    /// O [`crate::terrain::runtime::TerrainReader`] leva o campo de altura e o
    /// voxel, mas não as estradas nem os corpos de água — e o `TerrainRuntime`
    /// não é clonável por frame. Este `Arc` é construído UMA vez (o mundo
    /// carvado não muda depois do bootstrap) e depois custa um clone de
    /// ponteiro por frame.
    pub surfaces: Option<std::sync::Arc<SurfaceRegistries>>,
}

/// Definição de quest achatada para Lua (`viber.quest_def`).
#[derive(Debug, Clone, Default)]
pub struct QuestDefLite {
    pub id: String,
    pub title: String,
    pub npc: String,
    pub biome: String,
    /// `kill` | `collect` | `visit`.
    pub kind: String,
    pub target: String,
    pub count: u32,
    pub radius: f32,
    pub gold: u32,
    pub xp: u32,
    pub items: Vec<String>,
    pub lines_intro: Vec<String>,
    pub lines_progress: Vec<String>,
    pub lines_complete: Vec<String>,
}

/// Estradas e água, partilhadas com os scripts.
#[derive(Debug, Default)]
pub struct SurfaceRegistries {
    pub roads: Vec<crate::terrain::roads::RoadPath>,
    pub water: Vec<crate::terrain::water::WaterBody>,
}

impl SurfaceRegistries {
    /// Ponto na fita de uma estrada (`isPointOnRoad`).
    pub fn on_road(&self, x: f32, z: f32) -> bool {
        let p = Vec2::new(x, z);
        self.roads.iter().any(|road| road.is_on_road(p))
    }

    /// Ponto dentro de uma zona de carve de água.
    pub fn in_water(&self, x: f32, z: f32) -> bool {
        let p = Vec2::new(x, z);
        self.water.iter().any(|body| body.contains(p))
    }
}

/// Cache do snapshot de superfícies, construída no primeiro frame com terreno.
///
/// Vive num `Local` do `luau_update` e não num `Resource`: ninguém mais a lê, e
/// as apps mínimas dos testes montam o sistema solto, sem plugin — um recurso
/// obrigatório partia-as todas.
pub type SurfaceCache = Option<std::sync::Arc<SurfaceRegistries>>;

impl ScriptCtx {
    const LOG_CAP: usize = 256;
    /// Cap por linha: um `viber.log` gigante não pode reter dezenas de MB
    /// no ring de 256 linhas (trunca em char boundary, igual ao bridge).
    const MAX_LOG_MESSAGE: usize = 8192;
    /// Cap do snapshot de entidades nomeadas (`viber.find`) — o mesmo teto
    /// do `viber.debug.entities`.
    pub(crate) const NAMED_SNAPSHOT_CAP: usize = 4096;

    pub(crate) fn push_log(&mut self, mut line: String) {
        if line.len() > Self::MAX_LOG_MESSAGE {
            let mut end = Self::MAX_LOG_MESSAGE;
            while !line.is_char_boundary(end) {
                end -= 1;
            }
            line.truncate(end);
            line.push('…');
        }
        if self.logs.len() >= Self::LOG_CAP {
            self.logs.remove(0);
        }
        self.logs.push(line);
    }
}
