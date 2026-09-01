//! World-system elements: DayCycle, Weather, WorldBorder, BiomeRegion and
//! engine config — parsed into resources and driven by systems here.

use bevy::math::Quat;
use bevy::math::Vec3;
use bevy::prelude::*;

/// `<DayCycle>` clock: advances minute-of-day and ramps the ambient light.
#[derive(Debug, Clone, Resource)]
pub struct DayCycleState {
    pub minute_of_day: f32,
    pub minutes_per_real_second: f32,
    pub dawn_minute: f32,
    pub dusk_minute: f32,
    pub ambient_day: f32,
    pub ambient_night: f32,
    /// Ambient brightness the world authored (`<AmbientLight brightness>`),
    /// used as the full-day anchor for the day/night ramp. `0` until the
    /// first drive tick captures it.
    pub ambient_reference: f32,
    pub drive_ambient: bool,
    /// Elevação máxima do sol ao meio-dia (graus).
    pub max_sun_elevation: f32,
    /// Azimute do nascer do sol (graus).
    pub sun_azimuth_base: f32,
}

impl DayCycleState {
    #[allow(clippy::too_many_arguments)]
    pub fn from_parts(
        minute_of_day: f32,
        minutes_per_real_second: f32,
        dawn_minute: f32,
        dusk_minute: f32,
        ambient_day: f32,
        ambient_night: f32,
        drive_ambient: bool,
        max_sun_elevation: f32,
        sun_azimuth_base: f32,
    ) -> Self {
        Self {
            minute_of_day,
            minutes_per_real_second,
            dawn_minute,
            dusk_minute,
            ambient_day,
            ambient_night,
            // Captured from the live ambient light on the first drive tick.
            ambient_reference: 0.0,
            drive_ambient,
            max_sun_elevation,
            sun_azimuth_base,
        }
    }
}

/// Daylight factor `0.0` (night) → `1.0` (day) with 60-minute dawn/dusk ramps.
pub fn daylight_factor(minute: f32, dawn: f32, dusk: f32) -> f32 {
    const RAMP: f32 = 60.0;
    if minute < dawn - RAMP {
        0.0
    } else if minute < dawn {
        (minute - (dawn - RAMP)) / RAMP
    } else if minute < dusk {
        1.0
    } else if minute < dusk + RAMP {
        1.0 - (minute - dusk) / RAMP
    } else {
        0.0
    }
}

/// `<Weather>` wind/cloud/rain config.
#[derive(Debug, Clone, Resource)]
pub struct WeatherState {
    pub wind: [f32; 2],
    pub wind_strength: f32,
    pub clouds: f32,
    pub rain: f32,
    pub cycle: bool,
}

/// `<BiomeRegion>` polygon + fog/tint data (fog rendering follow-up).
#[derive(Debug, Clone, Resource)]
pub struct BiomeRegionData {
    pub id: String,
    pub polygon: Vec<[f32; 2]>,
    pub fog_density: f32,
    pub tint: Option<[f32; 3]>,
}

/// Todas as `<BiomeRegion>` do mundo (loop 9: fog/tint por bioma).
#[derive(Debug, Clone, Resource, Default)]
pub struct BiomeRegions {
    pub list: Vec<BiomeRegionData>,
}

/// `<WorldBorder>` config.
#[derive(Debug, Clone, Resource)]
pub struct WorldBorderConfig {
    pub radius: f32,
    pub warn_seconds: f32,
    pub margin: f32,
}

/// Generic engine config element kept as raw data.
#[derive(Debug, Clone, Resource)]
pub struct EngineConfigData {
    pub tag: String,
    pub attrs: Vec<(String, String)>,
}

/// Deferred world-system requests collected while spawning entities.
#[derive(Debug, Resource, Default)]
pub struct PendingWorldSystems {
    pub day_cycle: Option<DayCycleState>,
    pub weather: Option<WeatherState>,
    pub border: Option<WorldBorderConfig>,
    pub biomes: Vec<BiomeRegionData>,
    pub configs: Vec<EngineConfigData>,
}

impl PendingWorldSystems {
    /// Take all deferred requests out (leaving the accumulator empty).
    pub fn consume(&mut self) -> PendingWorldSystems {
        std::mem::take(self)
    }
}

/// Marks entities authored at y≈0 that must sit on the terrain surface once
/// the carved world exists (the original engine seated statics via CCT).
#[derive(Debug, Component)]
pub struct SeatOnTerrain;

