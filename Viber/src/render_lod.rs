//! Render LOD — distance culling and shadow budget for spawned instances.
//!
//! `simple-rpg` places ~9700 glTF scenes (6000 `<StaticSpawner>` props, 3200
//! `<Vegetation>` instances, 484 authored `<GltfScene>`), and every scene
//! expands into several entities: ~60k entities, all of them extracted,
//! frustum-checked and rendered into four shadow cascades every frame on a
//! 4 km map. Nothing beyond the fog is worth that.
//!
//! Two knobs, both mirroring what [`crate::luau::ScriptActivation`] already
//! does for AI:
//!
//! * [`CullDistance`] on a **scene root** hides the whole subtree past a
//!   radius. Visibility is inherited in Bevy, so toggling the root is enough
//!   — no per-mesh component and no propagation walk.
//! * [`NoShadowSubtree`] tags a root whose meshes must not cast. Unlike
//!   visibility, `NotShadowCaster` is read on the *mesh* entity, so this one
//!   does need a one-shot propagation pass once the scene has spawned.

use crate::profiler::{Group, timed};
use bevy::gltf::Gltf;
use bevy::light::NotShadowCaster;
use bevy::prelude::*;

/// Hides the entity (and, by inheritance, its whole subtree) once the camera
/// is further than `radius` meters away.
///
/// The band between `radius * HYSTERESIS` and `radius` is sticky: an object
/// walking the boundary does not flicker on and off with camera jitter.
#[derive(Component, Debug, Clone, Copy)]
pub struct CullDistance {
    /// Distance (meters) past which the subtree stops rendering.
    pub radius: f32,
}

impl CullDistance {
    pub fn new(radius: f32) -> Self {
        Self { radius }
    }
}

/// Fraction of `radius` at which a hidden object becomes visible again.
const CULL_HYSTERESIS: f32 = 0.94;

/// Default cull radius (meters) for `<StaticSpawner>` props — trees, rocks,
/// crates. Far enough that a treeline still reads at the fog line.
pub const DEFAULT_STATIC_CULL: f32 = 320.0;

/// Default cull radius for `<Vegetation>` — grass and flowers, which are
/// sub-meter and stop contributing well before the props do.
pub const DEFAULT_VEGETATION_CULL: f32 = 80.0;

/// Default cull radius for `<DynamicSpawner>` creatures. Beyond this their
/// scripts are frozen anyway (`ScriptActivation`, 45 m by default).
pub const DEFAULT_DYNAMIC_CULL: f32 = 160.0;

/// Marks a scene root whose meshes must not cast shadows. Removed once the
/// propagation lands (the scene spawns asynchronously, so the marker waits).
#[derive(Component, Debug)]
pub struct NoShadowSubtree;

/// Mesh LOD ladder for one spawned instance.
///
/// The `simple-rpg` worlds already author `lod1-url` / `lod2-url` and the
/// `lod-threshold-near` / `lod-threshold-mid` cuts on every `<GLTFLoader>`
/// template — VibeGame honored them, Viber dropped them as no-ops and drew
/// the hero mesh of every pine at 200 m. This component restores the ladder:
/// swapping `WorldAssetRoot` despawns the old scene subtree and spawns the
/// new one (`world_instance_spawner` reacts to `Changed<WorldAssetRoot>`),
/// so only one tier is resident per instance at a time — triangles *and*
/// VRAM, not just draw calls.
#[derive(Component, Debug, Clone)]
pub struct MeshLod {
    /// Scene per tier, coarsest last. Tier 0 is always present.
    pub tiers: Vec<Handle<bevy::world_serialization::WorldAsset>>,
    /// The glTF asset each tier's scene came from, parallel to `tiers`.
    ///
    /// Animation clips are per-gltf assets, so when a swap re-arms the
    /// animation bind ([`crate::animation::rearm_after_scene_swap`]) the new
    /// tier's own gltf has to travel with the swap.
    pub gltf_tiers: Vec<Handle<Gltf>>,
    /// Distance at which tier 0 gives way to tier 1.
    pub near: f32,
    /// Distance at which tier 1 gives way to tier 2.
    pub mid: f32,
    /// Tier currently attached to `WorldAssetRoot`.
    pub current: u8,
    /// Re-tag the fresh subtree as a non-caster after a swap.
    pub no_shadows: bool,
}

