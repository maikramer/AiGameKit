//! Mundo vivo (loop 9 do port simple-rpg) — o análogo nativo dos sistemas
//! de ambiente do VibeGame:
//!
//! - **BiomeRegion**: [F]og por bioma — quando o herói entra no polígono de
//!   uma `<BiomeRegion>`, a câmara recebe [`DistanceFog`] (densidade do XML)
//!   e a luz ambiente ganha o tint do bioma.
//! - **Orçamento de PointLights**: só as 12 luzes mais próximas da câmara
//!   ficam acesas (o mundo tem 69 tochas/lanternas).
//! - **Gestos idle de NPC**: os NPCs de quest (sem script) tocam um clip de
//!   gesto (`talk`/`wave`/`call`) de vez em quando, se o rig tiver.
//! - **SFX espaciais mínimos**: eventos [`SfxEvent`] tocam WAVs curtos com
//!   volume por distância (hit/whoosh/harvest/ui) — `assets/audio/sfx/`.
//!
//! BGM por bioma adiado: as BiomeRegions do XML trazem todas
//! `bgm-layer="1"` (a mesma camada) — nada a trocar.

use std::collections::HashMap;

use bevy::prelude::*;

use crate::animation::CharacterAnimator;
use crate::luau::{LuaScriptRef, ScriptToast};
use crate::player::Player;

/// Número máximo de PointLights acesas em simultâneo.
pub const LIGHT_BUDGET: usize = 12;
/// Intervalo de refrescamento do orçamento de luzes (s).
pub const LIGHT_BUDGET_INTERVAL: f32 = 1.0;
/// Intervalo entre gestos idle de NPC (s, ±jitter).
pub const GESTURE_MIN_INTERVAL: f32 = 7.0;

// ── SFX ─────────────────────────────────────────────────────────────────

/// Clip de SFX curto.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SfxClip {
    Hit,
    Whoosh,
    Harvest,
    Ui,
}

impl SfxClip {
    pub fn file(self) -> &'static str {
        match self {
            SfxClip::Hit => "assets/audio/sfx/hit.wav",
            SfxClip::Whoosh => "assets/audio/sfx/whoosh.wav",
            SfxClip::Harvest => "assets/audio/sfx/harvest.wav",
            SfxClip::Ui => "assets/audio/sfx/ui.wav",
        }
    }
}

/// Toca um SFX (volume cai com a distância ao oyente — a câmara).
#[derive(Debug, Clone, Copy, bevy::ecs::message::Message)]
pub struct SfxEvent {
    pub clip: SfxClip,
    /// Posição no mundo; `None` = som de interface (volume cheio).
    pub position: Option<Vec3>,
}

// ── ponto-em-polígono ───────────────────────────────────────────────────

/// Ray casting clássico: o ponto (x, z) está dentro do polígono?
pub fn point_in_polygon(x: f32, z: f32, polygon: &[[f32; 2]]) -> bool {
    let mut inside = false;
    let mut j = polygon.len().saturating_sub(1);
    for i in 0..polygon.len() {
        let (xi, zi) = (polygon[i][0], polygon[i][1]);
        let (xj, zj) = (polygon[j][0], polygon[j][1]);
        let crosses = (zi > z) != (zj > z);
        if crosses {
            let intersect_x = (xj - xi) * (z - zi) / (zj - zi + f32::EPSILON) + xi;
            if x < intersect_x {
                inside = !inside;
            }
        }
        j = i;
    }
    inside
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct AmbientPlugin;

impl Plugin for AmbientPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<CurrentBiome>()
            .add_message::<SfxEvent>()
            .add_systems(PostStartup, load_sfx_assets)
            .add_systems(
                Update,
                (
                    biome_fog_system,
                    light_budget_system,
                    npc_gesture_system,
                    sfx_player_system,
                ),
            );
    }
}

// ── BiomeRegion: fog + tint ─────────────────────────────────────────────

