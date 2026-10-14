//! Selection and the transform gizmo in the viewport, Godot-style.
//!
//! * Click (no drag) picks the node under the cursor (`viber::scene::pick`) and reveals it in
//!   the Objects dock.
//! * Q select · W move · E rotate · R scale · T local/global axes · F frame the selection ·
//!   Ctrl while dragging snaps (1 m / 15° / 0.1) · Esc cancels a drag.
//! * A drag previews on the entity's `Transform`; releasing commits ONE canonical pose edit
//!   (`viber::scene::pose::pose_op`) through the engine's editing API — undoable, saved to the
//!   node's own file, identical to what an agent would write.

use bevy::camera::visibility::RenderLayers;
use bevy::gizmos::config::{DefaultGizmoConfigGroup, GizmoConfigStore};
use bevy::input_focus::InputFocus;
use bevy::math::{Affine3A, Isometry3d};
use bevy::prelude::*;
use bevy::ui::UiGlobalTransform;
use bevy::window::PrimaryWindow;
use viber::editor_camera::{EditorCamState, EditorCamera};
use viber::scene::SceneSource;
use viber::scene::pose::{Pose, PoseParts, pose_op};

use super::gizmo_math::{
    angle_delta, angle_on_plane, closest_on_axis, distance_to_polyline, distance_to_segment,
    plane_basis, snap,
};
use super::{EditorViewport, ViewportHover};
use crate::scene::doc::{NodeRef, SceneDoc};
use crate::scene::{PendingEdits, Selection, TreeExpansion, reveal};

/// Render layer only the editor's 3D camera sees: gizmos never draw on the editor UI camera.
pub const GIZMO_LAYER: usize = 31;
/// Gizmo size as a fraction of the camera distance (constant on screen).
const SIZE_FRACTION: f32 = 0.16;
/// Screen-space grab tolerance (pixels).
const GRAB_PX: f32 = 10.0;
/// A press that moves less than this is a click (pick), not a drag.
const CLICK_PX: f32 = 4.0;
const SNAP_MOVE: f32 = 1.0;
const SNAP_ROTATE_DEG: f32 = 15.0;
const SNAP_SCALE: f32 = 0.1;
const RING_SEGMENTS: usize = 48;
const AXES: [Vec3; 3] = [Vec3::X, Vec3::Y, Vec3::Z];
const AXIS_COLORS: [Srgba; 3] = [
    Srgba::rgb(0.95, 0.3, 0.3),
    Srgba::rgb(0.4, 0.9, 0.35),
    Srgba::rgb(0.35, 0.55, 1.0),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Mode {
    Select,
    #[default]
    Translate,
    Rotate,
    Scale,
    /// Terrain brush ([`super::terrain_tool`]).
    Terrain,
}

/// The gizmo's systems (the terrain tool runs after them).
#[derive(SystemSet, Debug, Clone, PartialEq, Eq, Hash)]
pub struct GizmoSet;

/// The active tool.
#[derive(Resource, Debug, Clone, Copy, Default)]
pub struct GizmoTool {
    pub mode: Mode,
    /// Axes follow the node's rotation instead of the world's.
    pub local: bool,
}

/// The live entity of the selected node (`None` when it has no single entity — e.g. a
/// `<Use>`-instanced prototype child — or no entity at all, like a `<Sky>`).
#[derive(Resource, Debug, Default, Clone, Copy)]
pub struct SelectedEntity(pub Option<Entity>);

/// The cursor as a world ray, when it is over the viewport.
#[derive(Resource, Debug, Default, Clone, Copy)]
pub struct ViewportCursor {
    pub screen: Option<Vec2>,
    pub ray: Option<Ray3d>,
}

#[derive(Debug, Clone)]
struct Drag {
    axis: usize,
    node: NodeRef,
    entity: Entity,
    start_local: Transform,
    parent_world: Affine3A,
    origin: Vec3,
    axis_dir: Vec3,
    start_world_rotation: Quat,
    start_param: f32,
    preview: Transform,
}

/// Press/drag state of the left button over the viewport.
#[derive(Resource, Debug, Default)]
pub struct GizmoInput {
    press: Option<Vec2>,
    drag: Option<Drag>,
    hover: Option<usize>,
}

pub struct GizmoPlugin;

impl Plugin for GizmoPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<GizmoTool>()
            .init_resource::<SelectedEntity>()
            .init_resource::<ViewportCursor>()
            .init_resource::<GizmoInput>()
            .add_systems(Startup, configure_gizmos)
            .add_systems(
                Update,
                (
                    track_cursor,
                    resolve_selected_entity,
                    tool_keys,
                    gizmo_interaction,
                    draw_selection,
                )
                    .chain()
                    .in_set(GizmoSet)
                    .after(crate::scene::apply_pending_edits),
            );
    }
}

