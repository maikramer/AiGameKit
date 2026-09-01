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

/// Global no-spawn circle (`<SpawnExclusion at="x z" radius="n">`).
#[derive(Debug, Clone, Copy)]
pub struct SpawnExclusion {
    pub center: bevy::math::Vec2,
    pub radius: f32,
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
    /// Point is on dry land but close enough to water to count as shoreline.
    pub near_water: bool,
    /// Point sits on a carved road ribbon (`<Road>` / `<RoadNetwork>`).
    pub road: bool,
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
    exclusions: &[SpawnExclusion],
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
        if exclusions
            .iter()
            .any(|e| (pos - e.center).length() < e.radius)
        {
            continue;
        }
        let terrain = sample(pos.x, pos.y);
        // Water and road placement rules. `avoid-water` keeps scenery out of
        // lakes and river channels; `near-water` / `in-water` are the inverse
        // (reeds on a shoreline, lilies on the surface); `avoid-road` keeps
        // props off the carved ribbons so a road stays walkable.
        if spec.avoid_water && terrain.water {
            continue;
        }
        if spec.in_water && !terrain.water {
            continue;
        }
        if spec.near_water && !terrain.water && !terrain.near_water {
            continue;
        }
        if spec.avoid_road && terrain.road {
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
    /// True para `<DynamicSpawner>` — as instâncias nascem com
    /// [`crate::ai::EnemyCreature`] e o driver de IA conduz-as.
    pub dynamic: bool,
    /// Script do template (criaturas): cada instância spawna com
    /// [`LuaScriptRef`] e o comportamento vive no Luau.
    pub template_script: Option<String>,
    /// Raio de ativação (congelamento) replicado às instâncias.
    pub activation_radius: f32,
}

/// All spawner groups collected at startup; consumed by
/// [`instantiate_spawn_groups`] once the terrain runtime and the template
/// assets are ready.
#[derive(Resource)]
pub struct PendingSpawnGroups {
    pub groups: Vec<SpawnGroupState>,
    /// `<SpawnExclusion>` circles collected across the whole world.
    pub exclusions: Vec<SpawnExclusion>,
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
    let exclusions = pending.exclusions.clone();
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
        let near_radius = group.spec.near_water_radius;
        let mut sample = |x: f32, z: f32| TerrainSample {
            height: runtime.sample(x, z),
            normal: grid.sample_normal(x, z, 0.5),
            water: runtime.in_water(x, z),
            // A shoreline test: within `near_water_radius` of a water body,
            // sampled on the four axes so a bank on any side counts.
            near_water: [
                (near_radius, 0.0),
                (-near_radius, 0.0),
                (0.0, near_radius),
                (0.0, -near_radius),
            ]
            .iter()
            .any(|(dx, dz)| runtime.in_water(x + dx, z + dz)),
            road: runtime.on_road(x, z),
        };
        for instance in compute_placements(&group.spec, &exclusions, &mut sample) {
            let mut transform = Transform::from_translation(instance.position);
            transform.rotation = bevy::math::Quat::from_rotation_y(instance.yaw_deg.to_radians());
            transform.scale = instance.scale;
            if let Some(scene) = scenes[instance.template_index.min(scenes.len() - 1)].clone() {
                let script_bundle = group.template_script.as_ref().map(|script| {
                    (
                        crate::luau::LuaScriptRef {
                            path: script.clone(),
                        },
                        crate::luau::ScriptActivation {
                            radius: group.activation_radius,
                        },
                    )
                });
                if group.dynamic {
                    // Com script no template o comportamento é do Luau (a
                    // engine só provê os blocos); sem script, cai na FSM Rust.
                    if let Some(script) = &group.template_script {
                        commands.spawn((
                            transform,
                            Visibility::Inherited,
                            WorldAssetRoot(scene),
                            crate::luau::LuaScriptRef {
                                path: script.clone(),
                            },
                            crate::luau::ScriptActivation {
                                radius: group.activation_radius,
                            },
                            // Vitals para o combate (dano/morte) + animação.
                            crate::vitals::Health::default(),
                            crate::animation::AnimatedScene {
                                gltf: group.handles
                                    [instance.template_index.min(group.handles.len() - 1)]
                                .clone(),
                            },
                        ));
                    } else {
                        commands.spawn((
                            transform,
                            Visibility::Inherited,
                            WorldAssetRoot(scene),
                            crate::ai::EnemyCreature::default(),
                        ));
                    }
                } else if let Some((script_ref, activation)) = script_bundle {
                    // Estático com script (ex.: árvores/rochas colhíveis): o
                    // congelamento por raio mantém 380 árvores baratas.
                    commands.spawn((
                        transform,
                        Visibility::Inherited,
                        WorldAssetRoot(scene),
                        script_ref,
                        activation,
                    ));
                } else {
                    commands.spawn((transform, Visibility::Inherited, WorldAssetRoot(scene)));
                }
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
            in_water: false,
            near_water: false,
            near_water_radius: 4.0,
            avoid_road: false,
            align_to_terrain: true,
            scale_min: 1.0,
            scale_max: 1.0,
            scale_axis_min: 1.0,
            scale_axis_max: 1.0,
            random_yaw: false,
            max_distance: 0.0,
            template_urls: vec!["/assets/meshes/a.glb".into()],
            template_script: None,
            activation_radius: 45.0,
        }
    }

