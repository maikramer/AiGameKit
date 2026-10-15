//! The Stamp tool (Fase J): pick a GLB (dock Files → "Carimbar"), then click
//! the terrain to place instances — Y from the ray hit, yaw cycling per stamp
//! ([ / ] adjust scale via the brush keys' spirit), drag-place with minimum
//! spacing, one coalesced undo per stroke. Esc/Q returns to Select.

use bevy::prelude::*;
use viber::scene::doc::NodeRef;
use viber::scene::terrain::ray_ground;

use super::ViewportHover;
use super::gizmo::{GizmoTool, Mode, ViewportCursor};
use super::terrain_tool::DAB_INTERVAL;

/// The active stamp asset (GLB path from the Files dock).
#[derive(Resource, Debug, Default, Clone)]
pub struct ActiveStamp(pub Option<String>);

/// Per-stroke stamp state (coalescing + spacing).
#[derive(Resource, Debug, Default)]
pub struct StampStroke {
    key: Option<u64>,
    next_key: u64,
    since_dab: std::time::Duration,
    last: Option<Vec3>,
}

/// Minimum distance between stamped instances while dragging (metres).
const SPACING: f32 = 2.5;

pub struct StampToolPlugin;

impl Plugin for StampToolPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<ActiveStamp>()
            .init_resource::<StampStroke>()
            .add_systems(
                Update,
                (stamp_keys, stamp_place, draw_stamp_cursor)
                    .chain()
                    .after(super::gizmo::GizmoSet),
            );
    }
}

/// Esc/Q or empty asset leaves Stamp mode.
fn leave_stamp(world: &mut World) {
    world.resource_mut::<GizmoTool>().mode = Mode::Select;
    world.resource_mut::<ActiveStamp>().0 = None;
    *world.resource_mut::<StampStroke>() = StampStroke::default();
}

/// Places stamps: press starts a stroke; dabs (time + spacing gated) insert
/// one instance each, coalesced into ONE undo per stroke.
fn stamp_place(world: &mut World) {
    if world.resource::<GizmoTool>().mode != Mode::Stamp {
        world.resource_mut::<StampStroke>().key = None;
        return;
    }
    let Some(asset) = world.resource::<ActiveStamp>().0.clone() else {
        // sem asset ativo, o modo não tem efeito (o painel avisa)
        return;
    };
    let cursor = *world.resource::<ViewportCursor>();
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let delta = world.resource::<Time<Real>>().delta();
    let starting = buttons.just_pressed(MouseButton::Left);
    if starting && !world.resource::<ViewportHover>().0 {
        return;
    }
    if !buttons.pressed(MouseButton::Left) {
        world.resource_mut::<StampStroke>().key = None;
        world.resource_mut::<StampStroke>().last = None;
        return;
    }
    let hit = cursor.ray.and_then(|ray| {
        let runtime = world.get_resource::<viber::terrain::runtime::TerrainRuntime>()?;
        ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
            runtime.sample(x, z)
        })
    });
    let Some(at) = hit else {
        return;
    };
    // stroke key + espaçamento
    let key = {
        let mut stroke = world.resource_mut::<StampStroke>();
        if starting || stroke.key.is_none() {
            stroke.next_key += 1;
            stroke.key = Some(stroke.next_key);
            stroke.since_dab = DAB_INTERVAL;
            stroke.last = None;
        }
        stroke.since_dab += delta;
        if stroke.since_dab < DAB_INTERVAL {
            return;
        }
        if let Some(last) = stroke.last
            && last.distance(at) < SPACING
        {
            return;
        }
        stroke.since_dab = std::time::Duration::ZERO;
        stroke.last = Some(at);
        stroke.key.expect("set above")
    };
    // yaw cicla por carimbo (variedade visual sem UI extra)
    let yaw = {
        let mut stroke = world.resource_mut::<StampStroke>();
        let yaw = (stroke.next_key as f32 * 37.0) % 360.0;
        let _ = &mut stroke;
        yaw
    };
    let parent = world
        .resource::<viber::scene::doc::SceneDoc>()
        .selector(&NodeRef::root(0));
    let doc = world.resource::<viber::scene::doc::SceneDoc>().clone();
    match viber::scene::catalog::glb_drop_pose(&doc, &parent, &asset, [at.x, at.y, at.z], yaw, 1.0)
    {
        Ok(op) => {
            let name = std::path::Path::new(&asset)
                .file_stem()
                .and_then(|stem| stem.to_str())
                .unwrap_or("stamp");
            match viber::scene::live::apply_coalesced(world, &format!("stamp {name}"), &[op], key) {
                Ok(_) => {
                    world.resource_mut::<crate::scene::EditStatus>().0 =
                        format!("stamp {name} @ {:.0} {:.0}", at.x, at.z);
                }
                Err(error) => {
                    world.resource_mut::<crate::scene::EditStatus>().0 =
                        format!("stamp falhou: {error}");
                }
            }
        }
        Err(error) => {
            world.resource_mut::<crate::scene::EditStatus>().0 = format!("stamp: {error}");
        }
    }
    if buttons.just_released(MouseButton::Left) {
        leave_stroke(world);
    }
}

fn leave_stroke(world: &mut World) {
    let mut stroke = world.resource_mut::<StampStroke>();
    stroke.key = None;
    stroke.last = None;
}

/// Anel de pré-visualização na pose do carimbo (como o do pincel de terreno).
fn draw_stamp_cursor(
    tool: Res<GizmoTool>,
    asset: Res<ActiveStamp>,
    cursor: Res<ViewportCursor>,
    runtime: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    mut gizmos: Gizmos,
) {
    if tool.mode != Mode::Stamp || asset.0.is_none() {
        return;
    }
    let Some(ray) = cursor.ray else {
        return;
    };
    let Some(runtime) = runtime else {
        return;
    };
    let Some(at) = viber::scene::terrain::ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
        runtime.sample(x, z)
    }) else {
        return;
    };
    let color = Srgba::rgb(0.95, 0.75, 0.35);
    let ring: Vec<Vec3> = (0..=RING_SEGMENTS)
        .map(|i| {
            let angle = i as f32 / RING_SEGMENTS as f32 * std::f32::consts::TAU;
            let p = Vec2::new(at.x, at.z) + Vec2::from_angle(angle) * RING_RADIUS;
            Vec3::new(p.x, runtime.sample(p.x, p.y) + 0.15, p.y)
        })
        .collect();
    gizmos.linestrip(ring, color);
    gizmos.line(at, at + Vec3::Y, color);
}

const RING_SEGMENTS: usize = 48;
const RING_RADIUS: f32 = 1.2;

/// S entra no Stamp (com asset ativo); Esc sai.
pub fn stamp_keys(world: &mut World) {
    let (press_s, press_esc) = {
        let keys = world.resource::<ButtonInput<KeyCode>>();
        (
            keys.just_pressed(KeyCode::KeyS),
            keys.just_pressed(KeyCode::Escape),
        )
    };
    let typing = world
        .get_resource::<bevy::input_focus::InputFocus>()
        .and_then(|focus| focus.get())
        .is_some_and(|entity| world.get::<bevy::text::EditableText>(entity).is_some());
    if typing {
        return;
    }
    if press_s && world.resource::<ActiveStamp>().0.is_some() {
        world.resource_mut::<GizmoTool>().mode = Mode::Stamp;
    }
    if world.resource::<GizmoTool>().mode == Mode::Stamp && press_esc {
        leave_stamp(world);
    }
}
