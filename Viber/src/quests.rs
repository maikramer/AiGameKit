//! Quests & diálogo (loop 3 do port simple-rpg) — o análogo nativo do
//! plugin Quests do VibeGame:
//!
//! - **Dados**: os JSONs de quests do MUNDO (`quests/*.json` ao lado do
//!   `world.xml`, remapeável via `game.quests_dir` do config.yaml) — mesmo
//!   schema do jogo browser. Conteúdo no disco: um mundo novo traz as suas
//!   quests sem recompilar a engine.
//! - **Estado**: `QuestLog` (NotTaken → Active → [Ready] → Done; bounties do
//!   quadro (`npc == "notice_board"`) são repetíveis e voltam a NotTaken).
//! - **Objetivos**: `kill` (hook do melee, por tipo de criatura), `visit`
//!   (proximidade a entidades com o nome do alvo) e `collect` (reportado por
//!   scripts via `viber.report_collect`; auto com o vault no loop 4).
//! - **Diálogo**: [E] perto de um `<DialogueNPC>` mostra as linhas certas no
//!   balão do HUD (intro → progresso → completa) e aceita/entrega a quest.
//! - **QuestTracker**: painel com as quests ativas (max 4 linhas).
//! - **Hooks Luau**: `quest_state/quest_accept/quest_turn_in/report_kill/
//!   report_collect`.
//!
//! Recompensas: XP real (vitals); ouro/itens chegam com o vault (loop 4) e
//! por enquanto vão para o toast.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use bevy::prelude::*;
use serde::Deserialize;

use crate::hud::HudBalloon;
use crate::luau::{LuaScriptRef, ScriptInteraction, ScriptToast};
use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::vitals::Health;
use crate::vitals::Xp;

/// Alcance do diálogo com `<DialogueNPC>` (mesmo do prompt do HUD).
pub const DIALOGUE_RANGE_M: f32 = crate::interact::BASE_RANGE_M;
/// Raio de "visita" a um marco nomeado (m).
pub const VISIT_RADIUS_M: f32 = 25.0;
/// Linhas máximas do QuestTracker.
pub const TRACKER_ROWS: usize = 4;

// ── dados (mesmo schema do VibeGame) ────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
pub struct QuestDef {
    pub id: String,
    pub npc: String,
    pub biome: String,
    pub title: String,
    #[serde(default)]
    pub lines_intro: Vec<String>,
    #[serde(default)]
    pub lines_progress: Vec<String>,
    #[serde(default)]
    pub lines_complete: Vec<String>,
    pub objective: QuestObjective,
    #[serde(default)]
    pub rewards: QuestRewards,
}

#[derive(Debug, Clone, Deserialize)]
pub struct QuestObjective {
    #[serde(rename = "type")]
    pub kind: String,
    /// kill/collect: tipo de criatura/item. visit: lista separada por
    /// espaços de nomes de entidades.
    pub target: String,
    pub count: u32,
    /// visit: raio autoral de proximidade (m) — os JSON trazem 9-12.
    /// Ausente → fallback [`VISIT_RADIUS_M`].
    #[serde(default)]
    pub radius: Option<f32>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct QuestRewards {
    #[serde(default)]
    pub gold: u32,
    #[serde(default)]
    pub xp: u32,
    #[serde(default)]
    pub items: Vec<String>,
}

/// Lê todas as quests `*.json` de um diretório do mundo (o `run` passa
/// `config.quests_dir_on(&world_dir)`; default `quests/` ao lado do
/// `world.xml`). Ficheiros em ordem SORTED por nome — determinismo ("mesma
/// seed, mesmo mundo" aplica-se à ordem dos defs, que o tracker respeita).
/// Falha de parse/leitura de um ficheiro = warn + skip; diretório ausente =
/// warn único + log vazio. Nunca pânico: um JSON que caia não derruba o jogo
/// (o `analyze` e os testes são o gate da classe — ver
/// `test_fantasy_scenario_quests_are_loaded`).
pub fn load_quests_from_dir(dir: &Path) -> Vec<QuestDef> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        warn!(
            target: "viber::quests",
            "diretório de quests ausente: {} — mundo sem quests (crie `quests/*.json` ou aponte `game.quests_dir` no config.yaml)",
            dir.display()
        );
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = entries
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| {
            path.is_file() && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("json"))
        })
        .collect();
    files.sort();
    let mut defs = Vec::new();
    for path in files {
        let parsed = std::fs::read_to_string(&path)
            .map_err(|error| error.to_string())
            .and_then(|text| {
                serde_json::from_str::<Vec<QuestDef>>(&text)
                    .map_err(|error| error.to_string())
            });
        match parsed {
            Ok(mut list) => defs.append(&mut list),
            Err(error) => warn!(
                target: "viber::quests",
                "quest {}: {error} — ficheiro ignorado",
                path.display()
            ),
        }
    }
    defs
}

// ── estado ──────────────────────────────────────────────────────────────

/// Estado de uma quest aceita: progresso do objetivo + marcos visitados.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ActiveQuest {
    pub progress: u32,
    /// Alvos de visita já alcançados (normalizados).
    pub visited: Vec<String>,
}

/// Estado de interface por quest.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuestStatus {
    NotTaken,
    Active,
    /// Objetivo completo, à espera de entrega no NPC.
    Ready,
    Done,
}

/// Nome do estado para scripts (`viber.quest_state`).
pub fn status_name(status: QuestStatus) -> &'static str {
    match status {
        QuestStatus::NotTaken => "not_taken",
        QuestStatus::Active => "active",
        QuestStatus::Ready => "ready",
        QuestStatus::Done => "done",
    }
}

/// O diário do herói: definições + estado por quest.
#[derive(Debug, Clone, Resource)]
pub struct QuestLog {
    pub defs: Vec<QuestDef>,
    pub(crate) states: HashMap<String, ActiveQuest>,
    pub(crate) done: Vec<String>,
}

