//! Travel, A Nota & wayfinding (loop 6 do port simple-rpg) — o análogo
//! nativo de `nota.ts` + `travel.ts` + wayfinding do VibeGame:
//!
//! - **A Nota**: 12 marcos de traçado (3 por bioma, por `name=` de entidade).
//!   [F] perto de um marco não assinado → "Medido e assinado"; os 3 de um
//!   bioma → toast "bioma assinado". Guardado em [`NotaLog`] (persistência
//!   vem no loop 7).
//! - **Viagem rápida**: perto da fogueira (praça), **[G]** abre o painel de
//!   viagem aos marcos assinados; ↑↓ seleciona, [J] teleporta.
//! - **Waypoint**: o último marco assinado vira waypoint — linha no topo do
//!   ecrã com rumo (N/NE/…) e distância.
//! - **EnemyRegistry**: criaturas hostis vivas por banda do mundo
//!   (centro/norte/sul/este/oeste), snapshot para scripts via
//!   `viber.alive_in_region(idx)` — o gating do boss final vive no
//!   `boss.lua` (dorme enquanto o sul não estiver limpo).
//! - QA: **F11** teleporta ao próximo marco por assinar.
//!
//! Aggro-chain: `AttackAlert` (feedback) → `on_player_attack(px, pz)` nos
//! scripts num raio de 15 m do alvo atingido.

use std::collections::HashSet;

use bevy::prelude::*;

use crate::luau::ScriptToast;
use crate::player::Player;

/// Alcance para assinar um marco (m) — `NOTA_MARK_RADIUS` por bioma.
pub const NOTA_RANGE_DEFAULT_M: f32 = 12.0;
/// Alcance do painel de viagem à fogueira (a praça toda).
pub const TRAVEL_CAMPFIRE_RANGE_M: f32 = 14.0;
/// Raio do alerta a aliados (aggro-chain, m).
pub const ALERT_RADIUS_M: f32 = 15.0;

// ── catálogo da Nota (espelha nota-landmarks.ts) ────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Biome {
    DarkForest,
    Desert,
    Swamp,
    FrozenPeaks,
}

impl Biome {
    pub fn label(self) -> &'static str {
        match self {
            Biome::DarkForest => "Floresta Sombria",
            Biome::Desert => "Deserto",
            Biome::Swamp => "Pântano",
            Biome::FrozenPeaks => "Picos Gelados",
        }
    }

    /// Quest de traçado do bioma (id no JSON).
    pub fn survey_quest(self) -> &'static str {
        match self {
            Biome::DarkForest => "forest_survey",
            Biome::Desert => "desert_survey",
            Biome::Swamp => "swamp_survey",
            Biome::FrozenPeaks => "peaks_survey",
        }
    }

    pub fn mark_radius(self) -> f32 {
        match self {
            Biome::DarkForest => 10.0,
            Biome::Desert => 12.0,
            Biome::Swamp => 11.0,
            Biome::FrozenPeaks => 9.0,
        }
    }
}

/// Marco de traçado: (name da entidade, bioma, rótulo legível).
pub struct NotaLandmark {
    pub name: &'static str,
    pub biome: Biome,
    pub label: &'static str,
}

/// Os 12 marcos — espelham os `objective.target` das quests `*_survey`.
pub const LANDMARKS: [NotaLandmark; 12] = [
    NotaLandmark { name: "forest-outpost-tower", biome: Biome::DarkForest, label: "Torre do Posto Avançado" },
    NotaLandmark { name: "forest-crossroads-well", biome: Biome::DarkForest, label: "Poço da Encruzilhada" },
    NotaLandmark { name: "forest-stone-circle", biome: Biome::DarkForest, label: "Círculo de Menires" },
    NotaLandmark { name: "desert-arch", biome: Biome::Desert, label: "Arco do Deserto" },
    NotaLandmark { name: "desert-caravan-wreck", biome: Biome::Desert, label: "Caravana Encalhada" },
    NotaLandmark { name: "desert-sun-obelisk", biome: Biome::Desert, label: "Obelisco do Sol" },
    NotaLandmark { name: "swamp-wrecked-boat", biome: Biome::Swamp, label: "Barco Naufragado" },
    NotaLandmark { name: "swamp-sunken-graves", biome: Biome::Swamp, label: "Covas Submersas" },
    NotaLandmark { name: "swamp-bone-altar", biome: Biome::Swamp, label: "Altar de Ossos" },
    NotaLandmark { name: "peaks-cairn-1", biome: Biome::FrozenPeaks, label: "Primeiro Mojão" },
    NotaLandmark { name: "peaks-cairn-2", biome: Biome::FrozenPeaks, label: "Segundo Mojão" },
    NotaLandmark { name: "peaks-cairn-3", biome: Biome::FrozenPeaks, label: "Terceiro Mojão" },
];