impl MeshLod {
    /// Tier index for `distance`, with a sticky band so an instance sitting
    /// on a threshold does not respawn its subtree every frame.
    fn tier_for(&self, distance: f32, current: u8) -> u8 {
        // `already_coarse`: the instance is *on* the coarser tier, so the
        // edge moves inward — it keeps the cheap mesh a little longer.
        // Otherwise the edge moves outward and the detailed mesh holds.
        let hysteresis = |edge: f32, already_coarse: bool| {
            if already_coarse {
                edge * (1.0 - LOD_HYSTERESIS)
            } else {
                edge * (1.0 + LOD_HYSTERESIS)
            }
        };
        let last = (self.tiers.len() as u8).saturating_sub(1);
        let near = hysteresis(self.near, current >= 1);
        let mid = hysteresis(self.mid, current >= 2);
        let wanted = if distance >= mid {
            2
        } else if distance >= near {
            1
        } else {
            0
        };
        wanted.min(last)
    }
}

/// Relative width of the sticky band around each LOD threshold.
const LOD_HYSTERESIS: f32 = 0.08;

/// Scene swaps allowed per frame. A swap despawns and respawns a subtree, so
/// a camera teleport (fast travel) must not try to re-tier 6000 props in one
/// frame — it drains over the next few instead.
///
/// Overridable with `VIBER_LOD_SWAP_BUDGET` (`0` disables the ladder without
/// disabling the culling): the two halves of this plugin have very different
/// costs, and telling them apart in a profile needs them to be separable.
pub const MAX_LOD_SWAPS_PER_FRAME: usize = 24;

/// Resolved swap budget: `VIBER_LOD_SWAP_BUDGET`, else the default.
fn swap_budget() -> usize {
    std::env::var("VIBER_LOD_SWAP_BUDGET")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(MAX_LOD_SWAPS_PER_FRAME)
}

/// Scene swaps performed on the last frame, and instances still waiting.
///
/// A swap is the expensive half of the ladder (despawn + respawn of a glTF
/// subtree); `pending` staying pegged means the budget is saturated and the
/// queue never drains, which reads as a permanent stutter while walking.
#[derive(Resource, Debug, Default, Clone, Copy)]
pub struct MeshLodStats {
    /// Swaps applied on the last frame.
    pub swaps_last_frame: usize,
    /// Instances that wanted a different tier but ran out of budget.
    pub pending: usize,
}

/// Registers the render-LOD systems.
pub struct RenderLodPlugin;

impl bevy::app::Plugin for RenderLodPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<MeshLodStats>()
            .add_observer(retag_no_shadow_on_ready)
            .add_systems(
                bevy::app::PostUpdate,
                (
                    timed(Group::Render, cull_distant_objects),
                    timed(Group::Render, update_mesh_lod),
                    timed(Group::Render, propagate_no_shadow),
                )
                    .chain()
                    .before(bevy::camera::visibility::VisibilitySystems::VisibilityPropagate),
            );
    }
}

/// Re-marks a non-casting LOD instance once its NEW tier has spawned.
///
/// A swap only requests the scene: the scene spawner despawns the old
/// subtree and spawns the new one in the next frame's `SpawnScene`. Tagging
/// at swap time let [`propagate_no_shadow`] find the OLD (already tagged)
/// meshes and clear the marker, so every fresh tier cast shadows again.
/// `WorldInstanceReady` fires right after the spawn, before `PostUpdate`, so
/// the new meshes are tagged before they are ever extracted.
fn retag_no_shadow_on_ready(
    ready: On<bevy::world_serialization::WorldInstanceReady>,
    lods: Query<&MeshLod>,
    mut commands: Commands,
) {
    if lods.get(ready.entity).is_ok_and(|lod| lod.no_shadows) {
        commands.entity(ready.entity).try_insert(NoShadowSubtree);
    }
}

/// Swaps each instance's scene to the tier its camera distance calls for.
///
/// Only hidden-by-culling instances are skipped: a prop the player cannot see
/// does not deserve a respawn, and it re-tiers on the frame it comes back.
fn update_mesh_lod(
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    mut stats: ResMut<MeshLodStats>,
    mut instances: Query<(
        &GlobalTransform,
        &mut MeshLod,
        &mut bevy::world_serialization::WorldAssetRoot,
        &Visibility,
    )>,
) {
    let Some(camera) = cameras.iter().next() else {
        return;
    };
    let eye = camera.translation();
    let mut budget = swap_budget();
    let mut swaps = 0usize;
    let mut pending = 0usize;
    for (transform, mut lod, mut root, visibility) in &mut instances {
        if *visibility == Visibility::Hidden {
            continue;
        }
        if budget == 0 {
            // Ainda conta o trabalho por fazer: é isto que distingue "a fila
            // drenou" de "a fila está saturada todos os frames".
            let distance = transform.translation().distance(eye);
            if lod.tier_for(distance, lod.current) != lod.current {
                pending += 1;
            }
            continue;
        }
        let distance = transform.translation().distance(eye);
        let wanted = lod.tier_for(distance, lod.current);
        if wanted == lod.current {
            continue;
        }
        let Some(scene) = lod.tiers.get(wanted as usize).cloned() else {
            continue;
        };
        // Mutating `WorldAssetRoot` is the swap: the spawner despawns the old
        // instance and spawns the new scene as children of this same entity,
        // so the transform, collider and script all survive untouched. The
        // animation bind does not survive — it re-arms off this very change
        // (see `crate::animation::rearm_after_scene_swap`).
        // The fresh subtree earns its `NotShadowCaster` tags again in
        // `retag_no_shadow_on_ready`, once it has actually spawned.
        root.0 = scene;
        lod.current = wanted;
        budget -= 1;
        swaps += 1;
    }
    stats.swaps_last_frame = swaps;
    stats.pending = pending;
}

