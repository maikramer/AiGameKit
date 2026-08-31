//! `<StaticSpawner>` runtime: deterministic instance placement on the terrain.
//!
//! Placement is a pure function of (spec, terrain sampler) driven by a
//! SplitMix64 RNG — the same seed produces the same forest. The live sampler
//! wraps [`TerrainRuntime`]; tests pass closures.

use bevy::asset::LoadState;
use bevy::gltf::Gltf;
use bevy::math::Vec3;
use bevy::prelude::*;
use bevy::world_serialization::WorldAssetRoot;

use crate::recipes::StaticSpawnerSpec;
use crate::terrain::runtime::TerrainRuntime;

/// Small deterministic RNG (SplitMix64) — same seed, same sequence.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed)
    }

    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in `0.0..1.0` (24 bits of mantissa — plenty for placement).
    pub fn next_f32(&mut self) -> f32 {
        (self.next_u64() >> 40) as f32 / (1u64 << 24) as f32
    }

    /// Uniform in `min..max`.
    pub fn range(&mut self, min: f32, max: f32) -> f32 {
        min + (max - min) * self.next_f32()
    }

    /// Uniform point in the unit disc (sqrt keeps the area density flat).
    pub fn unit_disc(&mut self) -> bevy::math::Vec2 {
        let angle = self.range(0.0, std::f32::consts::TAU);
        let radius = self.next_f32().sqrt();
        bevy::math::Vec2::new(angle.cos() * radius, angle.sin() * radius)
    }
}

/// One placed template instance.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedInstance {
    pub position: Vec3,
    pub yaw_deg: f32,
    pub scale: Vec3,
    /// Index into the spawner's `template_urls` (and `handles`).
    pub template_index: usize,
}

/// Terrain query result for one candidate position.
#[derive(Debug, Clone, Copy)]
pub struct TerrainSample {
    pub height: f32,
    /// Surface normal (normalized by the sampler).
    pub normal: Vec3,
    pub water: bool,
}

/// Pick a position for one instance: cluster center + disc offset, clamped to
/// the region rectangle.
fn next_candidate(
    spec: &StaticSpawnerSpec,
    rng: &mut Rng,
    clusters: &[bevy::math::Vec2],
) -> bevy::math::Vec2 {
    let mut pos = if clusters.is_empty() {
        bevy::math::Vec2::new(
            rng.range(spec.region_min[0], spec.region_max[0]),
            rng.range(spec.region_min[2], spec.region_max[2]),
        )
    } else {
        let center = clusters[rng.next_u64() as usize % clusters.len()];
        center + rng.unit_disc() * spec.cluster_radius
    };
    pos.x = pos.x.clamp(
        spec.region_min[0].min(spec.region_max[0]),
        spec.region_min[0].max(spec.region_max[0]),
    );
    pos.y = pos.y.clamp(
        spec.region_min[2].min(spec.region_max[2]),
        spec.region_min[2].max(spec.region_max[2]),
    );
    pos
}