/// One-shot: lift seated entities that ended up below the terrain surface.
/// Runs once the terrain runtime exists; later spawns (spawn groups) are
/// already placed by [`crate::spawner::compute_placements`].
pub fn seat_statics_once(
    mut done: Local<bool>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut targets: Query<(Entity, &mut Transform), With<SeatOnTerrain>>,
    parents: Query<&ChildOf>,
    seated: Query<(), With<SeatOnTerrain>>,
) {
    if *done {
        return;
    }
    let Some(runtime) = runtime else {
        return;
    };
    for (entity, mut transform) in &mut targets {
        // Only the OUTERMOST seatable group is seated.
        //
        // `SeatOnTerrain` is placed on every `<Group>`, and the write is a
        // *local* Y. Seating a nested group therefore added the ground height
        // a second time on top of an ancestor that had already been raised to
        // it — `simple-rpg` nests the city two deep, so its plaza ended up at
        // 2x the terrain height and every prop in it at 3x, leaving the whole
        // village floating ~49 m above the ground the hero stands on.
        //
        // Seating just the root also makes the local translation the world
        // translation, so the ground is sampled at the right XZ without
        // depending on transform propagation having run this frame.
        if has_seated_ancestor(entity, &parents, &seated) {
            continue;
        }
        let ground = runtime.sample(transform.translation.x, transform.translation.z);
        if transform.translation.y < ground - 0.25 {
            transform.translation.y = ground;
        }
    }
    *done = true;
}

/// True when any ancestor of `entity` also carries [`SeatOnTerrain`].
fn has_seated_ancestor(
    entity: Entity,
    parents: &Query<&ChildOf>,
    seated: &Query<(), With<SeatOnTerrain>>,
) -> bool {
    let mut current = entity;
    // Depth guard: worlds compose with `<Include>` and could nest deeply, but
    // a cycle would hang the startup frame.
    for _ in 0..64 {
        let Ok(parent) = parents.get(current) else {
            return false;
        };
        if seated.get(parent.parent()).is_ok() {
            return true;
        }
        current = parent.parent();
    }
    false
}

/// Posição do sol (e estado dia/noite) calculada a partir do relógio.
#[derive(Debug, Clone, Copy, Resource, Default)]
pub struct SunState {
    /// Direção PARA o sol, normalizada (mundo, Y-up).
    pub dir: Vec3,
    pub elevation_deg: f32,
    /// 0 = dia pleno, 1 = noite plena.
    pub night: f32,
}

/// Elevação (graus) do sol para o minuto do dia: sobe `max_elevation` ao
/// meio-dia e desce abaixo do horizonte à noite.
pub fn sun_elevation(minute: f32, dawn: f32, dusk: f32, max_elevation: f32) -> f32 {
    const NIGHT_HALF_ARC: f32 = 25.0;
    if minute >= dawn && minute < dusk {
        let t = (minute - dawn) / (dusk - dawn);
        (std::f32::consts::PI * t).sin() * max_elevation
    } else {
        let dusk_len = 24.0 * 60.0 - dusk + dawn;
        let t = if minute >= dusk {
            (minute - dusk) / dusk_len
        } else {
            (minute + (24.0 * 60.0 - dusk)) / dusk_len
        };
        -(std::f32::consts::PI * t).sin().abs() * NIGHT_HALF_ARC
    }
}

/// Azimute (graus) do sol: avança 180° durante o dia, 180° durante a noite.
pub fn sun_azimuth(minute: f32, dawn: f32, dusk: f32, base: f32) -> f32 {
    if minute >= dawn && minute < dusk {
        base + 180.0 * (minute - dawn) / (dusk - dawn)
    } else {
        let dusk_len = 24.0 * 60.0 - dusk + dawn;
        let t = if minute >= dusk {
            (minute - dusk) / dusk_len
        } else {
            (minute + (24.0 * 60.0 - dusk)) / dusk_len
        };
        base + 180.0 + 180.0 * t
    }
}