pub fn landmark_by_name(name: &str) -> Option<&'static NotaLandmark> {
    LANDMARKS.iter().find(|l| l.name == name)
}

/// Bandas do mundo para o registry de hostis (índice = `alive_in_region`).
pub const REGIONS: [(&str, f32, f32, f32, f32); 5] = [
    // (rótulo, min_x, min_z, max_x, max_z)
    ("centro", -120.0, -120.0, 120.0, 120.0),
    ("norte", -2000.0, 120.0, 2000.0, 2000.0),
    ("sul", -2000.0, -2000.0, 2000.0, -120.0),
    ("este", 120.0, -2000.0, 2000.0, 2000.0),
    ("oeste", -2000.0, -2000.0, -120.0, 2000.0),
];

/// Índice da região que contém o ponto.
pub fn region_of(x: f32, z: f32) -> usize {
    REGIONS
        .iter()
        .position(|(_, x0, z0, x1, z1)| x >= *x0 && x <= *x1 && z >= *z0 && z <= *z1)
        .unwrap_or(0)
}

// ── estado ──────────────────────────────────────────────────────────────

/// A Nota: marcos assinados (persistência no loop 7).
#[derive(Debug, Clone, Resource, Default)]
pub struct NotaLog {
    pub marked: HashSet<String>,
}

/// Waypoint atual (último marco assinado).
#[derive(Debug, Clone, Resource, Default)]
pub struct Waypoint {
    pub label: Option<&'static str>,
    pub position: Option<Vec3>,
}

/// Hostis vivos por banda do mundo (índice = [`REGIONS`]).
#[derive(Debug, Clone, Resource, Default)]
pub struct EnemyRegistry {
    pub counts: [u32; 5],
}

impl EnemyRegistry {
    /// Puro: conta quantas posições caem em cada banda.
    pub fn count_positions(positions: &[(f32, f32)]) -> [u32; 5] {
        let mut counts = [0_u32; 5];
        for &(x, z) in positions {
            counts[region_of(x, z)] += 1;
        }
        counts
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct TravelPlugin;

impl Plugin for TravelPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<NotaLog>()
            .init_resource::<Waypoint>()
            .init_resource::<EnemyRegistry>()
            .init_resource::<TravelMenuState>()
            .add_message::<TravelPing>()
            .add_systems(Startup, (spawn_travel_menu, spawn_waypoint_hud))
            .add_systems(
                Update,
                (
                    nota_measure_system,
                    travel_menu_system,
                    waypoint_hud_system,
                    enemy_registry_system,
                    quest_debug_landmark,
                ),
            );
    }
}

/// Pedido de viagem (puro p/ testes do menu).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct TravelPing {
    pub label: &'static str,
}

// ── A Nota: medir e assinar [F] ─────────────────────────────────────────

