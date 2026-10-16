//! Sliding compass strip: direction letters + tick marks track the camera
//! heading, and each 45° sector shows the distance to the nearest NPC.

use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::OrbitCamera;

/// One direction letter in the sliding compass strip.
#[derive(Component)]
pub struct CompassLetter {
    /// World bearing this letter sits at (0° = north/−Z, 90° = east/+X).
    pub bearing_deg: f32,
}

/// A compass tick mark (slides with the letters).
#[derive(Component)]
pub struct CompassTick {
    pub bearing_deg: f32,
}

/// Distance readout under a compass letter: nearest NPC in that sector.
#[derive(Component)]
pub struct CompassDistance {
    pub bearing_deg: f32,
}

/// Camera heading as a compass bearing in degrees (0° = north/−Z, 90° = east).
pub fn heading_bearing_deg(yaw_deg: f32) -> f32 {
    (-yaw_deg).rem_euclid(360.0)
}

/// World bearing of an offset `(dx, dz)` from the player (0° = north/−Z).
pub fn world_bearing_deg(dx: f32, dz: f32) -> f32 {
    dx.atan2(-dz).to_degrees().rem_euclid(360.0)
}

/// Nearest NPC distance within ±`sector_deg` of `bearing`, if any.
/// `bearings` holds `(world_bearing_deg, distance)` per NPC.
pub fn sector_distance(bearings: &[(f32, f32)], bearing: f32, sector_deg: f32) -> Option<f32> {
    bearings
        .iter()
        .filter(|(b, _)| {
            let d = (b - bearing).rem_euclid(360.0);
            d.min(360.0 - d) <= sector_deg
        })
        .map(|(_, dist)| *dist)
        .fold(None, |acc: Option<f32>, dist| {
            Some(acc.map_or(dist, |acc| acc.min(dist)))
        })
}

/// Horizontal offset (px from the strip center) for a compass letter whose
/// bearing differs from the heading by `delta_deg`. `None` when out of the
/// visible span.
pub fn compass_offset_px(delta_deg: f32, half_width: f32, span_deg: f32) -> Option<f32> {
    if delta_deg.abs() >= span_deg {
        None
    } else {
        Some(delta_deg / span_deg * half_width)
    }
}