fn configure_gizmos(mut store: ResMut<GizmoConfigStore>) {
    let (config, _) = store.config_mut::<DefaultGizmoConfigGroup>();
    config.render_layers = RenderLayers::layer(GIZMO_LAYER);
    // Handles stay readable inside geometry.
    config.depth_bias = -1.0;
    config.line.width = 2.5;
}

/// Window cursor → viewport pixel → world ray through the editor camera.
fn track_cursor(
    windows: Query<&Window, With<PrimaryWindow>>,
    viewports: Query<(&ComputedNode, &UiGlobalTransform), With<EditorViewport>>,
    cameras: Query<(&Camera, &GlobalTransform), With<EditorCamera>>,
    mut cursor: ResMut<ViewportCursor>,
) {
    *cursor = ViewportCursor::default();
    let (Ok(window), Ok((node, ui_transform)), Ok((camera, camera_global))) =
        (windows.single(), viewports.single(), cameras.single())
    else {
        return;
    };
    let Some(position) = window.cursor_position() else {
        return;
    };
    let physical = position * window.scale_factor();
    let size = node.size();
    let top_left = ui_transform.translation - size * 0.5;
    let local = physical - top_left;
    if local.x < 0.0 || local.y < 0.0 || local.x > size.x || local.y > size.y {
        return;
    }
    cursor.screen = Some(local);
    cursor.ray = camera.viewport_to_world(camera_global, local).ok();
}

fn resolve_selected_entity(
    selection: Res<Selection>,
    doc: Res<SceneDoc>,
    sources: Query<(Entity, &SceneSource)>,
    mut selected: ResMut<SelectedEntity>,
) {
    selected.0 = selection
        .primary()
        .filter(|node| !node.is_root())
        .and_then(|node| {
            let origin = doc.origin_of(node);
            let mut matches = sources
                .iter()
                .filter(|(_, source)| *source.0 == origin)
                .map(|(e, _)| e);
            let first = matches.next()?;
            matches.next().is_none().then_some(first)
        });
}

#[allow(clippy::too_many_arguments)] // Bevy system: one parameter per resource/query.
fn tool_keys(
    keys: Res<ButtonInput<KeyCode>>,
    buttons: Res<ButtonInput<MouseButton>>,
    focus: Option<Res<InputFocus>>,
    editable: Query<(), With<bevy::text::EditableText>>,
    hover: Res<ViewportHover>,
    mut tool: ResMut<GizmoTool>,
    selected: Res<SelectedEntity>,
    globals: Query<&GlobalTransform>,
    mut camera: Option<ResMut<EditorCamState>>,
) {
    // Flying (RMB + WASD/QE) owns those keys; a focused text field owns all of them.
    let typing = focus
        .and_then(|f| f.get())
        .is_some_and(|entity| editable.contains(entity));
    let ctrl = keys.any_pressed([KeyCode::ControlLeft, KeyCode::ControlRight]);
    if typing || ctrl || buttons.pressed(MouseButton::Right) || !hover.0 {
        return;
    }
    for (key, mode) in [
        (KeyCode::KeyQ, Mode::Select),
        (KeyCode::KeyW, Mode::Translate),
        (KeyCode::KeyE, Mode::Rotate),
        (KeyCode::KeyR, Mode::Scale),
    ] {
        if keys.just_pressed(key) {
            tool.mode = mode;
        }
    }
    if keys.just_pressed(KeyCode::KeyT) {
        tool.local = !tool.local;
    }
    if keys.just_pressed(KeyCode::KeyF)
        && let (Some(entity), Some(camera)) = (selected.0, camera.as_deref_mut())
        && let Ok(global) = globals.get(entity)
    {
        let _ = viber::editor_camera::look_at(camera, global.translation());
        camera.distance = camera.distance.min(60.0);
    }
}