impl Default for QuestLog {
    /// Compat com apps mínimas/testes: `quests/` relativo ao CWD. O `run`
    /// real NUNCA usa este default — `main.rs` insere o QuestLog explicitamente
    /// com o dir resolvido do config (`config.quests_dir_on`), antes dos
    /// plugins (o `init_resource` do `QuestsPlugin` não substitui o que já
    /// existe).
    fn default() -> Self {
        Self::with_dir(Path::new("quests"))
    }
}

impl QuestDef {
    /// Raio efetivo do objetivo visit: o `radius` do JSON com fallback
    /// [`VISIT_RADIUS_M`] (questões antigas sem o campo).
    pub fn visit_radius(&self) -> f32 {
        self.objective.radius.unwrap_or(VISIT_RADIUS_M)
    }
}

impl QuestLog {
    /// Constrói o diário lendo as quests do diretório do mundo (os estados
    /// nascem vazios — é o construtor do `run`; ver [`load_quests_from_dir`]).
    pub fn with_dir(dir: &Path) -> Self {
        Self {
            defs: load_quests_from_dir(dir),
            states: HashMap::new(),
            done: Vec::new(),
        }
    }

    pub fn def(&self, id: &str) -> Option<&QuestDef> {
        self.defs.iter().find(|d| d.id == id)
    }

    /// Estado computado (Ready = ativa com objetivo completo). Collect lê o
    /// VAULT (o inventário é a autoridade); kill/visit usam o estado interno.
    pub fn status(&self, id: &str, vault: Option<&crate::economy::Vault>) -> QuestStatus {
        if self.done.iter().any(|d| d == id) {
            return QuestStatus::Done;
        }
        let Some((def, active)) = self.def(id).zip(self.states.get(id)) else {
            return QuestStatus::NotTaken;
        };
        if self.is_complete_with_vault(def, active, vault) {
            QuestStatus::Ready
        } else {
            QuestStatus::Active
        }
    }

    /// Progresso de um objetivo collect a partir do vault.
    fn collect_progress(&self, def: &QuestDef, vault: Option<&crate::economy::Vault>) -> u32 {
        match vault {
            Some(vault) => vault.count(&def.objective.target).min(def.objective.count),
            None => 0,
        }
    }

    fn is_complete_with_vault(
        &self,
        def: &QuestDef,
        active: &ActiveQuest,
        vault: Option<&crate::economy::Vault>,
    ) -> bool {
        match def.objective.kind.as_str() {
            "visit" => active.visited.len() >= def.objective.count as usize,
            "collect" => self.collect_progress(def, vault) >= def.objective.count,
            _ => active.progress >= def.objective.count,
        }
    }

    /// Aceita (NotTaken → Active). `false` se não existe ou já está ativa/feita.
    pub fn accept(&mut self, id: &str) -> bool {
        if self.status(id, None) != QuestStatus::NotTaken {
            return false;
        }
        self.states.insert(id.into(), ActiveQuest::default());
        true
    }

    /// Entrega (Ready → Done; repetíveis voltam a NotTaken). Collect consome
    /// os itens do vault. Devolve as recompensas quando a entrega aconteceu.
    pub fn turn_in(
        &mut self,
        id: &str,
        mut vault: Option<&mut crate::economy::Vault>,
    ) -> Option<QuestRewards> {
        if self.status(id, vault.as_deref()) != QuestStatus::Ready {
            return None;
        }
        let (rewards, repeatable, collect_target, collect_count) = {
            let def = self.def(id)?;
            (
                def.rewards.clone(),
                def.npc == "notice_board",
                (def.objective.kind == "collect").then_some(def.objective.target.clone()),
                def.objective.count,
            )
        };
        // collect consome os itens entregues
        if let Some(target) = collect_target {
            let Some(vault) = vault.as_mut() else {
                return None; // collect exige vault para consumir
            };
            if !vault.take(&target, collect_count) {
                return None; // stock sumiu entre ready e entrega
            }
        }
        self.states.remove(id);
        if !repeatable {
            self.done.push(id.into());
        }
        Some(rewards)
    }

    /// Aplica um abate a todas as quests ativas com esse alvo; devolve os
    /// ids que ficaram Ready agora.
    pub fn report_kill(&mut self, kind: &str) -> Vec<String> {
        self.report_progress(kind, 1)
    }

    /// Aplica progresso de kill às quests ativas com esse alvo; devolve os
    /// ids que ficaram Ready agora. (collect é vault-driven.)
    pub fn report_progress(&mut self, target: &str, amount: u32) -> Vec<String> {
        let wanted = normalize_target(target);
        let candidates: Vec<(String, u32)> = self
            .defs
            .iter()
            .filter(|d| {
                d.objective.kind == "kill" && normalize_target(&d.objective.target) == wanted
            })
            .filter(|d| self.states.contains_key(&d.id))
            .map(|d| (d.id.clone(), d.objective.count))
            .collect();
        let mut became_ready = Vec::new();
        for (id, count) in candidates {
            let Some(active) = self.states.get_mut(&id) else {
                continue;
            };
            let was_complete = active.progress >= count;
            active.progress = active.progress.saturating_add(amount);
            if !was_complete && active.progress >= count {
                became_ready.push(id);
            }
        }
        became_ready
    }

    /// Registra visita a um marco nomeado; devolve os ids que ficaram Ready.
    pub fn report_visit(&mut self, place_name: &str) -> Vec<String> {
        let wanted = normalize_target(place_name);
        let candidates: Vec<(String, u32)> = self
            .defs
            .iter()
            .filter(|d| {
                d.objective.kind == "visit"
                    && d.objective
                        .target
                        .split_whitespace()
                        .any(|t| normalize_target(t) == wanted)
                    && self.states.contains_key(&d.id)
            })
            .map(|d| (d.id.clone(), d.objective.count))
            .collect();
        let mut became_ready = Vec::new();
        for (id, count) in candidates {
            let Some(active) = self.states.get_mut(&id) else {
                continue;
            };
            let was_complete = active.visited.len() >= count as usize;
            if !active.visited.iter().any(|v| v == &wanted) {
                active.visited.push(wanted.clone());
            }
            if !was_complete && active.visited.len() >= count as usize {
                became_ready.push(id);
            }
        }
        became_ready
    }