/// Advance the day clock and ramp `GlobalAmbientLight.brightness`.
#[allow(clippy::needless_pass_by_value)]
pub fn daycycle_drive(
    time: Res<Time>,
    clock: Option<ResMut<DayCycleState>>,
    ambient: Option<ResMut<GlobalAmbientLight>>,
) {
    let (Some(mut clock), Some(mut ambient)) = (clock, ambient) else {
        return;
    };
    clock.minute_of_day = (clock.minute_of_day
        + clock.minutes_per_real_second * time.delta_secs() / 60.0)
        % (24.0 * 60.0);
    if !clock.drive_ambient {
        return;
    }
    // Capture the world's own ambient before this system starts writing it.
    if clock.ambient_reference <= 0.0 {
        clock.ambient_reference = ambient.brightness.max(1.0);
    }
    let day = daylight_factor(clock.minute_of_day, clock.dawn_minute, clock.dusk_minute);
    // `ambient-day-intensity` / `ambient-night-intensity` are VibeGame's
    // three.js ambient *intensities* — a 0..1 scale. Bevy's
    // `AmbientLight::brightness` is in lux and the same worlds author it in the
    // hundreds (`simple-rpg`: `brightness="110"`). Writing 0.26 straight into
    // it dropped the ambient by ~400x and left the whole village in the dark.
    //
    // So the pair is used as a day/night *ratio* against the brightness the
    // world authored: full day keeps that value, night falls to
    // `night / day` of it.
    let scale = if clock.ambient_day > f32::EPSILON {
        (day * clock.ambient_day + (1.0 - day) * clock.ambient_night) / clock.ambient_day
    } else {
        day
    };
    ambient.brightness = clock.ambient_reference * scale.clamp(0.0, 1.0);
}

/// Publish [`SunState`] from the day clock and aim the directional light.
///
/// NOTE (Claude): `main.rs` already scheduled this system but the body had not
/// been written yet, so the binary did not build. This is the straightforward
/// composition of the `sun_elevation` / `sun_azimuth` helpers that were already
/// here — replace it if the intended behaviour differs.
#[allow(clippy::needless_pass_by_value)]
pub fn sun_drive(
    clock: Option<Res<DayCycleState>>,
    mut sun: ResMut<SunState>,
    mut lights: Query<&mut Transform, With<DirectionalLight>>,
) {
    let Some(clock) = clock else {
        return;
    };
    let elevation_deg = sun_elevation(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
        clock.max_sun_elevation,
    );
    let azimuth_deg = sun_azimuth(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
        clock.sun_azimuth_base,
    );
    let (el, az) = (elevation_deg.to_radians(), azimuth_deg.to_radians());
    // Direction *towards* the sun (Y-up).
    let dir = Vec3::new(el.cos() * az.sin(), el.sin(), el.cos() * az.cos()).normalize_or_zero();
    sun.dir = dir;
    sun.elevation_deg = elevation_deg;
    // Night ramps in as the sun drops below the horizon.
    sun.night = (1.0 - (elevation_deg / 6.0).clamp(0.0, 1.0)).clamp(0.0, 1.0);

    // Sunlight travels from the sun into the scene; bevy shines a directional
    // light along the entity's -Z (same convention as `recipes::spawn`).
    for mut transform in &mut lights {
        transform.rotation = Quat::from_rotation_arc(-Vec3::Z, -dir);
    }
}

/// Keep the player inside the world disc (radius − margin).
#[allow(clippy::needless_pass_by_value)]
pub fn world_border_clamp(
    border: Option<Res<WorldBorderConfig>>,
    mut players: Query<&mut Transform, With<crate::player::Player>>,
) {
    let Some(border) = border else {
        return;
    };
    let limit = border.radius - border.margin;
    for mut transform in &mut players {
        let pos = transform.translation;
        let dist_sq = pos.x * pos.x + pos.z * pos.z;
        if dist_sq > limit * limit {
            let scale = limit / dist_sq.sqrt();
            transform.translation.x = pos.x * scale;
            transform.translation.z = pos.z * scale;
            bevy::log::info!("world border: player returned inside r={limit}");
        }
    }
}