/// Compute every instance of a spawner group deterministically.
///
/// Rejected candidates (water, slope, overlap, max-distance) burn an attempt;
/// the loop stops at `count` placements or `count*8 + 64` attempts, mirroring
/// the original engine's bounded-retry behaviour.
pub fn compute_placements(
    spec: &StaticSpawnerSpec,
    sample: &mut dyn FnMut(f32, f32) -> TerrainSample,
) -> Vec<PlacedInstance> {
    let mut rng = Rng::new(spec.seed ^ 0x5EED_5EED_5EED_5EED);
    let clusters: Vec<bevy::math::Vec2> = if spec.cluster_count > 0 {
        (0..spec.cluster_count)
            .map(|_| {
                bevy::math::Vec2::new(
                    rng.range(spec.region_min[0], spec.region_max[0]),
                    rng.range(spec.region_min[2], spec.region_max[2]),
                )
            })
            .collect()
    } else {
        Vec::new()
    };

    let slope_limit = spec.max_slope_deg.to_radians().cos();
    let mut placed: Vec<bevy::math::Vec2> = Vec::new();
    let mut out = Vec::with_capacity(spec.count as usize);
    let max_attempts = spec.count * 8 + 64;

    for _ in 0..max_attempts {
        if out.len() >= spec.count as usize {
            break;
        }
        let pos = next_candidate(spec, &mut rng, &clusters);
        if spec.max_distance > 0.0 && pos.length() > spec.max_distance {
            continue;
        }
        let terrain = sample(pos.x, pos.y);
        if spec.avoid_water && terrain.water {
            continue;
        }
        let normal = terrain.normal.normalize_or_zero();
        if normal.y < slope_limit {
            continue;
        }
        if spec.avoid_overlaps && spec.footprint_radius > 0.0 {
            let min_dist_sq = spec.footprint_radius * spec.footprint_radius;
            if placed.iter().any(|p| p.distance_squared(pos) < min_dist_sq) {
                continue;
            }
        }
        placed.push(pos);

        let scale_u = rng.range(spec.scale_min, spec.scale_max);
        let axis = Vec3::new(
            rng.range(spec.scale_axis_min, spec.scale_axis_max),
            1.0,
            rng.range(spec.scale_axis_min, spec.scale_axis_max),
        );
        let yaw = if spec.random_yaw {
            rng.range(0.0, 360.0)
        } else {
            0.0
        };
        let template_index = if spec.template_urls.len() > 1 {
            (rng.next_u64() as usize) % spec.template_urls.len()
        } else {
            0
        };
        let y = if spec.align_to_terrain {
            terrain.height
        } else {
            spec.region_min[1]
        };
        out.push(PlacedInstance {
            position: Vec3::new(pos.x, y, pos.y),
            yaw_deg: yaw,
            scale: Vec3::splat(scale_u) * axis,
            template_index,
        });
    }
    out
}

/// One collected `<StaticSpawner>`: spec plus one handle per template url.
pub struct SpawnGroupState {
    pub spec: StaticSpawnerSpec,
    pub handles: Vec<Handle<Gltf>>,
    pub done: bool,
}

/// All spawner groups collected at startup; consumed by
/// [`instantiate_spawn_groups`] once the terrain runtime and the template
/// assets are ready.
#[derive(Resource)]
pub struct PendingSpawnGroups {
    pub groups: Vec<SpawnGroupState>,
}

