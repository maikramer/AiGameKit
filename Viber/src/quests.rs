//! Quests & diálogo (loop 3 do port simple-rpg) — o análogo nativo do
//! plugin Quests do VibeGame:
//!
//! - **Dados**: as 21 quests dos JSONs (`examples/simple-rpg/quests/*.json`)
//!   embutidas via `include_str!` — mesmo schema do jogo browser.
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

use bevy::prelude::*;
use serde::Deserialize;

use crate::hud::HudBalloon;
use crate::luau::{LuaScriptRef, ScriptInteraction, ScriptToast};
use crate::vitals::Health;
use crate::player::Player;
use crate::vitals::Xp;

/// Alcance do diálogo com `<DialogueNPC>` (mesmo do prompt do HUD).
pub const DIALOGUE_RANGE_M: f32 = 3.5;
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

const QUEST_JSONS: [&str; 5] = [
    include_str!("../examples/simple-rpg/quests/city_quests.json"),
    include_str!("../examples/simple-rpg/quests/dark_forest_quests.json"),
    include_str!("../examples/simple-rpg/quests/desert_quests.json"),
    include_str!("../examples/simple-rpg/quests/mountain_quests.json"),
    include_str!("../examples/simple-rpg/quests/swamp_quests.json"),
];

/// Parseia todos os JSONs embutidos (falha de parse = warn + skip; o resto
/// do jogo continua).
pub fn load_quests() -> Vec<QuestDef> {
    let mut defs = Vec::new();
    for (path, json) in QUEST_JSONS.iter().enumerate() {
        match serde_json::from_str::<Vec<QuestDef>>(json) {
            Ok(mut list) => defs.append(&mut list),
            Err(error) => warn!(target: "viber::quests", "quest json #{path}: {error}"),
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
    states: HashMap<String, ActiveQuest>,
    done: Vec<String>,
}

impl Default for QuestLog {
    fn default() -> Self {
        Self {
            defs: load_quests(),
            states: HashMap::new(),
            done: Vec::new(),
        }
    }
}

impl QuestLog {
    pub fn def(&self, id: &str) -> Option<&QuestDef> {
        self.defs.iter().find(|d| d.id == id)
    }

    /// Estado computado (Ready = ativa com objetivo completo).
    pub fn status(&self, id: &str) -> QuestStatus {
        if self.done.iter().any(|d| d == id) {
            return QuestStatus::Done;
        }
        let Some((def, active)) = self.def(id).zip(self.states.get(id)) else {
            return QuestStatus::NotTaken;
        };
        if self.is_complete(def, active) {
            QuestStatus::Ready
        } else {
            QuestStatus::Active
        }
    }

    fn is_complete(&self, def: &QuestDef, active: &ActiveQuest) -> bool {
        match def.objective.kind.as_str() {
            "visit" => active.visited.len() >= def.objective.count as usize,
            _ => active.progress >= def.objective.count,
        }
    }

    /// Aceita (NotTaken → Active). `false` se não existe ou já está ativa/feita.
    pub fn accept(&mut self, id: &str) -> bool {
        if self.status(id) != QuestStatus::NotTaken {
            return false;
        }
        self.states.insert(id.into(), ActiveQuest::default());
        true
    }

    /// Entrega (Ready → Done; repetíveis voltam a NotTaken). Devolve as
    /// recompensas quando a entrega aconteceu.
    pub fn turn_in(&mut self, id: &str) -> Option<QuestRewards> {
        if self.status(id) != QuestStatus::Ready {
            return None;
        }
        let (rewards, repeatable) = {
            let def = self.def(id)?;
            (def.rewards.clone(), def.npc == "notice_board")
        };
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

    /// Aplica progresso de kill/collect às quests ativas com esse alvo;
    /// devolve os ids que ficaram Ready agora.
    pub fn report_progress(&mut self, target: &str, amount: u32) -> Vec<String> {
        let wanted = normalize_target(target);
        let candidates: Vec<(String, u32)> = self
            .defs
            .iter()
            .filter(|d| {
                (d.objective.kind == "kill" || d.objective.kind == "collect")
                    && normalize_target(&d.objective.target) == wanted
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
    pub fn active_ids(&self) -> Vec<String> {
        self.defs
            .iter()
            .filter(|d| matches!(self.status(&d.id), QuestStatus::Active | QuestStatus::Ready))
            .map(|d| d.id.clone())
            .collect()
    }

    /// Texto "x/y" do objetivo (ou "2/3 marcos" para visit).
    pub fn progress_text(&self, id: &str) -> String {
        let (Some(def), Some(active)) = (self.def(id), self.states.get(id)) else {
            return String::new();
        };
        match def.objective.kind.as_str() {
            "visit" => format!("{}/{}", active.visited.len(), def.objective.count),
            _ => format!("{}/{}", active.progress.min(def.objective.count), def.objective.count),
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
            .add_systems(Startup, spawn_tracker)
            .add_systems(
                Update,
                (
                    quest_dialogue_system,
                    quest_visit_system,
                    quest_tracker_system,
                    toast_log_system,
                    quest_debug_teleport,
                    quest_debug_nearest,
                    quest_debug_hostile,
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
    let y = terrain
        .as_ref()
        .map(|t| t.sample(x, z))
        .unwrap_or(target.y);
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
        (&GlobalTransform, Option<&ScriptInteraction>),
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
    let Some(target) = creatures
        .iter()
        .filter(|(_, interaction)| interaction.is_none())
        .min_by(|(a, _), (b, _)| {
            a.translation()
                .distance_squared(player_pos)
                .total_cmp(&b.translation().distance_squared(player_pos))
        })
        .map(|(t, _)| t.translation())
    else {
        toasts.write(ScriptToast("QA: nenhuma criatura hostil ativa".into()));
        return;
    };
    let x = target.x + 1.8;
    let z = target.z + 1.8;
    let y = terrain
        .as_ref()
        .map(|t| t.sample(x, z))
        .unwrap_or(target.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast("QA: teleport à criatura mais próxima".into()));
}

/// Espelha toasts no log (bridge) — o DISPLAY visual chega com o loop 5.
fn toast_log_system(mut toasts: MessageReader<ScriptToast>) {
    for toast in toasts.read() {
        info!(target: "viber::toast", "{}", toast.0);
    }
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
    let y = terrain
        .as_ref()
        .map(|t| t.sample(x, z))
        .unwrap_or(target.y);
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
    npcs: Query<(&GlobalTransform, &crate::recipes::spawn::DialogueNpc)>,
    mut log: ResMut<QuestLog>,
    mut heroes: Query<&mut Xp, With<Player>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut balloons: Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    mut texts: Query<&mut Text>,
) {
    if !keys.just_pressed(KeyCode::KeyE) {
        return;
    }
    let Some(player) = players.iter().next() else {
        return;
    };
    let Some((_, npc)) = npcs
        .iter()
        .find(|(t, _)| t.translation().distance(player.translation()) < DIALOGUE_RANGE_M)
    else {
        return;
    };
    let id = npc.dialogue_id.clone();
    info!(target: "viber::quests", "diálogo [E] com '{id}' — estado {}", crate::quests::status_name(log.status(&id)));
    let body: String = match log.status(&id) {
        QuestStatus::NotTaken => {
            log.accept(&id);
            info!(target: "viber::quests", "quest '{id}' aceita via diálogo");
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
                    d.objective.count,
                    d.title.clone(),
                )
            });
            let Some((progress_lines, complete_lines, count, title)) = snapshot else {
                return;
            };
            if log.status(&id) == QuestStatus::Ready {
                info!(target: "viber::quests", "entrega de '{id}'");
                if let Some(rewards) = log.turn_in(&id) {
                        if let Ok(mut xp) = heroes.single_mut() {
                            crate::vitals::gain_xp(&mut xp, rewards.xp);
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
                }
                join_lines(&complete_lines)
            } else {
                let remaining = count.saturating_sub(
                    log.states.get(&id).map(|a| a.progress).unwrap_or(0),
                );
                join_lines(&progress_lines).replace("{remaining}", &remaining.to_string())
            }
        }
        QuestStatus::Done => join_lines(
            &log.def(&id)
                .map(|d| d.lines_complete.clone())
                .unwrap_or_default(),
        ),
    };
    show_balloon(&mut balloons, &mut texts, &body);
}

fn join_lines(lines: &[String]) -> String {
    lines.join("\n")
}

/// Mostra o balão do HUD com `body` (mesmo mecanismo do hud: timer de 4 s).
fn show_balloon(
    balloons: &mut Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    texts: &mut Query<&mut Text>,
    body: &str,
) {
    for (mut visibility, mut balloon, children) in balloons.iter_mut() {
        balloon.timer = crate::hud::BALLOON_DURATION;
        *visibility = Visibility::Visible;
        if let Some(child) = children.first() {
            if let Ok(mut text) = texts.get_mut(*child) {
                text.0 = body.into();
            }
        }
    }
}

/// Visit quests: proximidade a entidades com o nome do alvo (throttle 0,5 s).
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
    let visit_targets: Vec<(String, Vec<String>)> = log
        .defs
        .iter()
        .filter(|d| d.objective.kind == "visit" && log.status(&d.id) == QuestStatus::Active)
        .map(|d| {
            (
                d.id.clone(),
                d.objective
                    .target
                    .split_whitespace()
                    .map(normalize_target)
                    .collect(),
            )
        })
        .collect();
    if visit_targets.is_empty() {
        return;
    }
    for (name, transform) in &named {
        let name_norm = normalize_target(name);
        if transform.translation().distance(player_pos) > VISIT_RADIUS_M {
            continue;
        }
        for (_id, targets) in &visit_targets {
            if !targets.contains(&name_norm) {
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
    let active = log.active_ids();
    for (i, child) in children.iter().enumerate() {
        let Ok(mut text) = texts.get_mut(child) else {
            continue;
        };
        let wanted = active
            .get(i)
            .and_then(|id| log.def(id))
            .map(|def| format!("{}  [{}]", def.title, log.progress_text(&def.id)))
            .unwrap_or_default();
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn log() -> QuestLog {
        let log = QuestLog::default();
        assert!(log.defs.len() >= 21, "21 quests carregadas, {:?}", log.defs.len());
        log
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
    fn test_normalize_target_matches_boss_aliases() {
        assert_eq!(normalize_target("boss_bogwarden"), normalize_target("Bog-Warden"));
        assert_eq!(normalize_target("Wolf"), normalize_target("wolf"));
        assert_ne!(normalize_target("wolf"), normalize_target("shade"));
    }

    #[test]
    fn test_kill_quest_lifecycle() {
        let mut log = log();
        assert_eq!(log.status("forest_wolves"), QuestStatus::NotTaken);
        assert!(log.accept("forest_wolves"));
        assert_eq!(log.status("forest_wolves"), QuestStatus::Active);
        // aceitar duas vezes falha
        assert!(!log.accept("forest_wolves"));
        // 4 de 5 lobos…
        for _ in 0..4 {
            assert!(log.report_kill("wolf").is_empty());
        }
        assert_eq!(log.status("forest_wolves"), QuestStatus::Active);
        assert_eq!(log.progress_text("forest_wolves"), "4/5");
        // …o 5.º fica pronto
        assert_eq!(log.report_kill("wolf"), vec!["forest_wolves".to_string()]);
        assert_eq!(log.status("forest_wolves"), QuestStatus::Ready);
        // entregar: one-shot vira Done
        let rewards = log.turn_in("forest_wolves").expect("recompensas");
        assert_eq!(rewards.xp, 150);
        assert_eq!(log.status("forest_wolves"), QuestStatus::Done);
        assert!(log.turn_in("forest_wolves").is_none());
    }

    #[test]
    fn test_notice_board_bounty_is_repeatable() {
        let mut log = log();
        log.accept("city_wolves");
        for _ in 0..3 {
            log.report_kill("wolf");
        }
        assert_eq!(log.status("city_wolves"), QuestStatus::Ready);
        let rewards = log.turn_in("city_wolves").expect("entrega");
        assert_eq!(rewards.gold, 80);
        // repetível: volta a NotTaken (o cartaz volta à tábua)
        assert_eq!(log.status("city_wolves"), QuestStatus::NotTaken);
        assert!(log.accept("city_wolves"));
    }

    #[test]
    fn test_visit_quest_multiple_targets() {
        let mut log = log();
        log.accept("forest_survey");
        assert_eq!(log.status("forest_survey"), QuestStatus::Active);
        assert_eq!(log.progress_text("forest_survey"), "0/3");
        // nomes com variações normalizam
        assert!(log.report_visit("Forest-Outpost-Tower").is_empty());
        assert_eq!(log.progress_text("forest_survey"), "1/3");
        assert!(log.report_visit("forest-outpost-tower").is_empty(), "revisita não duplica");
        assert!(log.report_visit("forest-crossroads-well").is_empty());
        assert_eq!(
            log.report_visit("forest-stone-circle"),
            vec!["forest_survey".to_string()]
        );
        assert_eq!(log.status("forest_survey"), QuestStatus::Ready);
    }

    #[test]
    fn test_collect_quest_via_progress() {
        let mut log = log();
        log.accept("city_stone");
        assert!(log.report_progress("stone", 7).is_empty());
        assert_eq!(log.progress_text("city_stone"), "7/10");
        assert_eq!(
            log.report_progress("Stone", 3),
            vec!["city_stone".to_string()]
        );
        assert_eq!(log.status("city_stone"), QuestStatus::Ready);
    }

    #[test]
    fn test_tracker_lists_active_in_def_order() {
        let mut log = log();
        log.accept("forest_wolves");
        log.accept("city_wolves");
        let active = log.active_ids();
        assert_eq!(active.len(), 2);
        // ordem dos defs: city primeiro (city_quests carregado antes)
        assert_eq!(active[0], "city_wolves");
        assert_eq!(active[1], "forest_wolves");
    }
}