/// World position, size and axes of the gizmo for the selected entity.
fn gizmo_frame(
    world: &World,
    entity: Entity,
    local_axes: bool,
    camera_eye: Vec3,
) -> Option<(Vec3, f32, [Vec3; 3])> {
    let global = world.get::<GlobalTransform>(entity)?;
    let origin = global.translation();
    let size = (origin.distance(camera_eye) * SIZE_FRACTION).max(0.05);
    let axes = if local_axes {
        let rotation = global.to_scale_rotation_translation().1;
        AXES.map(|axis| rotation * axis)
    } else {
        AXES
    };
    Some((origin, size, axes))
}

fn ring_points(origin: Vec3, axis: Vec3, radius: f32) -> Vec<Vec3> {
    let (u, v) = plane_basis(axis);
    (0..RING_SEGMENTS)
        .map(|i| {
            let angle = i as f32 / RING_SEGMENTS as f32 * std::f32::consts::TAU;
            origin + (u * angle.cos() + v * angle.sin()) * radius
        })
        .collect()
}

/// The handle (axis index) under the cursor.
fn hovered_handle(
    world: &World,
    mode: Mode,
    origin: Vec3,
    size: f32,
    axes: [Vec3; 3],
    screen: Vec2,
) -> Option<usize> {
    let mut cameras =
        world.try_query_filtered::<(&Camera, &GlobalTransform), With<EditorCamera>>()?;
    let (camera, camera_global) = cameras.single(world).ok()?;
    let project = |p: Vec3| camera.world_to_viewport(camera_global, p).ok();
    let mut best: Option<(usize, f32)> = None;
    for (index, axis) in axes.iter().enumerate() {
        let distance = match mode {
            Mode::Select | Mode::Terrain => return None,
            Mode::Translate | Mode::Scale => {
                let (Some(a), Some(b)) = (project(origin), project(origin + *axis * size)) else {
                    continue;
                };
                distance_to_segment(screen, a, b)
            }
            Mode::Rotate => {
                let points: Option<Vec<Vec2>> = ring_points(origin, *axis, size)
                    .into_iter()
                    .map(project)
                    .collect();
                let Some(points) = points else {
                    continue;
                };
                distance_to_polyline(screen, &points)
            }
        };
        if distance < GRAB_PX && best.is_none_or(|(_, d)| distance < d) {
            best = Some((index, distance));
        }
    }
    best.map(|(index, _)| index)
}

/// Pick, grab, drag and commit — exclusive, because picking reads the whole world.
fn gizmo_interaction(world: &mut World) {
    let cursor = *world.resource::<ViewportCursor>();
    let tool = *world.resource::<GizmoTool>();
    let selected = world.resource::<SelectedEntity>().0;
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let keys = world.resource::<ButtonInput<KeyCode>>().clone();
    let snapping = keys.any_pressed([KeyCode::ControlLeft, KeyCode::ControlRight]);
    let eye = world
        .get_resource::<EditorCamState>()
        .map(EditorCamState::eye)
        .unwrap_or(Vec3::ZERO);

    if tool.mode == Mode::Terrain {
        // The terrain brush owns the left button.
        return;
    }
    let frame = selected.and_then(|entity| gizmo_frame(world, entity, tool.local, eye));
    let hover = match (frame, cursor.screen) {
        (Some((origin, size, axes)), Some(screen))
            if world.resource::<GizmoInput>().drag.is_none() =>
        {
            hovered_handle(world, tool.mode, origin, size, axes, screen)
        }
        _ => world
            .resource::<GizmoInput>()
            .drag
            .as_ref()
            .map(|drag| drag.axis),
    };
    world.resource_mut::<GizmoInput>().hover = hover;

    // Esc cancels a drag: back to where it started.
    if keys.just_pressed(KeyCode::Escape)
        && let Some(drag) = world.resource_mut::<GizmoInput>().drag.take()
    {
        if let Some(mut transform) = world.get_mut::<Transform>(drag.entity) {
            *transform = drag.start_local;
        }
        viber::scene::sync::teleport_body(world, drag.entity, drag.start_local);
        return;
    }

    // Um handle de feature (Lake/River/Road) tem prioridade sobre o pick.
    if world
        .get_resource::<super::feature_handles::FeatureGrab>()
        .is_some()
    {
        return;
    }
    if buttons.just_pressed(MouseButton::Left) && world.resource::<ViewportHover>().0 {
        let Some(screen) = cursor.screen else {
            return;
        };
        world.resource_mut::<GizmoInput>().press = Some(screen);
        if let (Some(axis), Some(entity), Some((origin, _, axes)), Some(ray)) =
            (hover, selected, frame, cursor.ray)
        {
            start_drag(world, tool.mode, axis, entity, origin, axes[axis], ray);
        }
        return;
    }

    if buttons.pressed(MouseButton::Left) {
        if let Some(ray) = cursor.ray {
            update_drag(world, tool.mode, ray, snapping);
        }
        return;
    }

    if buttons.just_released(MouseButton::Left) {
        let press = world.resource_mut::<GizmoInput>().press.take();
        if let Some(drag) = world.resource_mut::<GizmoInput>().drag.take() {
            commit_drag(world, tool.mode, drag);
            return;
        }
        let is_click =
            matches!((press, cursor.screen), (Some(a), Some(b)) if a.distance(b) < CLICK_PX);
        if is_click && let Some(ray) = cursor.ray {
            pick_at(world, ray);
        }
    }
}

