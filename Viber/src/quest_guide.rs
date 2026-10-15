//! Guia de missões: PARA ONDE ir agora — a seta de rumo do HUD
//! (`<WaypointArrow>`) e os "faróis" do minimapa/bússola leem daqui.
//!
//! Prioridade do alvo (uma pergunta, uma resposta):
//!
//! 1. **Entregar** — uma missão pronta (`ready`) → o NPC que a deu;
//! 2. **Cumprir** — a missão seguida no tracker (a 1.ª ativa):
//!    `visit` → o marco por visitar mais próximo; `kill` → a criatura viva
//!    desse tipo mais próxima (a zona de caça honesta);
//! 3. **Nova missão** — sem nada ativo → o NPC com missão por aceitar mais
//!    próximo.
//!
//! Os faróis são os NPC que valem a pena (missão por aceitar ou por
//! entregar): antes o minimapa e a bússola mostravam TODOS os NPC de missão,
//! incluindo as já acabadas — um mapa cheio de "!" mortos.

use std::collections::HashMap;

use bevy::prelude::*;

use crate::luau::LuaScriptRef;
use crate::player::Player;
use crate::quests::{QuestLog, QuestStatus, normalize_target};
use crate::recipes::spawn::DialogueNpc;
use crate::vitals::Health;

/// Recalcular o guia a esta cadência (s) — o alvo não muda a 60 Hz.
const GUIDE_PERIOD: f32 = 0.25;

/// O que o alvo representa (muda o glifo/cor da seta).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GuideKind {
    TurnIn,
    Objective,
    NewQuest,
}

/// Alvo atual do guia.
#[derive(Debug, Clone, PartialEq)]
pub struct GuideTarget {
    pub position: Vec3,
    pub kind: GuideKind,
    /// Texto curto da legenda (título da missão).
    pub label: String,
}

/// O guia, recalculado a cada [`GUIDE_PERIOD`].
#[derive(Resource, Debug, Default, Clone)]
pub struct QuestGuide {
    pub target: Option<GuideTarget>,
    /// NPC com missão por aceitar ou por entregar (minimapa/bússola).
    pub beacons: Vec<Vec3>,
}

/// Posições dos marcos de visita por nome normalizado — resolvidas UMA vez
/// por alvo (varrer 80k nomes a cada recálculo custava frames).
#[derive(Default)]
struct VisitCache(HashMap<String, Option<Vec3>>);

pub struct QuestGuidePlugin;