/// Spawn every instance of every loaded spawner group. Runs each frame until
/// all groups are done, then removes itself.
pub fn instantiate_spawn_groups(
    mut commands: Commands,
    gltfs: Res<Assets<Gltf>>,
    server: Res<AssetServer>,
    runtime: Option<Res<TerrainRuntime>>,
    mut pending: Option<ResMut<PendingSpawnGroups>>,
) {
    let Some(pending) = pending.as_mut() else {
        return;
    };
    let Some(runtime) = runtime else {
        return; // terrain bootstrap has not published the carved world yet
    };
    let mut all_done = true;
    for group in &mut pending.groups {
        if group.done {
            continue;
        }
        let load_states: Vec<Option<LoadState>> = group
            .handles
            .iter()
            .map(|handle| server.get_load_state(handle))
            .collect();
        if load_states
            .iter()
            .any(|state| matches!(state, Some(LoadState::Failed(_))))
        {
            bevy::log::warn!(
                "spawner template failed to load — group skipped ({})",
                group
                    .spec
                    .template_urls
                    .first()
                    .map(String::as_str)
                    .unwrap_or("?")
            );
            group.done = true;
            continue;
        }
        if !load_states
            .iter()
            .all(|state| matches!(state, Some(LoadState::Loaded)))
        {
            all_done = false;
            continue;
        }
        let scenes: Vec<Option<bevy::asset::Handle<bevy::world_serialization::WorldAsset>>> = group
            .handles
            .iter()
            .map(|handle| {
                gltfs
                    .get(handle)
                    .and_then(|gltf| gltf.default_scene.clone())
            })
            .collect();
        if scenes.iter().any(Option::is_none) {
            bevy::log::warn!("spawner template has no default scene — group skipped");
            group.done = true;
            continue;
        }
        let grid = &runtime.grid;
        let mut sample = |x: f32, z: f32| TerrainSample {
            height: runtime.sample(x, z),
            normal: grid.sample_normal(x, z, 0.5),
            water: runtime.in_water(x, z),
        };
        for instance in compute_placements(&group.spec, &mut sample) {
            let mut transform = Transform::from_translation(instance.position);
            transform.rotation = bevy::math::Quat::from_rotation_y(instance.yaw_deg.to_radians());
            transform.scale = instance.scale;
            if let Some(scene) = scenes[instance.template_index.min(scenes.len() - 1)].clone() {
                commands.spawn((transform, Visibility::Inherited, WorldAssetRoot(scene)));
            }
        }
        group.done = true;
    }
    if all_done {
        commands.remove_resource::<PendingSpawnGroups>();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::recipes::StaticSpawnerSpec;

    fn spec() -> StaticSpawnerSpec {
        StaticSpawnerSpec {
            seed: 42,
            count: 10,
            region_min: [-100.0, 0.0, -100.0],
            region_max: [100.0, 0.0, 100.0],
            cluster_count: 0,
            cluster_radius: 0.0,
            footprint_radius: 0.0,
            avoid_overlaps: false,
            max_slope_deg: 90.0,
            avoid_water: false,
            align_to_terrain: true,
            scale_min: 1.0,
            scale_max: 1.0,
            scale_axis_min: 1.0,
            scale_axis_max: 1.0,
            random_yaw: false,
            max_distance: 0.0,
            template_urls: vec!["/assets/meshes/a.glb".into()],
        }
    }

    fn flat(_x: f32, _z: f32) -> TerrainSample {
        TerrainSample {
            height: 3.0,
            normal: Vec3::Y,
            water: false,
        }
    }

    #[test]
    fn test_rng_is_deterministic_per_seed() {
        let mut a = Rng::new(7);
        let mut b = Rng::new(7);
        for _ in 0..8 {
            assert_eq!(a.next_u64(), b.next_u64());
        }
        let mut c = Rng::new(8);
        assert_ne!(a.next_u64(), c.next_u64());
    }

    #[test]
    fn test_rng_range_stays_in_bounds() {
        let mut rng = Rng::new(1);
        for _ in 0..200 {
            let v = rng.range(2.5, 7.5);
            assert!((2.5..=7.5).contains(&v));
        }
    }

    #[test]
    fn test_placements_respect_count_region_and_height() {
        let out = compute_placements(&spec(), &mut flat);
        assert_eq!(out.len(), 10);
        for instance in &out {
            assert!(instance.position.x >= -100.0 && instance.position.x <= 100.0);
            assert!(instance.position.z >= -100.0 && instance.position.z <= 100.0);
            assert_eq!(instance.position.y, 3.0, "align_to_terrain uses sampler");
            assert_eq!(instance.yaw_deg, 0.0, "no random yaw by default");
            assert_eq!(instance.scale, Vec3::ONE);
            assert_eq!(instance.template_index, 0);
        }
    }

    #[test]
    fn test_placements_are_deterministic() {
        let a = compute_placements(&spec(), &mut flat);
        let b = compute_placements(&spec(), &mut flat);
        assert_eq!(a, b);
    }

    #[test]
    fn test_slope_filter_rejects_steep_terrain() {
        let mut s = spec();
        s.max_slope_deg = 40.0;
        let out = compute_placements(&s, &mut |_: f32, _: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::new(1.0, 0.2, 0.0),
            water: false,
        });
        assert!(out.is_empty(), "all candidates exceed the 40° slope limit");
    }

    #[test]
    fn test_water_filter_rejects_water() {
        let mut s = spec();
        s.avoid_water = true;
        let out = compute_placements(&s, &mut |_: f32, _: f32| TerrainSample {
            height: -1.0,
            normal: Vec3::Y,
            water: true,
        });
        assert!(out.is_empty());
    }

    #[test]
    fn test_overlap_filter_enforces_footprint() {
        let mut s = spec();
        s.count = 5;
        s.avoid_overlaps = true;
        s.footprint_radius = 5.0;
        s.region_min = [0.0, 0.0, 0.0];
        s.region_max = [1.0, 0.0, 1.0]; // everything within 5 m of everything
        let out = compute_placements(&s, &mut flat);
        assert_eq!(out.len(), 1, "only the first candidate fits the footprint");
    }

    #[test]
    fn test_random_yaw_and_scale_jitter() {
        let mut s = spec();
        s.random_yaw = true;
        s.scale_min = 0.8;
        s.scale_max = 1.4;
        let out = compute_placements(&s, &mut flat);
        let yaws: Vec<f32> = out.iter().map(|i| i.yaw_deg).collect();
        assert!(yaws.iter().any(|y| *y > 0.0), "yaws should vary: {yaws:?}");
        for instance in &out {
            assert!((0.8..=1.4).contains(&instance.scale.x));
        }
    }
}