    /// ids das quests ativas (para o tracker), na ordem dos defs.
    pub fn active_ids(&self, vault: Option<&crate::economy::Vault>) -> Vec<String> {
        self.defs
            .iter()
            .filter(|d| {
                matches!(
                    self.status(&d.id, vault),
                    QuestStatus::Active | QuestStatus::Ready
                )
            })
            .map(|d| d.id.clone())
            .collect()
    }

    /// Texto "x/y" do objetivo (collect lê o vault; visit conta marcos).
    pub fn progress_text(&self, id: &str, vault: Option<&crate::economy::Vault>) -> String {
        let (Some(def), Some(active)) = (self.def(id), self.states.get(id)) else {
            return String::new();
        };
        match def.objective.kind.as_str() {
            "visit" => format!("{}/{}", active.visited.len(), def.objective.count),
            "collect" => {
                format!(
                    "{}/{}",
                    self.collect_progress(def, vault),
                    def.objective.count
                )
            }
            _ => format!(
                "{}/{}",
                active.progress.min(def.objective.count),
                def.objective.count
            ),
        }
    }
}

/// Normaliza tipos/nomes de alvo: minúsculas, sem `-`/`_`, prefixo `boss`
/// removido — `boss_bogwarden` e `bog-warden` caem no mesmo alvo.
pub fn normalize_target(raw: &str) -> String {
    let cleaned: String = raw
        .to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    cleaned.strip_prefix("boss").unwrap_or(&cleaned).to_string()
}

// ── plugin + UI ─────────────────────────────────────────────────────────

pub struct QuestsPlugin;

impl Plugin for QuestsPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<QuestLog>()
            // Idempotente com o Ambient/Combat/Vitals (apps mínimas de teste
            // ficam auto-suficientes — mesmo padrão do `CombatPlugin`).
            .add_message::<crate::ambient::SfxEvent>()
            .add_systems(Startup, (spawn_tracker, spawn_quest_banner))
            .add_systems(
                Update,
                (
                    timed(Group::World, quest_dialogue_system),
                    quest_banner_drive,
                    quest_visit_system,
                    timed(Group::World, quest_tracker_system),
                    quest_debug_teleport,
                    quest_debug_nearest,
                    quest_debug_hostile,
                    quest_debug_harvest,
                ),
            );
    }
}

/// Debug de QA (**F6**): teleporta o herói ao `<DialogueNPC>` de quest MAIS
/// PRÓXIMO da posição atual. Par de `quest_debug_teleport` (**F7**, ciclo).
#[allow(clippy::type_complexity)]
fn quest_debug_nearest(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    npcs: Query<(&GlobalTransform, &crate::recipes::spawn::DialogueNpc)>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !keys.just_pressed(KeyCode::F6) || npcs.is_empty() {
        return;
    }
    let Ok((_player_entity, player_global, mut transform)) = players.single_mut() else {
        return;
    };
    let player_pos = player_global.translation();
    let Some(target) = npcs
        .iter()
        .min_by(|(a, _), (b, _)| {
            a.translation()
                .distance_squared(player_pos)
                .total_cmp(&b.translation().distance_squared(player_pos))
        })
        .map(|(t, _)| t.translation())
    else {
        return;
    };
    let x = target.x + 1.6;
    let z = target.z + 1.6;
    let y = terrain.as_ref().map(|t| t.sample(x, z)).unwrap_or(target.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast("QA: teleport ao NPC mais próximo".into()));
}

/// Debug de QA (**F8**): teleporta o herói à criatura hostil (scriptada,
/// com Health, sem interação de colheita) MAIS PRÓXIMA — valida kills de
/// quest sem procurar lobos a pé.
#[allow(clippy::type_complexity)]
fn quest_debug_hostile(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    creatures: Query<
        (&GlobalTransform, &LuaScriptRef, Option<&ScriptInteraction>),
        (With<LuaScriptRef>, With<Health>, Without<Player>),
    >,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !keys.just_pressed(KeyCode::F8) {
        return;
    }
    let Ok((_pe, player_global, mut transform)) = players.single_mut() else {
        return;
    };
    let player_pos = player_global.translation();
    // hostil = script de inimigo/boss (townsfolk/POIs ficam de fora)
    let is_hostile =
        |path: &str| path.contains("enemies/") || path.contains("bosses/") || path.contains("boss");
    let Some(target) = creatures
        .iter()
        .filter(|(_, script, interaction)| interaction.is_none() && is_hostile(&script.path))
        .min_by(|(a, _, _), (b, _, _)| {
            a.translation()
                .distance_squared(player_pos)
                .total_cmp(&b.translation().distance_squared(player_pos))
        })
        .map(|(t, _, _)| t.translation())
    else {
        toasts.write(ScriptToast("QA: nenhuma criatura hostil ativa".into()));
        return;
    };
    let x = target.x + 1.8;
    let z = target.z + 1.8;
    let y = terrain.as_ref().map(|t| t.sample(x, z)).unwrap_or(target.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast("QA: teleport à criatura mais próxima".into()));
}