fn start_drag(
    world: &mut World,
    mode: Mode,
    axis: usize,
    entity: Entity,
    origin: Vec3,
    axis_dir: Vec3,
    ray: Ray3d,
) {
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        return;
    };
    let Some(start_local) = world.get::<Transform>(entity).copied() else {
        return;
    };
    let parent_world = world
        .get::<ChildOf>(entity)
        .and_then(|child_of| world.get::<GlobalTransform>(child_of.parent()))
        .map(GlobalTransform::affine)
        .unwrap_or(Affine3A::IDENTITY);
    let start_world_rotation = world
        .get::<GlobalTransform>(entity)
        .map(|g| g.to_scale_rotation_translation().1)
        .unwrap_or(Quat::IDENTITY);
    let start_param = match mode {
        Mode::Rotate => angle_on_plane(origin, axis_dir, ray.origin, *ray.direction),
        _ => closest_on_axis(origin, axis_dir, ray.origin, *ray.direction),
    };
    let Some(start_param) = start_param else {
        return;
    };
    world.resource_mut::<GizmoInput>().drag = Some(Drag {
        axis,
        node,
        entity,
        start_local,
        parent_world,
        origin,
        axis_dir,
        start_world_rotation,
        start_param,
        preview: start_local,
    });
}

fn update_drag(world: &mut World, mode: Mode, ray: Ray3d, snapping: bool) {
    let Some(mut drag) = world.resource::<GizmoInput>().drag.clone() else {
        return;
    };
    let to_local = drag.parent_world.inverse();
    let parent_rotation = drag.parent_world.to_scale_rotation_translation().1;
    let mut preview = drag.start_local;
    match mode {
        Mode::Select | Mode::Terrain => return,
        Mode::Translate => {
            let Some(t) = closest_on_axis(drag.origin, drag.axis_dir, ray.origin, *ray.direction)
            else {
                return;
            };
            let mut delta = t - drag.start_param;
            if snapping {
                delta = snap(delta, SNAP_MOVE);
            }
            let world_position = drag.origin + drag.axis_dir * delta;
            preview.translation = to_local.transform_point3(world_position);
        }
        Mode::Rotate => {
            let Some(angle) =
                angle_on_plane(drag.origin, drag.axis_dir, ray.origin, *ray.direction)
            else {
                return;
            };
            let mut delta = angle_delta(drag.start_param, angle);
            if snapping {
                delta = snap(delta, SNAP_ROTATE_DEG.to_radians());
            }
            let world_rotation =
                Quat::from_axis_angle(drag.axis_dir, delta) * drag.start_world_rotation;
            preview.rotation = (parent_rotation.inverse() * world_rotation).normalize();
        }
        Mode::Scale => {
            let Some(t) = closest_on_axis(drag.origin, drag.axis_dir, ray.origin, *ray.direction)
            else {
                return;
            };
            if drag.start_param.abs() < 1e-4 {
                return;
            }
            let mut factor = (t / drag.start_param).max(0.01);
            if snapping {
                factor = snap(factor, SNAP_SCALE).max(SNAP_SCALE);
            }
            // Scale handles act on the node's own axes.
            let mut scale = drag.start_local.scale;
            scale[drag.axis] *= factor;
            preview.scale = scale;
        }
    }
    drag.preview = preview;
    if let Some(mut transform) = world.get_mut::<Transform>(drag.entity) {
        *transform = preview;
    }
    // A rigid body owns its pose (Rapier writes it back every frame): move the body too.
    viber::scene::sync::teleport_body(world, drag.entity, preview);
    world.resource_mut::<GizmoInput>().drag = Some(drag);
}