    fn flat(_x: f32, _z: f32) -> TerrainSample {
        TerrainSample {
            height: 3.0,
            normal: Vec3::Y,
            water: false,
            near_water: false,
            road: false,
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
        let out = compute_placements(&spec(), &[], &mut flat);
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
        let a = compute_placements(&spec(), &[], &mut flat);
        let b = compute_placements(&spec(), &[], &mut flat);
        assert_eq!(a, b);
    }

    #[test]
    fn test_slope_filter_rejects_steep_terrain() {
        let mut s = spec();
        s.max_slope_deg = 40.0;
        let out = compute_placements(&s, &[], &mut |_: f32, _: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::new(1.0, 0.2, 0.0),
            water: false,
            near_water: false,
            road: false,
        });
        assert!(out.is_empty(), "all candidates exceed the 40° slope limit");
    }

    #[test]
    fn test_water_filter_rejects_water() {
        let mut s = spec();
        s.avoid_water = true;
        let out = compute_placements(&s, &[], &mut |_: f32, _: f32| TerrainSample {
            height: -1.0,
            normal: Vec3::Y,
            water: true,
            near_water: false,
            road: false,
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
        let out = compute_placements(&s, &[], &mut flat);
        assert_eq!(out.len(), 1, "only the first candidate fits the footprint");
    }

    #[test]
    fn test_spawn_exclusion_rejects_circle() {
        let mut s = spec();
        s.count = 20;
        // flat terrain everywhere; the whole region is otherwise valid
        let mut no_excl = compute_placements(&s, &[], &mut flat);
        let with_excl = compute_placements(
            &s,
            &[SpawnExclusion {
                center: bevy::math::Vec2::ZERO,
                radius: 150.0,
            }],
            &mut flat,
        );
        assert!(!no_excl.is_empty());
        // every instance outside the exclusion circle
        for instance in &with_excl {
            let d = bevy::math::Vec2::new(instance.position.x, instance.position.z).length();
            assert!(
                d >= 150.0,
                "instance at {d:.1} m inside the 150 m exclusion"
            );
        }
        no_excl.clear();
    }

    #[test]
    fn test_vegetation_spec_to_spawner_spec_caps_count() {
        let mut v = crate::recipes::VegetationSpec {
            meshes: vec!["/assets/meshes/vegetation/grass.glb".into()],
            density_per_km2: 100_000.0,
            seed: 601,
            region_min: [-190.0, 0.0, 116.0],
            region_max: [190.0, 0.0, 380.0],
            scale_min: 0.9,
            scale_max: 1.5,
            scale_axis_min: 0.9,
            scale_axis_max: 1.1,
            max_slope_deg: 26.0,
            avoid_water: true,
            avoid_road: false,
            avoid_overlaps: true,
            random_yaw: true,
            max_distance: 110.0,
            cluster_count: 128,
            cluster_radius: 8.8,
            max_instances: 800,
        };
        // 380×264 m = 0.1 km² × 100k = ~10 028 → capped at 800
        assert_eq!(v.instance_count(), 800);
        let group = v.to_spawner_spec();
        assert_eq!(group.count, 800);
        assert_eq!(group.template_urls.len(), 1);
        assert!(group.avoid_water && group.random_yaw);
        // small density: uncapped, rounded up
        v.density_per_km2 = 100.0;
        v.max_instances = 800;
        assert_eq!(v.instance_count(), 11);
    }

    #[test]
    fn test_random_yaw_and_scale_jitter() {
        let mut s = spec();
        s.random_yaw = true;
        s.scale_min = 0.8;
        s.scale_max = 1.4;
        let out = compute_placements(&s, &[], &mut flat);
        let yaws: Vec<f32> = out.iter().map(|i| i.yaw_deg).collect();
        assert!(yaws.iter().any(|y| *y > 0.0), "yaws should vary: {yaws:?}");
        for instance in &out {
            assert!((0.8..=1.4).contains(&instance.scale.x));
        }
    }

    /// Water and road rules pick opposite sides of the same query, so each one
    /// is checked against a field that is half water / half road.
    #[test]
    fn test_placement_water_and_road_rules() {
        let base = || {
            let mut s = spec();
            s.count = 40;
            s.region_min = [-50.0, 0.0, -50.0];
            s.region_max = [50.0, 0.0, 50.0];
            s.avoid_overlaps = false;
            s
        };
        // West half is water, east half is road.
        let mut field = |x: f32, _z: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::Y,
            water: x < 0.0,
            near_water: (0.0..6.0).contains(&x),
            road: x > 0.0,
        };

        let mut s = base();
        s.avoid_water = true;
        let out = compute_placements(&s, &[], &mut field);
        assert!(!out.is_empty(), "some candidates land on dry ground");
        assert!(
            out.iter().all(|i| i.position.x >= 0.0),
            "avoid-water keeps every instance out of the water half"
        );

        let mut s = base();
        s.in_water = true;
        let out = compute_placements(&s, &[], &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x < 0.0),
            "in-water places only inside the water half"
        );

        let mut s = base();
        s.avoid_road = true;
        let out = compute_placements(&s, &[], &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x <= 0.0),
            "avoid-road keeps every instance off the road half"
        );