/// Debug de QA (**F9**): teleporta o herói ao colhível/ponto de interação
/// (ScriptInteraction: árvore, pedra, baú, quadro) mais próximo.
#[allow(clippy::type_complexity)]
fn quest_debug_harvest(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    interactions: Query<(Entity, &GlobalTransform, &ScriptInteraction)>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut last: Local<Option<Entity>>,
) {
    if !keys.just_pressed(KeyCode::F9) {
        return;
    }
    let Ok((_pe, player_global, mut transform)) = players.single_mut() else {
        return;
    };
    let player_pos = player_global.translation();
    // exclui o último alvo para o ciclo avançar pela cadeia de vizinhos
    let previous = *last;
    let Some((target_entity, target_transform)) = interactions
        .iter()
        .filter(|(_, _, interaction)| {
            let label = interaction.label.to_lowercase();
            label.contains("cortar") || label.contains("minerar")
        })
        .filter(|(entity, _, _)| previous.is_none_or(|l| l != *entity))
        .min_by(|(_, a, _), (_, b, _)| {
            a.translation()
                .distance_squared(player_pos)
                .total_cmp(&b.translation().distance_squared(player_pos))
        })
        .map(|(entity, t, _)| (entity, t.translation()))
    else {
        toasts.write(ScriptToast("QA: nenhum colhível por perto".into()));
        return;
    };
    *last = Some(target_entity);
    let x = target_transform.x + 1.5;
    let z = target_transform.z + 1.5;
    let y = terrain
        .as_ref()
        .map(|t| t.sample(x, z))
        .unwrap_or(target_transform.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast("QA: teleport ao colhível mais próximo".into()));
}

/// Debug de QA (**F7**): teleporta o herói ao próximo `<DialogueNPC>` em
/// ciclo (análogo do debug action `tp` do VibeGame). Para validar o flow de
/// quests pela bridge sem andar quilômetros. **Shift+F7**: criatura hostil
/// (scriptada) mais próxima — para validar kills de quest.
#[allow(clippy::type_complexity)]
fn quest_debug_teleport(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    npcs: Query<(&GlobalTransform, &crate::recipes::spawn::DialogueNpc)>,
    enemies: Query<(&GlobalTransform, &LuaScriptRef)>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut cursor: Local<usize>,
) {
    if !keys.just_pressed(KeyCode::F7) || npcs.is_empty() {
        return;
    }
    if true {
        let _ = enemies;
    }
    let Ok((_player_entity, _player_global, mut transform)) = players.single_mut() else {
        return;
    };
    let list: Vec<Vec3> = npcs.iter().map(|(t, _)| t.translation()).collect();
    let index = *cursor % list.len();
    *cursor += 1;
    let target = list[index];
    let x = target.x + 1.6;
    let z = target.z + 1.6;
    let y = terrain.as_ref().map(|t| t.sample(x, z)).unwrap_or(target.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast(format!("QA: teleport para npc #{index}")));
}

/// Marker da raiz do QuestTracker + linhas de texto.
#[derive(Component)]
struct QuestTracker;

/// Escreve as linhas certas no balão do HUD quando o herói aperta [E] perto
/// de um `<DialogueNPC>`: intro (aceita), progresso, ou entrega com
/// recompensas. Substitui o trigger genérico do `hud_balloon_update`.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn quest_dialogue_system(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(Entity, &GlobalTransform, &crate::recipes::spawn::DialogueNpc)>,
    // Um script reclamou o diálogo (`viber.own_system("dialogue")`): o
    // handler nativo cala e o fluxo é do Lua.
    owners: Option<Res<crate::luau::ownership::ScriptSystemOwners>>,
    // Arbitragem do [E] (`interact::InteractionFocus`): se o vencedor da
    // tecla é OUTRO alvo, o diálogo nativo não dispara — prompt e ação
    // apontavam para entidades diferentes quando um `ScriptInteraction`
    // mais próximo ganhava o foco.
    focus: Option<Res<crate::interact::InteractionFocus>>,
    mut log: ResMut<QuestLog>,
    mut vault: Option<ResMut<crate::economy::Vault>>,
    mut heroes: Query<&mut Xp, With<Player>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut commands: Commands,
    mut banners: Query<(&mut QuestDoneBanner, &Children)>,
    mut balloons: Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    mut texts: Query<&mut Text>,
    mut events: Option<ResMut<crate::luau::ScriptEventQueue>>,
) {
    if !keys.just_pressed(KeyCode::KeyE) {
        return;
    }
    if owners
        .as_deref()
        .is_some_and(|o| o.owns(crate::luau::ownership::SYSTEM_DIALOGUE))
    {
        return;
    }
    let Some(player) = players.iter().next() else {
        return;
    };
    let player_pos = player.translation();
    // O MAIS PRÓXIMO em alcance (o `find` first-hit era order-dependent —
    // com 2 NPCs em alcance entregava/aceitava a quest do errado). O alcance
    // é o EFETIVO (`interact::default_range`, metade do autorado).
    let range = crate::interact::default_range();
    let Some((npc_entity, _, npc)) = npcs
        .iter()
        .filter(|(_, t, _)| t.translation().distance(player_pos) < range)
        .min_by(|(_, a, _), (_, b, _)| {
            a.translation()
                .distance_squared(player_pos)
                .total_cmp(&b.translation().distance_squared(player_pos))
        })
    else {
        return;
    };
    // O vencedor do foco [E] (se algum) tem de ser ESTE NPC — um script mais
    // próximo que ganhou o [E] leva a interação consigo.
    if let Some(focus) = focus.as_deref() {
        if let Some(winner) = focus.winner(KeyCode::KeyE) {
            if winner != npc_entity {
                return;
            }
        }
    }
    let id = npc.dialogue_id.clone();
    let vault_ref = vault.as_deref();
    info!(target: "viber::quests", "diálogo [E] com '{id}' — estado {}", crate::quests::status_name(log.status(&id, vault_ref)));
    let body: String = match log.status(&id, vault.as_deref()) {
        QuestStatus::NotTaken => {
            log.accept(&id);
            info!(target: "viber::quests", "quest '{id}' aceita via diálogo");
            sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::QuestAccept,
                position: None,
            });
            join_lines(
                &log.def(&id)
                    .map(|d| d.lines_intro.clone())
                    .unwrap_or_default(),
            )
        }
        QuestStatus::Active | QuestStatus::Ready => {
            // snapshot dos dados antes do &mut do turn_in
            let snapshot = log.def(&id).map(|d| {
                (
                    d.lines_progress.clone(),
                    d.lines_complete.clone(),
                    d.objective.clone(),
                    d.title.clone(),
                )
            });
            let Some((progress_lines, complete_lines, objective, title)) = snapshot else {
                return;
            };
            if log.status(&id, vault.as_deref()) == QuestStatus::Ready {
                info!(target: "viber::quests", "entrega de '{id}'");
                if let Some(rewards) = log.turn_in(&id, vault.as_deref_mut()) {
                    if let Some(events) = events.as_deref_mut() {
                        events.push(crate::luau::ScriptGameEvent::QuestDone {
                            id: id.clone(),
                        });
                    }
                    if let Ok(mut xp) = heroes.single_mut() {
                        crate::vitals::gain_xp(&mut xp, rewards.xp);
                    }
                    if rewards.gold > 0 {
                        if let Some(vault) = vault.as_deref_mut() {
                            vault.add_resource("gold", rewards.gold);
                        }
                    }
                    for item in &rewards.items {
                        if let Some((id, n)) = parse_item_reward(item) {
                            if let Some(vault) = vault.as_deref_mut() {
                                vault.item_add(&id, n);
                            }
                        }
                    }
                    toasts.write(ScriptToast(format!(
                        "Quest concluída: {} (+{} XP{})",
                        title,
                        rewards.xp,
                        if rewards.gold > 0 {
                            format!(", +{} ouro", rewards.gold)
                        } else {
                            String::new()
                        }
                    )));
                    // Fanfare do passe de juice: banner autoral "MISSÃO
                    // CONCLUÍDA" + faíscas no herói + SFX de missão feita.
                    quest_done_fanfare(
                        &mut commands,
                        &mut sfx,
                        &mut banners,
                        &mut texts,
                        player_pos,
                        &title,
                    );
                }
                join_lines(&complete_lines)
            } else {
                let progress = match objective.kind.as_str() {
                    "visit" => log
                        .states
                        .get(&id)
                        .map(|a| a.visited.len() as u32)
                        .unwrap_or(0),
                    "collect" => vault
                        .as_deref()
                        .map(|v| v.count(&objective.target).min(objective.count))
                        .unwrap_or(0),
                    _ => log.states.get(&id).map(|a| a.progress).unwrap_or(0),
                };
                let remaining = objective.count.saturating_sub(progress);
                join_lines(&progress_lines).replace("{remaining}", &remaining.to_string())
            }
        }
        QuestStatus::Done => join_lines(
            &log.def(&id)
                .map(|d| d.lines_complete.clone())
                .unwrap_or_default(),
        ),
    };
    show_balloon(&mut balloons, &mut texts, &body, crate::hud::BALLOON_DURATION);
}