fn commit_drag(world: &mut World, mode: Mode, drag: Drag) {
    if drag.preview == drag.start_local {
        return;
    }
    let doc = world.resource::<SceneDoc>();
    let Some(xml) = doc.node(&drag.node) else {
        return;
    };
    let selector = doc.selector(&drag.node);
    let parts = match mode {
        Mode::Translate => PoseParts::TRANSLATION,
        Mode::Rotate => PoseParts {
            translation: false,
            rotation: true,
            scale: false,
        },
        Mode::Scale => PoseParts {
            translation: false,
            rotation: false,
            scale: true,
        },
        Mode::Select | Mode::Terrain => return,
    };
    let pose = Pose {
        translation: drag.preview.translation,
        rotation: drag.preview.rotation,
        scale: drag.preview.scale,
    };
    let lang = *world.resource::<crate::ui::strings::Lang>();
    let verb = match mode {
        Mode::Translate => crate::ui::strings::t(lang, crate::ui::strings::StrKey::VerbMove),
        Mode::Rotate => crate::ui::strings::t(lang, crate::ui::strings::StrKey::VerbRotate),
        _ => crate::ui::strings::t(lang, crate::ui::strings::StrKey::VerbScale),
    };
    match pose_op(&selector, xml, pose, parts) {
        Ok(op) => world
            .resource_mut::<PendingEdits>()
            .push(format!("{verb} {selector}"), vec![op]),
        Err(error) => warn!("{verb} {selector}: {error:#}"),
    }
}

fn pick_at(world: &mut World, ray: Ray3d) {
    let picked = viber::scene::pick::pick(world, ray.origin, *ray.direction, 4000.0);
    if let Some(node) = picked.map(|picked| picked.node) {
        let doc = world.resource::<SceneDoc>().clone();
        reveal(&doc, &node, &mut world.resource_mut::<TreeExpansion>());
        world.resource_mut::<Selection>().set_one(node);
    }
}

/// The selection's bounds and the gizmo handles.
fn draw_selection(world: &mut World) {
    let Some(entity) = world.resource::<SelectedEntity>().0 else {
        return;
    };
    let tool = *world.resource::<GizmoTool>();
    let hover = world.resource::<GizmoInput>().hover;
    let eye = world
        .get_resource::<EditorCamState>()
        .map(EditorCamState::eye)
        .unwrap_or(Vec3::ZERO);
    let bounds = viber::bridge::selection::world_aabb_of(entity, world);
    let frame = gizmo_frame(world, entity, tool.local, eye);
    let _ = world.run_system_cached_with(draw_handles, (bounds, frame, tool.mode, hover));
}

type DrawInput = (
    Option<bevy::shape::Aabb3d>,
    Option<(Vec3, f32, [Vec3; 3])>,
    Mode,
    Option<usize>,
);

fn draw_handles(In((bounds, frame, mode, hover)): In<DrawInput>, mut gizmos: Gizmos) {
    if let Some(bounds) = bounds {
        gizmos.aabb_3d(bounds, Transform::IDENTITY, Srgba::rgb(1.0, 0.85, 0.2));
    }
    let Some((origin, size, axes)) = frame else {
        return;
    };
    for (index, axis) in axes.iter().enumerate() {
        let color = if hover == Some(index) {
            Srgba::rgb(1.0, 1.0, 0.3)
        } else {
            AXIS_COLORS[index]
        };
        match mode {
            Mode::Select | Mode::Terrain => {}
            Mode::Translate => {
                gizmos.arrow(origin, origin + *axis * size, color);
            }
            Mode::Scale => {
                let end = origin + *axis * size;
                gizmos.line(origin, end, color);
                gizmos.cube(
                    Transform::from_translation(end).with_scale(Vec3::splat(size * 0.08)),
                    color,
                );
            }
            Mode::Rotate => {
                let rotation = Quat::from_rotation_arc(Vec3::Z, *axis);
                gizmos
                    .circle(Isometry3d::new(origin, rotation), size, color)
                    .resolution(RING_SEGMENTS as u32);
            }
        }
    }
}