        let mut s = base();
        s.near_water = true;
        let out = compute_placements(&s, &[], &mut field);
        assert!(!out.is_empty());
        assert!(
            out.iter().all(|i| i.position.x < 6.0),
            "near-water places on the bank (or in the water), not inland"
        );
    }

    /// `random-yaw` is what stops a stand of identical trees reading as clones.
    #[test]
    fn test_random_yaw_spreads_headings() {
        let mut s = spec();
        s.count = 24;
        s.region_min = [-50.0, 0.0, -50.0];
        s.region_max = [50.0, 0.0, 50.0];
        s.avoid_overlaps = false;
        s.random_yaw = true;
        let mut flat = |_: f32, _: f32| TerrainSample {
            height: 0.0,
            normal: Vec3::Y,
            water: false,
            near_water: false,
            road: false,
        };
        let out = compute_placements(&s, &[], &mut flat);
        let yaws: Vec<f32> = out.iter().map(|i| i.yaw_deg).collect();
        assert!(yaws.len() > 4);
        let distinct = yaws.iter().filter(|y| (**y - yaws[0]).abs() > 1.0).count();
        assert!(distinct > 0, "headings vary: {yaws:?}");
        assert!(
            yaws.iter().all(|y| (0.0..=360.0).contains(y)),
            "headings stay in range"
        );
    }
}