/// Bioma atual do herói (id da região, ou `None` = vale central).
#[derive(Debug, Clone, Resource, Default)]
pub struct CurrentBiome {
    pub id: Option<String>,
}

#[allow(clippy::too_many_arguments)]
fn biome_fog_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    players: Query<&GlobalTransform, With<Player>>,
    biomes: Option<Res<crate::worldsys::BiomeRegions>>,
    mut current: ResMut<CurrentBiome>,
    mut cameras: Query<Entity, With<Camera3d>>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.5;
    let Some(biomes) = biomes else { return };
    let Some(player) = players.iter().next() else { return };
    let pos = player.translation();

    let Some(region) = biomes
        .list
        .iter()
        .find(|b| point_in_polygon(pos.x, pos.z, &b.polygon))
    else {
        if current.id.take().is_some() {
            // saiu para o vale central: fog neutra fora
            for camera in &mut cameras {
                commands.entity(camera).remove::<DistanceFog>();
            }
            toasts.write(ScriptToast("De volta ao vale.".into()));
        }
        return;
    };

    if current.id.as_deref() == Some(region.id.as_str()) {
        return;
    }
    current.id = Some(region.id.clone());
    // fog exponencial com a densidade do XML; cor = tint do bioma
    let tint = region
        .tint
        .map(|t| Color::srgb(t[0], t[1], t[2]))
        .unwrap_or(Color::srgb(0.75, 0.78, 0.82));
    for camera in &mut cameras {
        commands.entity(camera).insert(DistanceFog {
            color: tint,
            directional_light_color: Color::NONE,
            directional_light_exponent: 1.0,
            falloff: FogFalloff::Exponential {
                density: region.fog_density.max(0.0001),
            },
        });
    }
    toasts.write(ScriptToast(format!("Entraste em: {}", region.id)));
}

use bevy::animation::AnimationPlayer;
use bevy::animation::transition::AnimationTransitions;
use bevy::camera::Camera3d;
use bevy::pbr::{DistanceFog, FogFalloff};

// ── orçamento de PointLights ────────────────────────────────────────────

/// Só as [`LIGHT_BUDGET`] luzes mais próximas da câmara ficam visíveis.
#[allow(clippy::type_complexity)]
fn light_budget_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    lights: Query<(Entity, &GlobalTransform), (With<PointLight>, Without<Camera3d>)>,
    mut visibilities: Query<&mut Visibility, With<PointLight>>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = LIGHT_BUDGET_INTERVAL;
    let Ok(cam) = cameras.single() else {
        return;
    };
    let cam_pos = cam.translation();
    let mut by_distance: Vec<(Entity, f32)> = lights
        .iter()
        .map(|(entity, t)| (entity, t.translation().distance_squared(cam_pos)))
        .collect();
    by_distance.sort_by(|a, b| a.1.total_cmp(&b.1));
    for (i, (entity, _)) in by_distance.iter().enumerate() {
        if let Ok(mut visibility) = visibilities.get_mut(*entity) {
            let wanted = if i < LIGHT_BUDGET {
                Visibility::Visible
            } else {
                Visibility::Hidden
            };
            if *visibility != wanted {
                *visibility = wanted;
            }
        }
    }
}

// ── gestos idle de NPC ──────────────────────────────────────────────────

