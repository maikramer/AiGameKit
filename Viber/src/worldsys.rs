//! World-system elements: DayCycle, Weather, WorldBorder, BiomeRegion and
//! engine config — parsed into resources and driven by systems here.

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
    pub drive_ambient: bool,
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
    ) -> Self {
        Self {
            minute_of_day,
            minutes_per_real_second,
            dawn_minute,
            dusk_minute,
            ambient_day,
            ambient_night,
            drive_ambient,
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
    mut targets: Query<&mut Transform, With<SeatOnTerrain>>,
) {
    if *done {
        return;
    }
    let Some(runtime) = runtime else {
        return;
    };
    for mut transform in &mut targets {
        let ground = runtime.sample(transform.translation.x, transform.translation.z);
        if transform.translation.y < ground - 0.25 {
            transform.translation.y = ground;
        }
    }
    *done = true;
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
    let day = daylight_factor(clock.minute_of_day, clock.dawn_minute, clock.dusk_minute);
    ambient.brightness = day * clock.ambient_day + (1.0 - day) * clock.ambient_night;
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
}