fn join_lines(lines: &[String]) -> String {
    lines.join("\n")
}

/// Parseia reward de item do JSON (`"potion:2"` → ("potion", 2)).
pub fn parse_item_reward(raw: &str) -> Option<(String, u32)> {
    let (id, n) = raw.split_once(':')?;
    Some((id.trim().to_lowercase(), n.trim().parse().ok()?))
}

/// Mostra o balão do HUD com `body` por `secs` segundos. Caminho ÚNICO do
/// balão: o diálogo nativo de quests e o `viber.say()` dos scripts escrevem
/// por aqui. Devolve `true` se o mundo tem um `<DialogueBalloon>` (quem
/// chama avisa 1× quando não tem — um `say` silencioso era invisível).
pub(crate) fn show_balloon(
    balloons: &mut Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    texts: &mut Query<&mut Text>,
    body: &str,
    secs: f32,
) -> bool {
    let mut found = false;
    for (mut visibility, mut balloon, children) in balloons.iter_mut() {
        found = true;
        balloon.timer = secs;
        *visibility = Visibility::Visible;
        if let Some(child) = children.first() {
            if let Ok(mut text) = texts.get_mut(*child) {
                text.0 = body.into();
            }
        }
    }
    found
}

// ── fanfare de quest concluída (passe de juice r1) ──────────────────────

/// Vida total do banner "MISSÃO CONCLUÍDA" (s), fades incluídos.
pub const QUEST_BANNER_SECS: f32 = 2.5;
/// Fade de entrada (s).
const QUEST_BANNER_FADE_IN: f32 = 0.22;
/// Fade de saída (s) — um pouco mais longo: o banner sai de cena.
const QUEST_BANNER_FADE_OUT: f32 = 0.5;

/// Banner autoral de turn-in (padrão `CampfireBanner` do menus): UM nó
/// pré-spawnado em Startup, invisível; o turn-in só arranca o timer e
/// escreve o subtítulo. Sem spawn/despawn por entrega.
#[derive(Component)]
struct QuestDoneBanner {
    timer: f32,
}

#[derive(Component)]
struct BannerTitle;

#[derive(Component)]
struct BannerSubtitle;

/// Alpha do banner com `remaining` s de vida — a mesma curva do toast
/// (`menus::toast_alpha`): entra rápido, segura opaco, sai devagar.
/// Puro para os testes.
pub fn quest_banner_alpha(remaining: f32) -> f32 {
    if remaining <= 0.0 {
        return 0.0;
    }
    let elapsed = QUEST_BANNER_SECS - remaining;
    ((elapsed / QUEST_BANNER_FADE_IN)
        .min(1.0)
        .min((remaining / QUEST_BANNER_FADE_OUT).min(1.0)))
    .clamp(0.0, 1.0)
}

/// Fanfare de entrega: burst `sparkle` no herói, `SfxEvent::QuestDone`
/// (interface, volume cheio) e o banner com o título da quest.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
fn quest_done_fanfare(
    commands: &mut Commands,
    sfx: &mut MessageWriter<crate::ambient::SfxEvent>,
    banners: &mut Query<(&mut QuestDoneBanner, &Children)>,
    texts: &mut Query<&mut Text>,
    player_pos: Vec3,
    title: &str,
) {
    sfx.write(crate::ambient::SfxEvent {
        clip: crate::ambient::SfxClip::QuestDone,
        position: None,
    });
    crate::particles::spawn_burst(
        commands,
        &crate::vitals::juice_spec(
            "sparkle",
            (0.15, 0.4),
            (0.4, 0.8),
            (1.5, 3.5),
            Some([1.0, 0.85, 0.4]),
        ),
        player_pos + Vec3::Y * 1.2,
        18,
    );
    if let Ok((mut banner, children)) = banners.single_mut() {
        banner.timer = QUEST_BANNER_SECS;
        // children[0] = "MISSÃO CONCLUÍDA" (fixo), children[1] = subtítulo.
        if let Some(&subtitle) = children.get(1) {
            if let Ok(mut text) = texts.get_mut(subtitle) {
                text.0 = format!("✦ {title}");
            }
        }
    }
}