/// Toggles `Visibility` on every [`CullDistance`] root from the camera range.
///
/// Runs every frame on purpose: instances stream in over many frames while
/// the spawner drains, so a camera-movement gate would leave fresh props
/// visible across the whole map until the player next moved. The work is a
/// squared-distance compare over ~10k roots, and the write is guarded so
/// change detection (and therefore visibility propagation) only fires on an
/// actual transition.
fn cull_distant_objects(
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    mut objects: Query<(&GlobalTransform, &CullDistance, &mut Visibility)>,
) {
    let Some(camera) = cameras.iter().next() else {
        return;
    };
    let eye = camera.translation();
    for (transform, cull, mut visibility) in &mut objects {
        let radius = cull.radius.max(1.0);
        let distance_sq = transform.translation().distance_squared(eye);
        let wanted = match *visibility {
            // Visible: keep it until it passes the outer edge.
            Visibility::Hidden => {
                let show = radius * CULL_HYSTERESIS;
                if distance_sq <= show * show {
                    Visibility::Inherited
                } else {
                    Visibility::Hidden
                }
            }
            _ => {
                if distance_sq <= radius * radius {
                    Visibility::Inherited
                } else {
                    Visibility::Hidden
                }
            }
        };
        // Guarded: `DerefMut` on `Visibility` would mark every root changed
        // every frame and re-run visibility propagation over the whole tree.
        if *visibility != wanted {
            *visibility = wanted;
        }
    }
}