/// Point-in-polygon test on XZ (ray cast) for biome regions.
pub fn point_in_biome(polygon: &[[f32; 2]], x: f32, z: f32) -> bool {
    let mut inside = false;
    let n = polygon.len();
    if n < 3 {
        return false;
    }
    let mut j = n - 1;
    for i in 0..n {
        let (xi, zi) = (polygon[i][0], polygon[i][1]);
        let (xj, zj) = (polygon[j][0], polygon[j][1]);
        if ((zi > z) != (zj > z)) && (x < (xj - xi) * (z - zi) / (zj - zi) + xi) {
            inside = !inside;
        }
        j = i;
    }
    inside
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_daylight_factor_ramps() {
        assert_eq!(daylight_factor(100.0, 330.0, 1170.0), 0.0); // night
        assert_eq!(daylight_factor(600.0, 330.0, 1170.0), 1.0); // mid-day
        let mid = daylight_factor(300.0, 330.0, 1170.0); // dawn ramp
        assert!((0.0..1.0).contains(&mid));
        assert_eq!(daylight_factor(1231.0, 330.0, 1170.0), 0.0); // after dusk ramp
    }

    #[test]
    fn test_point_in_biome() {
        let square = vec![[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]];
        assert!(point_in_biome(&square, 0.0, 0.0));
        assert!(!point_in_biome(&square, 50.0, 50.0));
    }

    /// A `<Group>` nested inside another must not be seated twice.
    ///
    /// Regression: `SeatOnTerrain` is on every group and writes a *local* Y,
    /// so seating a child on top of an already-seated parent added the ground
    /// height again. `simple-rpg` nests its city two deep, which put the plaza
    /// at 2x the terrain height and its props at 3x — the whole village
    /// floated ~49 m over the ground the hero walks on.
    #[test]
    fn test_seat_statics_only_moves_the_outermost_group() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::heightmap::HeightMapU16;
        use crate::terrain::runtime::TerrainRuntime;
        use crate::terrain::spec::TerrainSpec;

        let spec = TerrainSpec {
            world_size: 64.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        // Flat field at half of `max_height` → ground sits at 50 m.
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: vec![u16::MAX / 2; 33 * 33],
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 0.0)
            .expect("grid builds");
        let ground = grid.sample(0.0, 0.0);
        assert!(ground > 40.0, "fixture ground is well above zero: {ground}");

        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(TerrainRuntime {
            spec,
            grid,
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
        });
        app.add_systems(bevy::app::Update, seat_statics_once);

        let outer = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain))
            .id();
        let inner = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain, ChildOf(outer)))
            .id();
        // A prop that is not itself a group still rides the hierarchy.
        let prop = app
            .world_mut()
            .spawn((Transform::from_xyz(1.0, 0.0, 1.0), ChildOf(inner)))
            .id();

        app.update();

        let local_y =
            |app: &bevy::app::App, e| app.world().get::<Transform>(e).unwrap().translation.y;
        assert!(
            (local_y(&app, outer) - ground).abs() < 0.5,
            "the outermost group is seated on the ground"
        );
        assert_eq!(
            local_y(&app, inner),
            0.0,
            "the nested group keeps its authored local Y"
        );
        assert_eq!(local_y(&app, prop), 0.0, "props keep their authored offset");
    }

    /// The day/night intensities are a ratio, not an absolute brightness.
    ///
    /// Regression: `ambient-day-intensity` is a three.js 0..1 intensity while
    /// Bevy's `AmbientLight::brightness` is in lux, and the same worlds author
    /// it in the hundreds. Writing 0.26 straight into it dropped the ambient
    /// by ~400x and left the village in the dark.
    #[test]
    fn test_daycycle_ambient_is_a_ratio_of_the_authored_brightness() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(GlobalAmbientLight {
            brightness: 110.0,
            ..Default::default()
        });
        app.insert_resource(DayCycleState::from_parts(
            600.0, // midday
            0.0,   // clock frozen, so the test is about the ramp only
            330.0, 1170.0, 0.26, 0.07, true, 62.0, 205.0,
        ));
        app.add_systems(bevy::app::Update, daycycle_drive);

        app.update();
        let midday = app.world().resource::<GlobalAmbientLight>().brightness;
        assert!(
            (midday - 110.0).abs() < 1.0,
            "full day keeps the authored brightness, got {midday}"
        );

        // Midnight falls to the night/day ratio of it, not to 0.07 lux.
        app.world_mut()
            .resource_mut::<DayCycleState>()
            .minute_of_day = 60.0;
        app.update();
        let night = app.world().resource::<GlobalAmbientLight>().brightness;
        let expected = 110.0 * (0.07 / 0.26);
        assert!(
            (night - expected).abs() < 1.0,
            "night is the authored brightness scaled by night/day, got {night} (expected {expected})"
        );
        assert!(night > 10.0, "night is dim, not black");
    }
}