/// Banner centro-topo: "MISSÃO CONCLUÍDA" + título da quest.
fn spawn_quest_banner(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Percent(18.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                flex_direction: FlexDirection::Column,
                align_items: AlignItems::Center,
                row_gap: Val::Px(4.0),
                ..Default::default()
            },
            Visibility::Hidden,
            Name::new("ui:quest-done"),
            QuestDoneBanner { timer: 0.0 },
        ))
        .with_children(|wrap| {
            wrap.spawn((
                Text::new("MISSÃO CONCLUÍDA"),
                TextColor(Color::srgba(0.98, 0.8, 0.4, 0.0)),
                TextFont::from_font_size(22.0),
                bevy::ui::widget::TextShadow {
                    offset: Vec2::splat(1.0),
                    color: Color::srgba(0.0, 0.0, 0.0, 0.0),
                },
                BannerTitle,
            ));
            wrap.spawn((
                Text::new(""),
                TextColor(Color::srgba(0.95, 0.93, 0.85, 0.0)),
                TextFont::from_font_size(15.0),
                bevy::ui::widget::TextShadow {
                    offset: Vec2::splat(1.0),
                    color: Color::srgba(0.0, 0.0, 0.0, 0.0),
                },
                BannerSubtitle,
            ));
        });
}

/// Vida e fade do banner. O alpha RECOMPUTA-SE de constantes por frame (como
/// o fade dos toasts) — multiplicar o alpha corrente por si mesmo derivava
/// para preto; a sombra acompanha para não deixar fantasma invisível.
#[allow(clippy::type_complexity)]
fn quest_banner_drive(
    time: Res<Time>,
    mut banner: Query<(&mut QuestDoneBanner, &mut Visibility)>,
    // Sem `Without<>` os pares Title/Subtitle conflitavam em TextColor e
    // TextShadow (B0001: um nó de texto podia teoricamente ter os dois
    // markers) — o pânico morria no 1.º frame de qualquer mundo com HUD.
    mut titles: Query<&mut TextColor, (With<BannerTitle>, Without<BannerSubtitle>)>,
    mut subtitles: Query<&mut TextColor, (With<BannerSubtitle>, Without<BannerTitle>)>,
    mut title_shadows: Query<
        &mut bevy::ui::widget::TextShadow,
        (With<BannerTitle>, Without<BannerSubtitle>),
    >,
    mut subtitle_shadows: Query<
        &mut bevy::ui::widget::TextShadow,
        (With<BannerSubtitle>, Without<BannerTitle>),
    >,
) {
    let Ok((mut banner, mut visibility)) = banner.single_mut() else {
        return;
    };
    if banner.timer > 0.0 {
        banner.timer -= time.delta_secs();
    }
    let alpha = quest_banner_alpha(banner.timer);
    let wanted = if alpha > 0.0 {
        Visibility::Visible
    } else {
        Visibility::Hidden
    };
    if *visibility != wanted {
        *visibility = wanted;
    }
    for mut color in &mut titles {
        color.0.set_alpha(alpha);
    }
    for mut color in &mut subtitles {
        color.0.set_alpha(alpha);
    }
    for mut shadow in &mut title_shadows {
        shadow.color = Color::srgba(0.0, 0.0, 0.0, 0.7 * alpha);
    }
    for mut shadow in &mut subtitle_shadows {
        shadow.color = Color::srgba(0.0, 0.0, 0.0, 0.7 * alpha);
    }
}

/// Visit quests: proximidade a entidades com o nome do alvo (throttle 0,5 s).
/// O raio é POR QUEST (`radius` do JSON, fallback [`VISIT_RADIUS_M`]) —
/// antes era 25 m fixos e marcos com raio autoral 9-12 registavam a visita
/// de longe.
fn quest_visit_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    players: Query<&GlobalTransform, With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    mut log: ResMut<QuestLog>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.5;
    let Some(player) = players.iter().next() else {
        return;
    };
    let player_pos = player.translation();
    let visit_targets: Vec<(String, Vec<String>, f32)> = log
        .defs
        .iter()
        .filter(|d| d.objective.kind == "visit" && log.status(&d.id, None) == QuestStatus::Active)
        .map(|d| {
            (
                d.id.clone(),
                d.objective
                    .target
                    .split_whitespace()
                    .map(normalize_target)
                    .collect(),
                d.visit_radius(),
            )
        })
        .collect();
    if visit_targets.is_empty() {
        return;
    }
    for (_id, targets, radius) in &visit_targets {
        for (name, transform) in &named {
            let name_norm = normalize_target(name);
            if !targets.contains(&name_norm) {
                continue;
            }
            // 2D como o [F] da Nota (travel::nota_measure_system): a cota
            // residual (marco num outeiro, herói na base) não devia bloquear
            // o crédito da visita.
            if transform.translation().xz().distance(player_pos.xz()) > *radius {
                continue;
            }
            for became_ready in log.report_visit(name) {
                if let Some(def) = log.def(&became_ready) {
                    toasts.write(ScriptToast(format!("Objetivo: {} ✓", def.title)));
                }
            }
        }
    }
}