/// [F] perto de um marco não assinado → assina; 3 do bioma → bioma assinado.
#[allow(clippy::too_many_arguments)]
fn nota_measure_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    mut nota: ResMut<NotaLog>,
    mut waypoint: ResMut<Waypoint>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.3;
    // just_pressed (não pressed): o tap sintético da bridge faz press+release
    // no mesmo lote, e `pressed` já voltou a false quando o Update corre.
    if !keys.just_pressed(KeyCode::KeyF) {
        return;
    }
    let Some(player) = players.iter().next() else {
        return;
    };
    let player_pos = player.translation();
    let Some((name, _)) = named
        .iter()
        .find(|(name, t)| {
            landmark_by_name(name)
                .filter(|l| !nota.marked.contains(l.name))
                .filter(|l| {
                    t.translation().distance(player_pos) <= l.biome.mark_radius()
                })
                .is_some()
        })
    else {
        return;
    };
    let name = name.to_string();
    let Some(landmark) = landmark_by_name(&name) else {
        return;
    };
    nota.marked.insert(name.clone());
    waypoint.label = Some(landmark.label);
    waypoint.position = None; // re-preso na próxima passada (abaixo)
    for (entity_name, transform) in &named {
        if entity_name.to_string() == name {
            waypoint.position = Some(transform.translation());
            break;
        }
    }
    let remaining_in_biome = LANDMARKS
        .iter()
        .filter(|l| l.biome == landmark.biome && !nota.marked.contains(l.name))
        .count();
    if remaining_in_biome == 0 {
        toasts.write(ScriptToast(format!(
            "Medido e assinado: {} — {} ASSINADO!",
            landmark.label,
            landmark.biome.label().to_uppercase()
        )));
    } else {
        toasts.write(ScriptToast(format!(
            "Medido e assinado: {} (faltam {remaining_in_biome} em {})",
            landmark.label,
            landmark.biome.label()
        )));
    }
    info!(target: "viber::nota", "marco '{name}' assinado");
}

// ── viagem rápida [G] na fogueira ───────────────────────────────────────

#[derive(Component)]
struct TravelMenu;

#[derive(Component)]
struct TravelContent;

fn spawn_travel_menu(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45)),
            Visibility::Hidden,
            Name::new("ui:travel"),
            TravelMenu,
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    width: Val::Px(480.0),
                    height: Val::Px(280.0),
                    padding: UiRect::all(Val::Px(20.0)),
                    flex_direction: FlexDirection::Column,
                    row_gap: Val::Px(8.0),
                    border_radius: BorderRadius::all(Val::Px(14.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.96)),
                BorderColor::all(Color::srgb(0.95, 0.65, 0.3)),
            ))
            .with_children(|panel| {
                panel.spawn((
                    Text::new("VIAJAR (fogueira da praça)"),
                    TextColor(Color::srgb(0.98, 0.8, 0.5)),
                    TextFont::from_font_size(20.0),
                ));
                panel.spawn((
                    Text::new(""),
                    TextColor(Color::srgb(0.9, 0.88, 0.8)),
                    TextFont::from_font_size(15.0),
                    TravelContent,
                ));
            });
        });
}

/// Estado de abertura do menu de viagem (lido pelo QA F11 e pelo HUD).
#[derive(Debug, Clone, Resource, Default)]
pub struct TravelMenuState {
    pub open: bool,
    pub selection: usize,
}