impl Plugin for QuestGuidePlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<QuestGuide>()
            .add_systems(Update, update_quest_guide);
    }
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn update_quest_guide(
    time: Res<Time>,
    mut clock: Local<f32>,
    mut cache: Local<VisitCache>,
    log: Option<Res<QuestLog>>,
    vault: Option<Res<crate::economy::Vault>>,
    mut guide: ResMut<QuestGuide>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc)>,
    named: Query<(&Name, &GlobalTransform), Without<Player>>,
    creatures: Query<(&GlobalTransform, &LuaScriptRef, &Health), Without<Player>>,
) {
    *clock -= time.delta_secs();
    if *clock > 0.0 {
        return;
    }
    *clock = GUIDE_PERIOD;
    let (Some(log), Some(player)) = (log.as_deref(), players.iter().next()) else {
        if guide.target.is_some() || !guide.beacons.is_empty() {
            *guide = QuestGuide::default();
        }
        return;
    };
    let here = player.translation();
    let vault = vault.as_deref();
    let status = |id: &str| log.status(id, vault);

    // Onde está quem deu cada missão: o `<DialogueNPC dialogue-id>` ou, para
    // dadores sem diálogo nativo (o quadro de avisos), a entidade com `name`
    // igual ao `npc` da missão (em cache: são estáticos).
    let mut givers: HashMap<String, Vec3> = npcs
        .iter()
        .map(|(t, npc)| (npc.dialogue_id.clone(), t.translation()))
        .collect();
    for def in &log.defs {
        if givers.contains_key(&def.id) || !matches!(status(&def.id), QuestStatus::Ready) {
            continue;
        }
        let key = normalize_target(&def.npc);
        if !cache.0.contains_key(&key) {
            let found = named
                .iter()
                .find(|(name, _)| name.as_str() == def.npc)
                .map(|(_, t)| t.translation());
            cache.0.insert(key.clone(), found);
        }
        if let Some(p) = cache.0.get(&key).copied().flatten() {
            givers.insert(def.id.clone(), p);
        }
    }
    let giver_of = |quest: &str| givers.get(quest).copied();
    let nearest = |points: &mut dyn Iterator<Item = Vec3>| {
        points.min_by(|a, b| {
            a.distance_squared(here)
                .total_cmp(&b.distance_squared(here))
        })
    };

    let mut beacons: Vec<Vec3> = npcs
        .iter()
        .filter(|(_, npc)| {
            log.def(&npc.dialogue_id).is_some()
                && matches!(
                    status(&npc.dialogue_id),
                    QuestStatus::NotTaken | QuestStatus::Ready
                )
        })
        .map(|(t, _)| t.translation())
        .collect();

    let ready = log
        .defs
        .iter()
        .filter(|d| status(&d.id) == QuestStatus::Ready)
        .filter_map(|d| giver_of(&d.id).map(|p| (p, d.title.clone())))
        .min_by(|(a, _), (b, _)| {
            a.distance_squared(here)
                .total_cmp(&b.distance_squared(here))
        });
    for (p, _) in log
        .defs
        .iter()
        .filter(|d| d.npc == "notice_board" && status(&d.id) == QuestStatus::Ready)
        .filter_map(|d| giver_of(&d.id).map(|p| (p, ())))
    {
        beacons.push(p);
    }

    let target = if let Some((position, label)) = ready {
        Some(GuideTarget {
            position,
            kind: GuideKind::TurnIn,
            label,
        })
    } else if let Some(def) = log
        .defs
        .iter()
        .find(|d| status(&d.id) == QuestStatus::Active)
    {
        let position = match def.objective.kind.as_str() {
            "visit" => {
                let visited = log
                    .states
                    .get(&def.id)
                    .map(|a| a.visited.clone())
                    .unwrap_or_default();
                let wanted: Vec<String> = def
                    .objective
                    .target
                    .split_whitespace()
                    .map(normalize_target)
                    .filter(|t| !visited.contains(t))
                    .collect();
                for key in &wanted {
                    if !cache.0.contains_key(key) {
                        let found = named
                            .iter()
                            .find(|(name, _)| normalize_target(name.as_str()) == *key)
                            .map(|(_, t)| t.translation());
                        cache.0.insert(key.clone(), found);
                    }
                }
                nearest(
                    &mut wanted
                        .iter()
                        .filter_map(|k| cache.0.get(k).copied().flatten()),
                )
            }
            "kill" => nearest(
                &mut creatures
                    .iter()
                    .filter(|(_, script, hp)| {
                        hp.current > 0.0
                            && crate::quests::kill_matches(
                                &crate::combat::script_kind(&script.path),
                                &def.objective.target,
                            )
                    })
                    .map(|(t, _, _)| t.translation()),
            ),
            _ => None,
        };
        let verb = match def.objective.kind.as_str() {
            "visit" => "Visitar",
            "kill" => "Caçar",
            _ => "Objetivo",
        };
        position.map(|position| GuideTarget {
            position,
            kind: GuideKind::Objective,
            label: verb.into(),
        })
    } else {
        nearest(&mut beacons.iter().copied()).map(|position| GuideTarget {
            position,
            kind: GuideKind::NewQuest,
            label: "Nova missão".into(),
        })
    };
    guide.target = target;
    guide.beacons = beacons;
}

/// `enemies/wolf.lua` → `wolf`; `bosses/sand-worm.lua` → `sandworm` (o mesmo
/// normalizador dos alvos de missão: `boss_` cai, `-`/`_` também).
pub fn script_kind(path: &str) -> String {
    let stem = path
        .rsplit('/')
        .next()
        .unwrap_or(path)
        .trim_end_matches(".lua");
    normalize_target(stem)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn script_kind_matches_quest_targets() {
        assert_eq!(script_kind("enemies/wolf.lua"), normalize_target("wolf"));
        assert_eq!(
            script_kind("bosses/bog-warden.lua"),
            normalize_target("boss_bogwarden")
        );
        assert_eq!(script_kind("goblin.lua"), "goblin");
    }
}