/// Painel do QuestTracker (canto superior direito, sob o minimapa).
fn spawn_tracker(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(172.0),
                right: Val::Px(14.0),
                flex_direction: FlexDirection::Column,
                ..Default::default()
            },
            Name::new("hud:quest-tracker"),
            QuestTracker,
        ))
        .with_children(|panel| {
            for i in 0..TRACKER_ROWS {
                panel.spawn((
                    Text::new(""),
                    TextColor(Color::srgba(0.95, 0.93, 0.85, 0.9)),
                    TextFont::from_font_size(13.0),
                    Name::new(format!("tracker-row-{i}")),
                ));
            }
        });
}

/// Refresca as linhas do tracker (throttle 0,5 s).
fn quest_tracker_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    log: Res<QuestLog>,
    vault: Option<Res<crate::economy::Vault>>,
    tracker: Query<&Children, With<QuestTracker>>,
    mut texts: Query<&mut Text>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.5;
    let Ok(children) = tracker.single() else {
        return;
    };
    let active = log.active_ids(vault.as_deref());
    for (i, child) in children.iter().enumerate() {
        let Ok(mut text) = texts.get_mut(child) else {
            continue;
        };
        let wanted = active
            .get(i)
            .and_then(|id| log.def(id))
            .map(|def| {
                format!(
                    "{}  [{}]",
                    def.title,
                    log.progress_text(&def.id, vault.as_deref())
                )
            })
            .unwrap_or_default();
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

/// (testes, todo o crate) Dir das quests do exemplo — o conteúdo deixou de
/// estar embutido no binário; os testes leem do DISCO, como o `run`.
#[cfg(test)]
pub(crate) fn example_quests_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/simple-rpg/quests")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn log() -> QuestLog {
        let log = QuestLog::with_dir(&example_quests_dir());
        assert!(
            log.defs.len() >= 21,
            "21 quests carregadas, {:?}",
            log.defs.len()
        );
        log
    }

    #[test]
    fn test_quest_banner_alpha_curve() {
        // Acabado de arrancar: ainda a entrar (curva do toast).
        assert!(quest_banner_alpha(QUEST_BANNER_SECS) < 0.05);
        // Meio da vida: opaco.
        assert!((quest_banner_alpha(QUEST_BANNER_SECS * 0.5) - 1.0).abs() < 1e-4);
        // A sair (metade do fade-out).
        let leaving = quest_banner_alpha(QUEST_BANNER_FADE_OUT * 0.5);
        assert!(leaving > 0.0 && leaving < 0.6, "alpha={leaving}");
        // Fim determinístico; lixo não fabrica alpha.
        assert_eq!(quest_banner_alpha(0.0), 0.0);
        assert_eq!(quest_banner_alpha(-1.0), 0.0);
        // O banner dura ~2.5 s: tempo total coerente com os fades.
        assert!(QUEST_BANNER_SECS > QUEST_BANNER_FADE_IN + QUEST_BANNER_FADE_OUT);
    }

    #[test]
    fn test_all_quest_jsons_parse() {
        let log = log();
        // todos os biomas presentes
        for biome in ["city", "dark-forest", "desert", "frozen-peaks", "swamp"] {
            assert!(
                log.defs.iter().any(|d| d.biome == biome),
                "bioma {biome} sem quests"
            );
        }
        // 3 tipos de objetivo presentes
        for kind in ["kill", "collect", "visit"] {
            assert!(
                log.defs.iter().any(|d| d.objective.kind == kind),
                "objetivo {kind} ausente"
            );
        }
    }

    #[test]
    fn test_visit_radius_reads_json_with_fallback() {
        let log = log();
        // Os JSON de visita trazem radius 9-12 (desert 12, mountain 9,
        // dark-forest 10, swamp 11) — o parse deixou de descartá-lo.
        for (id, expected) in [
            ("desert_survey", 12.0),
            ("peaks_survey", 9.0),
            ("forest_survey", 10.0),
            ("swamp_survey", 11.0),
        ] {
            let def = log.def(id).unwrap_or_else(|| panic!("{id} ausente"));
            assert_eq!(def.objective.radius, Some(expected), "radius de {id}");
            assert!((def.visit_radius() - expected).abs() < 1e-4);
        }
        // Quest sem campo radius (kill/collect/JSON antigo) → fallback 25.
        let def = log.def("forest_wolves").expect("forest_wolves ausente");
        assert_eq!(def.objective.radius, None);
        assert!((def.visit_radius() - VISIT_RADIUS_M).abs() < 1e-4);
    }

    #[test]
    fn test_normalize_target_matches_boss_aliases() {
        assert_eq!(
            normalize_target("boss_bogwarden"),
            normalize_target("Bog-Warden")
        );
        assert_eq!(normalize_target("Wolf"), normalize_target("wolf"));
        assert_ne!(normalize_target("wolf"), normalize_target("shade"));
    }

    #[test]
    fn test_kill_quest_lifecycle() {
        let mut log = log();
        assert_eq!(log.status("forest_wolves", None), QuestStatus::NotTaken);
        assert!(log.accept("forest_wolves"));
        assert_eq!(log.status("forest_wolves", None), QuestStatus::Active);
        // aceitar duas vezes falha
        assert!(!log.accept("forest_wolves"));
        // 4 de 5 lobos…
        for _ in 0..4 {
            assert!(log.report_kill("wolf").is_empty());
        }
        assert_eq!(log.status("forest_wolves", None), QuestStatus::Active);
        assert_eq!(log.progress_text("forest_wolves", None), "4/5");
        // …o 5.º fica pronto
        assert_eq!(log.report_kill("wolf"), vec!["forest_wolves".to_string()]);
        assert_eq!(log.status("forest_wolves", None), QuestStatus::Ready);
        // entregar: one-shot vira Done
        let rewards = log.turn_in("forest_wolves", None).expect("recompensas");
        assert_eq!(rewards.xp, 150);
        assert_eq!(log.status("forest_wolves", None), QuestStatus::Done);
        assert!(log.turn_in("forest_wolves", None).is_none());
    }

    #[test]
    fn test_notice_board_bounty_is_repeatable() {
        let mut log = log();
        log.accept("city_wolves");
        for _ in 0..3 {
            log.report_kill("wolf");
        }
        assert_eq!(log.status("city_wolves", None), QuestStatus::Ready);
        let rewards = log.turn_in("city_wolves", None).expect("entrega");
        assert_eq!(rewards.gold, 80);
        // repetível: volta a NotTaken (o cartaz volta à tábua)
        assert_eq!(log.status("city_wolves", None), QuestStatus::NotTaken);
        assert!(log.accept("city_wolves"));
    }

    #[test]
    fn test_visit_quest_multiple_targets() {
        let mut log = log();
        log.accept("forest_survey");
        assert_eq!(log.status("forest_survey", None), QuestStatus::Active);
        assert_eq!(log.progress_text("forest_survey", None), "0/3");
        // nomes com variações normalizam
        assert!(log.report_visit("Forest-Outpost-Tower").is_empty());
        assert_eq!(log.progress_text("forest_survey", None), "1/3");
        assert!(
            log.report_visit("forest-outpost-tower").is_empty(),
            "revisita não duplica"
        );
        assert!(log.report_visit("forest-crossroads-well").is_empty());
        assert_eq!(
            log.report_visit("forest-stone-circle"),
            vec!["forest_survey".to_string()]
        );
        assert_eq!(log.status("forest_survey", None), QuestStatus::Ready);
    }

    #[test]
    fn test_collect_quest_reads_vault() {
        let mut log = log();
        let mut vault = crate::economy::Vault::default();
        log.accept("city_stone");
        assert_eq!(log.status("city_stone", Some(&vault)), QuestStatus::Active);
        assert_eq!(log.progress_text("city_stone", Some(&vault)), "0/10");
        // colheita deposita no vault — o objetivo lê o inventário
        vault.add_resource("stone", 7);
        assert_eq!(log.progress_text("city_stone", Some(&vault)), "7/10");
        assert_eq!(log.status("city_stone", Some(&vault)), QuestStatus::Active);
        vault.add_resource("stone", 3);
        assert_eq!(log.status("city_stone", Some(&vault)), QuestStatus::Ready);
        // entregar consome as 10 pedras
        let rewards = log
            .turn_in("city_stone", Some(&mut vault))
            .expect("entrega");
        assert_eq!(rewards.gold, 120);
        assert_eq!(vault.resource("stone"), 0, "pedras consumidas");
        assert_eq!(log.status("city_stone", Some(&vault)), QuestStatus::Done);
    }

    #[test]
    fn test_tracker_lists_active_in_def_order() {
        let mut log = log();
        log.accept("forest_wolves");
        log.accept("city_wolves");
        let active = log.active_ids(None);
        assert_eq!(active.len(), 2);
        // ordem dos defs: city primeiro (city_quests carregado antes)
        assert_eq!(active[0], "city_wolves");
        assert_eq!(active[1], "forest_wolves");
    }

    /// Os quatro cenários de fantasia têm de estar carregados.
    ///
    /// Um JSON do disco que deixa de parsear não é um erro barulhento: o
    /// loader faz `warn + skip` e o jogo arranca com MENOS quests, em
    /// silêncio (aconteceu: faltava `biome` em duas entradas e o ficheiro
    /// inteiro caiu). Este teste é o gate contra isso — e o `analyze` cruza
    /// as mesmas defs com o mundo (`audit::audit_quests`), a apanhar o NPC ou
    /// o marco de `visit` que não exista.
    #[test]
    fn test_fantasy_scenario_quests_are_loaded() {
        let defs = load_quests_from_dir(&example_quests_dir());
        for id in [
            "pilgrimage_vael",
            "outpost_veins",
            "citadel_shades",
            "smugglers_cache",
        ] {
            assert!(
                defs.iter().any(|d| d.id == id),
                "quest de cenário ausente: {id} (um JSON do disco deixou de parsear?)"
            );
        }
        // 21 do jogo original + 4 dos cenários. Um ficheiro que caia leva
        // várias de uma vez, portanto o piso apanha a classe.
        assert!(
            defs.len() >= 25,
            "só {} quests carregadas (21 + 4 dos cenários esperadas) — um JSON do disco caiu",
            defs.len()
        );
    }

    /// O loader de dir nunca pânica: dir ausente = vazio + warn; JSON maluco
    /// = skip do ficheiro; a ordem é SORTED por nome de ficheiro.
    #[test]
    fn test_load_quests_from_dir_is_tolerant_and_sorted() {
        // Dir ausente: vazio, sem pânico.
        let empty = load_quests_from_dir(Path::new("/não/existe/quests"));
        assert!(empty.is_empty(), "dir ausente → vazio");

        let dir = tempfile::tempdir().unwrap();
        // "b" malformado tem de ser saltado; "a" e "c" entram; um .txt é
        // ignorado. Ordem esperada: a (2 defs) + c (1 def) = 3.
        std::fs::write(
            dir.path().join("b_broken.json"),
            r#"[{"id": "sem_biome_e_sem_fecho"}"#,
        )
        .unwrap();
        std::fs::write(
            dir.path().join("a_quests.json"),
            r#"[
                {"id":"q1","npc":"marco","biome":"city","title":"Um","objective":{"type":"kill","target":"wolf","count":1}},
                {"id":"q2","npc":"notice_board","biome":"city","title":"Dois","objective":{"type":"collect","target":"stone","count":2}}
            ]"#,
        )
        .unwrap();
        std::fs::write(
            dir.path().join("c_quests.json"),
            r#"[{"id":"q3","npc":"marco","biome":"swamp","title":"Três","objective":{"type":"visit","target":"torre","count":1}}]"#,
        )
        .unwrap();
        std::fs::write(dir.path().join("notas.txt"), "ignorado").unwrap();

        let defs = load_quests_from_dir(dir.path());
        let ids: Vec<&str> = defs.iter().map(|d| d.id.as_str()).collect();
        assert_eq!(ids, vec!["q1", "q2", "q3"], "skip do JSON malformado + ordem sorted");
    }
}