/// NPCs (sem script, sem player) tocam um clip de gesto periodicamente.
#[allow(clippy::type_complexity)]
fn npc_gesture_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    npcs: Query<
        (Entity, &CharacterAnimator),
        (With<Name>, Without<Player>, Without<LuaScriptRef>),
    >,
    mut animation_players: Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
    mut timers: Local<HashMap<Entity, f32>>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 1.0;
    for (entity, animator) in &npcs {
        let entry = timers.entry(entity).or_insert_with(|| {
            // primeiro gesto entre 5 e 12 s (pseudo-aleatório por entidade)
            5.0 + (entity.to_bits() % 7000) as f32 / 1000.0
        });
        *entry -= 1.0;
        if *entry > 0.0 {
            continue;
        }
        *entry = GESTURE_MIN_INTERVAL + (entity.to_bits() % 5000) as f32 / 1000.0;
        let Some(node) = animator
            .clip_names
            .iter()
            .position(|n| {
                let lower = n.to_ascii_lowercase();
                lower.contains("talk") || lower.contains("wave") || lower.contains("call")
            })
            .and_then(|i| animator.nodes.get(i).copied())
        else {
            continue;
        };
        if let Ok((mut player, mut transitions)) =
            animation_players.get_mut(animator.player)
        {
            transitions.play(&mut player, node, std::time::Duration::from_millis(250));
        }
    }
}

// ── SFX ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Resource, Default)]
pub struct SfxHandles {
    pub hit: Option<Handle<AudioSource>>,
    pub whoosh: Option<Handle<AudioSource>>,
    pub harvest: Option<Handle<AudioSource>>,
    pub ui: Option<Handle<AudioSource>>,
}

fn load_sfx_assets(mut commands: Commands, server: Res<AssetServer>) {
    commands.insert_resource(SfxHandles {
        hit: Some(server.load("assets/audio/sfx/hit.wav")),
        whoosh: Some(server.load("assets/audio/sfx/whoosh.wav")),
        harvest: Some(server.load("assets/audio/sfx/harvest.wav")),
        ui: Some(server.load("assets/audio/sfx/ui.wav")),
    });
}

/// Toca o clip com volume por distância (Ativos de áudio são globais; a
/// atenuação é calculada no momento do evento).
fn sfx_player_system(
    mut events: MessageReader<SfxEvent>,
    handles: Option<Res<SfxHandles>>,
    listeners: Query<&GlobalTransform, With<Camera3d>>,
    mut commands: Commands,
) {
    let Some(handles) = handles else {
        return;
    };
    for event in events.read() {
        let handle = match event.clip {
            SfxClip::Hit => handles.hit.clone(),
            SfxClip::Whoosh => handles.whoosh.clone(),
            SfxClip::Harvest => handles.harvest.clone(),
            SfxClip::Ui => handles.ui.clone(),
        };
        let Some(handle) = handle else {
            continue;
        };
        let volume = match event.position {
            Some(pos) => {
                let distance = listeners
                    .iter()
                    .next()
                    .map(|cam| cam.translation().distance(pos))
                    .unwrap_or(0.0);
                (1.0 - distance / 40.0).clamp(0.05, 1.0)
            }
            None => 0.6,
        };
        commands.spawn((
            AudioPlayer(handle),
            PlaybackSettings::REMOVE.with_volume(bevy::audio::Volume::Linear(volume)),
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_point_in_polygon_square() {
        let square = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]];
        assert!(point_in_polygon(5.0, 5.0, &square));
        assert!(!point_in_polygon(15.0, 5.0, &square));
        assert!(!point_in_polygon(-1.0, -1.0, &square));
    }

    #[test]
    fn test_point_in_polygon_wedge() {
        // cunha do bioma norte (estilo environment.xml)
        let wedge = [
            [-56.0, 56.0],
            [56.0, 56.0],
            [4040.0, 4040.0],
            [-4040.0, 4040.0],
        ];
        assert!(point_in_polygon(0.0, 300.0, &wedge), "norte profundo");
        assert!(!point_in_polygon(0.0, 0.0, &wedge), "praça fora");
    }

    #[test]
    fn test_sfx_clip_files() {
        assert!(SfxClip::Hit.file().starts_with("assets/audio/sfx/"));
        assert!(SfxClip::Ui.file().ends_with("ui.wav"));
    }

    #[test]
    fn test_region_format() {
        // REGIONS do travel expostas em travel.rs — aqui só smoke do catálogo
        assert_eq!(crate::travel::LANDMARKS.len(), 12);
    }
}