/// [G] perto da fogueira abre; ↑↓ seleciona marcos assinados; [J] viaja.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn travel_menu_system(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut player_transforms: Query<&mut Transform, With<Player>>,
    camps: Query<&GlobalTransform, Without<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    nota: Res<NotaLog>,
    mut state: ResMut<TravelMenuState>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut q_menu: Query<&mut Visibility, With<TravelMenu>>,
    mut q_content: Query<&mut Text, With<TravelContent>>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    let near_campfire = players.iter().next().is_some_and(|player| {
        camps
            .iter()
            .any(|c| c.translation().distance(player.translation()) < TRAVEL_CAMPFIRE_RANGE_M)
    });

    if keys.just_pressed(KeyCode::KeyG) && (near_campfire || state.open) {
        state.open = !state.open;
        state.selection = 0;
    }
    if state.open && !near_campfire {
        state.open = false;
    }
    for mut visibility in q_menu.iter_mut() {
        let wanted = if state.open {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }

    let marked: Vec<(&'static str, &'static str)> = LANDMARKS
        .iter()
        .filter(|l| nota.marked.contains(l.name))
        .map(|l| (l.name, l.label))
        .collect();

    if !state.open {
        return;
    }
    if marked.is_empty() {
        for mut text in q_content.iter_mut() {
            let wanted = "Nenhum marco assinado ainda — [F] junto aos marcos.".into();
            if text.0 != wanted {
                text.0 = wanted;
            }
        }
        return;
    }
    if keys.just_pressed(KeyCode::ArrowDown) || keys.just_pressed(KeyCode::KeyS) {
        state.selection = (state.selection + 1) % marked.len();
    }
    if keys.just_pressed(KeyCode::ArrowUp) || keys.just_pressed(KeyCode::KeyW) {
        state.selection = (state.selection + marked.len() - 1) % marked.len();
    }

    // viajar
    if keys.just_pressed(KeyCode::KeyJ) {
        if let Some((name, label)) = marked.get(state.selection) {
            let target = named
                .iter()
                .find(|(name_entity, _)| name_entity.to_string() == *name)
                .map(|(_, t)| t.translation());
            if let Ok(mut transform) = player_transforms.single_mut() {
                if let Some(pos) = target {
                    let x = pos.x + 2.0;
                    let z = pos.z + 2.0;
                    let y = terrain
                        .as_ref()
                        .map(|t| t.sample(x, z))
                        .unwrap_or(pos.y);
                    transform.translation = Vec3::new(x, y + 0.1, z);
                    toasts.write(ScriptToast(format!("A viajar para {label}…")));
                    state.open = false;
                }
            }
        }
    }

    let mut lines = vec![String::new()];
    for (i, (_, label)) in marked.iter().enumerate() {
        let marker = if i == state.selection { ">" } else { " " };
        lines.push(format!("{marker} {label}"));
    }
    lines.push(String::new());
    lines.push("[J] viajar · ↑↓ escolher · [G] sair".into());
    for mut text in q_content.iter_mut() {
        let wanted = lines.join("\n");
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
    let _ = terrain;
}

// ── waypoint HUD ────────────────────────────────────────────────────────

fn spawn_waypoint_hud(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(64.0),
            left: Val::Px(0.0),
            right: Val::Px(0.0),
            justify_content: JustifyContent::Center,
            ..Default::default()
        },
        Visibility::Hidden,
        Name::new("ui:waypoint"),
        WaypointHud,
    ))
    .with_children(|wrap| {
        wrap.spawn((
            Text::new(""),
            TextColor(Color::srgb(0.98, 0.8, 0.5)),
            TextFont::from_font_size(14.0),
            WaypointText,
        ));
    });
}

#[derive(Component)]
struct WaypointHud;

#[derive(Component)]
struct WaypointText;

/// Rumo cardeal a partir de um delta (dx, dz), norte = -Z.
pub fn bearing_label(dx: f32, dz: f32) -> &'static str {
    let angle = dx.atan2(-dz).to_degrees();
    match angle {
        a if (22.5..67.5).contains(&a) => "NE",
        a if (67.5..112.5).contains(&a) => "E",
        a if (112.5..157.5).contains(&a) => "SE",
        a if (157.5..=180.0).contains(&a) || (-180.0..-157.5).contains(&a) => "S",
        a if (-157.5..-112.5).contains(&a) => "SO",
        a if (-112.5..-67.5).contains(&a) => "O",
        a if (-67.5..-22.5).contains(&a) => "NO",
        _ => "N",
    }
}