/// Inserts [`NotShadowCaster`] on every mesh below a [`NoShadowSubtree`] root.
///
/// `NotShadowCaster` is queried on the entity that owns the `Mesh3d`, and
/// glTF scenes spawn their meshes as descendants a few frames after the root
/// exists — hence a marker that survives until the meshes show up.
fn propagate_no_shadow(
    mut commands: Commands,
    roots: Query<Entity, With<NoShadowSubtree>>,
    children: Query<&Children>,
    meshes: Query<Has<NotShadowCaster>, With<Mesh3d>>,
) {
    for root in &roots {
        let mut found = 0usize;
        for descendant in children.iter_descendants(root) {
            let Ok(already_tagged) = meshes.get(descendant) else {
                continue;
            };
            found += 1;
            if !already_tagged {
                commands.entity(descendant).insert(NotShadowCaster);
            }
        }
        // The marker clears as soon as the scene has *any* mesh — counting
        // only the newly tagged ones would keep re-walking the subtree of an
        // instance whose meshes were all tagged already (an LOD swap that
        // reused a cached scene).
        if found > 0 {
            commands.entity(root).remove::<NoShadowSubtree>();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a minimal app with the culling system and a camera at the origin.
    fn app_with_camera() -> App {
        let mut app = App::new();
        app.add_systems(Update, cull_distant_objects);
        app.world_mut().spawn((
            Camera3d::default(),
            GlobalTransform::from_translation(Vec3::ZERO),
        ));
        app
    }

    fn spawn_at(app: &mut App, x: f32, radius: f32) -> Entity {
        app.world_mut()
            .spawn((
                GlobalTransform::from_translation(Vec3::new(x, 0.0, 0.0)),
                CullDistance::new(radius),
                Visibility::Inherited,
            ))
            .id()
    }

    #[test]
    fn test_object_inside_the_radius_stays_visible() {
        let mut app = app_with_camera();
        let entity = spawn_at(&mut app, 50.0, 100.0);
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Inherited
        );
    }

    #[test]
    fn test_object_beyond_the_radius_is_hidden() {
        let mut app = app_with_camera();
        let entity = spawn_at(&mut app, 150.0, 100.0);
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Hidden
        );
    }

    #[test]
    fn test_hysteresis_keeps_a_hidden_object_hidden_inside_the_band() {
        let mut app = app_with_camera();
        // 97 m: inside the 100 m radius, but above the 94 m re-show edge.
        let entity = spawn_at(&mut app, 150.0, 100.0);
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Hidden
        );
        app.world_mut()
            .entity_mut(entity)
            .insert(GlobalTransform::from_translation(Vec3::new(97.0, 0.0, 0.0)));
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Hidden,
            "inside the hysteresis band a hidden object must stay hidden"
        );
        app.world_mut()
            .entity_mut(entity)
            .insert(GlobalTransform::from_translation(Vec3::new(90.0, 0.0, 0.0)));
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Inherited,
            "past the re-show edge it comes back"
        );
    }

    #[test]
    fn test_culling_without_a_camera_is_a_noop() {
        let mut app = App::new();
        app.add_systems(Update, cull_distant_objects);
        let entity = app
            .world_mut()
            .spawn((
                GlobalTransform::from_translation(Vec3::new(9999.0, 0.0, 0.0)),
                CullDistance::new(10.0),
                Visibility::Inherited,
            ))
            .id();
        app.update();
        assert_eq!(
            *app.world().get::<Visibility>(entity).unwrap(),
            Visibility::Inherited
        );
    }

    #[test]
    fn test_no_shadow_marker_tags_mesh_descendants_and_clears() {
        let mut app = App::new();
        app.add_systems(Update, propagate_no_shadow);
        let mesh = app.world_mut().spawn(Mesh3d::default()).id();
        let root = app.world_mut().spawn(NoShadowSubtree).add_child(mesh).id();
        app.update();
        assert!(app.world().get::<NotShadowCaster>(mesh).is_some());
        assert!(
            app.world().get::<NoShadowSubtree>(root).is_none(),
            "the marker clears once the meshes are tagged"
        );
    }

    fn ladder(tiers: usize) -> MeshLod {
        MeshLod {
            tiers: (0..tiers).map(|_| Handle::default()).collect(),
            gltf_tiers: (0..tiers).map(|_| Handle::default()).collect(),
            near: 40.0,
            mid: 100.0,
            current: 0,
            no_shadows: false,
        }
    }

    #[test]
    fn test_tier_follows_the_authored_thresholds() {
        let lod = ladder(3);
        assert_eq!(lod.tier_for(10.0, 0), 0);
        assert_eq!(lod.tier_for(60.0, 0), 1);
        assert_eq!(lod.tier_for(200.0, 0), 2);
    }

    #[test]
    fn test_tier_is_clamped_to_the_tiers_the_template_authored() {
        // Only lod0 + lod1 exist: a far prop stops at tier 1 instead of
        // indexing past the end of the ladder.
        let lod = ladder(2);
        assert_eq!(lod.tier_for(500.0, 0), 1);
    }

    #[test]
    fn test_tier_hysteresis_holds_across_the_threshold() {
        let lod = ladder(3);
        // Coming from tier 0, the 40 m edge moves out to 43.2 m…
        assert_eq!(lod.tier_for(41.0, 0), 0);
        // …and coming back from tier 1 it moves in to 36.8 m.
        assert_eq!(lod.tier_for(38.0, 1), 1);
        assert_eq!(lod.tier_for(36.0, 1), 0);
    }

    /// The fresh tier of a non-casting instance is re-marked when its scene
    /// instance becomes ready — not at swap time, when only the old (already
    /// tagged) subtree exists.
    #[test]
    fn test_scene_ready_re_marks_non_casting_lod_instances() {
        let mut app = App::new();
        app.add_observer(retag_no_shadow_on_ready);
        let mut quiet = ladder(2);
        quiet.no_shadows = true;
        let caster = app.world_mut().spawn(ladder(2)).id();
        let non_caster = app.world_mut().spawn(quiet).id();
        let instance_id = {
            let world = app.world_mut();
            world.init_resource::<bevy::world_serialization::WorldInstanceSpawner>();
            world.resource_scope(|_, mut spawner: Mut<bevy::world_serialization::WorldInstanceSpawner>| {
                spawner.spawn_as_child(Handle::default(), non_caster)
            })
        };
        for entity in [caster, non_caster] {
            app.world_mut().trigger(bevy::world_serialization::WorldInstanceReady { entity, instance_id });
        }
        app.update();
        assert!(app.world().get::<NoShadowSubtree>(non_caster).is_some());
        assert!(app.world().get::<NoShadowSubtree>(caster).is_none());
    }

    #[test]
    fn test_no_shadow_marker_waits_for_the_scene_to_spawn() {
        let mut app = App::new();
        app.add_systems(Update, propagate_no_shadow);
        let root = app.world_mut().spawn(NoShadowSubtree).id();
        app.update();
        assert!(
            app.world().get::<NoShadowSubtree>(root).is_some(),
            "no meshes yet — the marker survives for a later frame"
        );
    }
}