/// Animate the compass strip: letters + ticks slide with the heading, letters
/// outside the span hide, and each direction shows the distance to the
/// nearest NPC in its sector.
#[allow(clippy::type_complexity)]
pub fn hud_compass_update(
    cameras: Query<&OrbitCamera>,
    players: Query<&GlobalTransform, With<Player>>,
    guide: Option<Res<crate::quest_guide::QuestGuide>>,
    mut letters: Query<(&mut Node, &mut Visibility, &CompassLetter), Without<CompassTick>>,
    mut ticks: Query<(&mut Node, &CompassTick), (Without<CompassLetter>, Without<CompassDistance>)>,
    mut distances: Query<
        (&mut Node, &mut Visibility, &CompassDistance, &mut Text),
        (Without<CompassLetter>, Without<CompassTick>),
    >,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let heading = heading_bearing_deg(cam.yaw_deg);
    let bearings: Vec<(f32, f32)> = players
        .iter()
        .next()
        .map(|player| {
            let origin = player.translation();
            guide
                .as_deref()
                .map_or(&[][..], |g| g.beacons.as_slice())
                .iter()
                .map(|p| {
                    (
                        world_bearing_deg(p.x - origin.x, p.z - origin.z),
                        p.distance(origin),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    for (mut node, mut visibility, letter) in &mut letters {
        let delta = crate::camera::shortest_angle_delta_deg(heading, letter.bearing_deg);
        // Escrita GUARDADA também na `Visibility`: escrever o mesmo valor
        // marca `Changed` e punha o Taffy a refazer o layout da faixa do
        // compasso em todos os frames.
        let wanted = match compass_offset_px(delta, 230.0, 55.0) {
            Some(offset) => {
                if node.left != Val::Px(230.0 + offset) {
                    node.left = Val::Px(230.0 + offset);
                }
                Visibility::Inherited
            }
            None => Visibility::Hidden,
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
    for (mut node, tick) in &mut ticks {
        let delta = crate::camera::shortest_angle_delta_deg(heading, tick.bearing_deg);
        let wanted = match compass_offset_px(delta, 230.0, 55.0) {
            Some(offset) => Val::Px(230.0 + offset),
            None => Val::Px(-100.0), // parked off-strip
        };
        if node.left != wanted {
            node.left = wanted;
        }
    }
    for (mut node, mut visibility, dist, mut text) in &mut distances {
        let delta = crate::camera::shortest_angle_delta_deg(heading, dist.bearing_deg);
        let wanted = match compass_offset_px(delta, 230.0, 55.0) {
            Some(offset) => Val::Px(230.0 + offset),
            None => Val::Px(-100.0),
        };
        if node.left != wanted {
            node.left = wanted;
        }
        let sector = sector_distance(&bearings, dist.bearing_deg, 22.5);
        let wanted_visibility = if sector.is_some() {
            Visibility::Inherited
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted_visibility {
            *visibility = wanted_visibility;
        }
        let next = sector.map_or_else(|| " ".to_string(), |d| format!("{}m", d.round() as i32));
        if text.0 != next {
            text.0 = next;
        }
    }
}

/// Marca de marcodisponível na faixa do compasso: losango teal que desliza
/// como as letras — a promessa "há algo para ver nessa direcção". Atalaias
/// e marcos comuns partilham o pool; o desenho distingue-os.
#[derive(Component)]
pub struct CompassLandmark {
    /// Slot fixo (ordem de desenho); o rumo é reatribuído por frame.
    pub slot: usize,
}

/// Quantas marcas de marcos a faixa mostra.
pub const COMPASS_LANDMARK_SLOTS: usize = 4;

/// Ângulo (graus, ±180) do rumo `bearing` face ao `heading`.
fn bearing_delta(heading: f32, bearing: f32) -> f32 {
    let delta = (bearing - heading).rem_euclid(360.0);
    if delta > 180.0 { delta - 360.0 } else { delta }
}

/// Marcas de marcos disponíveis no compasso: os 4 rumos MAIS alinhados com
/// o olhar (atalaias primeiro em empate) ganham slot; fora do span escondem.
/// Lê o snapshot `travel::LandmarkBlips` — nunca varre o mundo.
#[allow(clippy::type_complexity)]
pub fn hud_compass_landmarks_update(
    cameras: Query<&OrbitCamera>,
    players: Query<&GlobalTransform, With<Player>>,
    blips: Option<Res<crate::travel::LandmarkBlips>>,
    mut markers: Query<(&mut Node, &mut Visibility, &CompassLandmark)>,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let heading = heading_bearing_deg(cam.yaw_deg);
    let Some(player) = players.iter().next() else {
        return;
    };
    let origin = player.translation();
    let mut ranked: Vec<(f32, f32, bool)> = blips
        .as_deref()
        .map(|b| b.0.as_slice())
        .unwrap_or(&[])
        .iter()
        .map(|b| {
            let (dx, dz) = (b.position.x - origin.x, b.position.z - origin.z);
            (
                bearing_delta(heading, world_bearing_deg(dx, dz)).abs(),
                origin.distance(b.position),
                b.tower,
            )
        })
        .filter(|(delta, _, _)| *delta < 55.0)
        .collect();
    // Mais alinhado com o olhar primeiro; atalaias ganham empates (é a
    // estrutura da exploração — a torre manda sobre o mojão atrás dela).
    ranked.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then_with(|| b.2.cmp(&a.2))
            .then_with(|| a.1.total_cmp(&b.1))
    });
    for (mut node, mut visibility, marker) in &mut markers {
        match ranked.get(marker.slot) {
            Some((delta, _, _)) => {
                let wanted = compass_offset_px(*delta, 230.0, 55.0);
                if let Some(offset) = wanted {
                    if node.left != Val::Px(230.0 + offset) {
                        node.left = Val::Px(230.0 + offset);
                    }
                    if *visibility != Visibility::Inherited {
                        *visibility = Visibility::Inherited;
                    }
                } else if *visibility != Visibility::Hidden {
                    *visibility = Visibility::Hidden;
                }
            }
            None => {
                if *visibility != Visibility::Hidden {
                    *visibility = Visibility::Hidden;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_heading_bearing_matches_compass() {
        // yaw 0 → camera looks −Z (north) → bearing 0; yaw 90 swings the
        // camera to the +X side so it faces −X (west) → bearing 270.
        assert!(approx(heading_bearing_deg(0.0), 0.0));
        assert!(approx(heading_bearing_deg(90.0), 270.0));
        assert!(approx(heading_bearing_deg(-90.0), 90.0));
        // yaw wraps: −450° ≡ −90° → facing east.
        assert!(approx(heading_bearing_deg(-450.0), 90.0));
    }

    #[test]
    fn test_world_bearing_matches_compass_rose() {
        // North (−Z) = 0°, east (+X) = 90°, south (+Z) = 180°, west = 270°.
        assert!(approx(world_bearing_deg(0.0, -1.0), 0.0));
        assert!(approx(world_bearing_deg(1.0, 0.0), 90.0));
        assert!(approx(world_bearing_deg(0.0, 1.0), 180.0));
        assert!(approx(world_bearing_deg(-1.0, 0.0), 270.0));
    }

    #[test]
    fn test_sector_distance_picks_nearest_in_sector() {
        let bearings = [(0.0, 41.0), (30.0, 10.0), (180.0, 7.0)];
        // Within ±22.5° of north: only the 41 m NPC.
        assert_eq!(sector_distance(&bearings, 0.0, 22.5), Some(41.0));
        // NE sector (45°) catches the 10 m NPC at bearing 30.
        assert_eq!(sector_distance(&bearings, 45.0, 22.5), Some(10.0));
        // Empty sector → None.
        assert_eq!(sector_distance(&bearings, 270.0, 22.5), None);
    }

    #[test]
    fn test_compass_offset_visibility_window() {
        // Centered letter sits at zero offset; ±span hides.
        assert_eq!(compass_offset_px(0.0, 230.0, 55.0), Some(0.0));
        let half = compass_offset_px(27.5, 230.0, 55.0).unwrap();
        assert!(approx(half, 115.0));
        assert_eq!(compass_offset_px(55.0, 230.0, 55.0), None);
        assert_eq!(compass_offset_px(-80.0, 230.0, 55.0), None);
    }

    #[test]
    fn test_bearing_delta_wraps() {
        // 350° face a 0° são 10° à esquerda, não 350.
        assert!(approx(bearing_delta(0.0, 350.0), -10.0));
        assert!(approx(bearing_delta(350.0, 10.0), 20.0));
        assert!(approx(bearing_delta(0.0, 180.0), 180.0));
        assert!(approx(bearing_delta(45.0, 45.0), 0.0));
    }
}