/// Linha do waypoint: rumo + distância ao último marco assinado.
fn waypoint_hud_system(
    players: Query<&GlobalTransform, With<Player>>,
    waypoint: Res<Waypoint>,
    mut hud: Query<&mut Visibility, With<WaypointHud>>,
    mut texts: Query<&mut Text, With<WaypointText>>,
) {
    let Some(label) = waypoint.label else {
        return;
    };
    let Some(position) = waypoint.position else {
        return;
    };
    let Some(player) = players.iter().next() else {
        return;
    };
    let delta = position - player.translation();
    let distance = delta.length();
    for mut visibility in hud.iter_mut() {
        *visibility = Visibility::Visible;
    }
    for mut text in texts.iter_mut() {
        let wanted = format!(
            "{} · {} · {} m",
            label,
            bearing_label(delta.x, delta.z),
            distance.round() as i32
        );
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

// ── enemy registry ──────────────────────────────────────────────────────

/// Conta hostis vivos por banda (1 Hz) e espelha no ctx dos scripts.
fn enemy_registry_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    creatures: Query<(&GlobalTransform, &crate::vitals::Health), With<crate::luau::LuaScriptRef>>,
    mut registry: ResMut<EnemyRegistry>,
    host: ResMut<crate::luau::LuaScriptHost>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 1.0;
    let positions: Vec<(f32, f32)> = creatures
        .iter()
        .filter(|(_, hp)| hp.current > 0.0)
        .map(|(t, _)| (t.translation().x, t.translation().z))
        .collect();
    registry.counts = EnemyRegistry::count_positions(&positions);
    if let Some(mut ctx) = host.lua.app_data_mut::<crate::luau::ScriptCtx>() {
        ctx.alive_regions = registry.counts;
    }
}

// ── QA: F11 teleport ao próximo marco por assinar ───────────────────────

fn quest_debug_landmark(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform), With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    nota: Res<NotaLog>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !keys.just_pressed(KeyCode::F11) {
        return;
    }
    let Ok((_pe, player_global, mut transform)) = players.single_mut() else {
        return;
    };
    let player_pos = player_global.translation();
    let Some(target) = named
        .iter()
        .filter(|(name, _)| {
            landmark_by_name(name)
                .filter(|l| !nota.marked.contains(l.name))
                .is_some()
        })
        .min_by(|(_, a_t), (_, b_t)| {
            a_t.translation()
                .distance_squared(player_pos)
                .total_cmp(&b_t.translation().distance_squared(player_pos))
        })
        .map(|(name, t)| (name.to_string(), t.translation()))
    else {
        toasts.write(ScriptToast("QA: todos os marcos assinados".into()));
        return;
    };
    let x = target.1.x + 2.0;
    let z = target.1.z + 2.0;
    let y = terrain
        .as_ref()
        .map(|t| t.sample(x, z))
        .unwrap_or(target.1.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    toasts.write(ScriptToast(format!("QA: teleport ao marco {}", target.0)));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_catalog_12_landmarks_3_per_biome() {
        assert_eq!(LANDMARKS.len(), 12);
        for biome in [Biome::DarkForest, Biome::Desert, Biome::Swamp, Biome::FrozenPeaks] {
            let count = LANDMARKS.iter().filter(|l| l.biome == biome).count();
            assert_eq!(count, 3, "bioma {:?} com {count}", biome);
        }
        // nomes únicos
        let mut names: Vec<_> = LANDMARKS.iter().map(|l| l.name).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), 12);
    }

    #[test]
    fn test_survey_quests_exist_in_quest_log() {
        let log = crate::quests::QuestLog::default();
        for biome in [Biome::DarkForest, Biome::Desert, Biome::Swamp, Biome::FrozenPeaks] {
            assert!(
                log.def(biome.survey_quest()).is_some(),
                "quest de traçado {} em falta",
                biome.survey_quest()
            );
        }
    }

    #[test]
    fn test_region_of_points() {
        assert_eq!(region_of(0.0, 0.0), 0); // centro
        assert_eq!(region_of(0.0, 300.0), 1); // norte
        assert_eq!(region_of(0.0, -300.0), 2); // sul
        assert_eq!(region_of(500.0, 0.0), 3); // este
        assert_eq!(region_of(-500.0, 0.0), 4); // oeste
    }

    #[test]
    fn test_registry_counts_positions() {
        let counts = EnemyRegistry::count_positions(&[
            (0.0, 0.0),
            (0.0, 300.0),
            (0.0, 300.0),
            (-500.0, 0.0),
        ]);
        assert_eq!(counts, [1, 2, 0, 0, 1]);
    }

    #[test]
    fn test_bearing_labels() {
        // norte = -Z
        assert_eq!(bearing_label(0.0, -10.0), "N");
        assert_eq!(bearing_label(10.0, 0.0), "E");
        assert_eq!(bearing_label(0.0, 10.0), "S");
        assert_eq!(bearing_label(-10.0, 0.0), "O");
        assert_eq!(bearing_label(10.0, -10.0), "NE");
    }

    #[test]
    fn test_landmark_lookup_and_radius() {
        let l = landmark_by_name("desert-arch").expect("marco");
        assert_eq!(l.label, "Arco do Deserto");
        assert_eq!(l.biome.mark_radius(), 12.0);
        assert!(landmark_by_name("not-a-landmark").is_none());
    }
}
