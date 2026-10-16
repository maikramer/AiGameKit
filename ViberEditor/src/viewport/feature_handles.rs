//! Feature point handles (Fase 2B): the selected node's `at`/`path` points drawn on the
//! terrain — the Lake/Pad centre, the River/Road polylines and a `<RoadNetwork>`'s `<Way>`
//! points (its `<Segment>`s drawn between them) — DRAGGABLE in Select mode.
//! The release writes ONE `set` op (undoable, canonical numbers), the same edit an agent
//! makes with `viber scene set`.

use bevy::prelude::*;
use viber::scene::doc::{NodeRef, SceneDoc};
use viber::scene::pose::format_num;
use viber::scene::terrain::ray_ground;

use super::ViewportHover;
use super::gizmo::{GizmoTool, Mode, ViewportCursor};
use crate::scene::{PendingEdits, Selection};
use viber::editor_camera::EditorCamera;

/// Grab radius in screen pixels.
const GRAB_PX: f32 = 14.0;
const HANDLE_R: f32 = 0.9;

/// The selected feature, parsed for handles: the attribute that stores the
/// points, where each point sits in the world (terrain height), and how it
/// draws (closed circle, rect, or open polyline).
#[derive(Resource, Debug, Default, Clone)]
pub struct FeaturePoints {
    pub node: Option<NodeRef>,
    /// `at` (single point) or `path` (pairs) — the attr a drag rewrites.
    attr: String,
    /// World positions (XZ from the attr, Y from the terrain).
    points: Vec<Vec3>,
    /// How the outline draws — and whether index 0 also moves a `radius`/`size`.
    kind: FeatureKind,
    /// RoadNetwork: each handle is a `<Way>` CHILD (its `xz` is what a drag rewrites).
    ways: Vec<NodeRef>,
    /// RoadNetwork: the `<Segment>` polylines (a → via… → b), for the outline.
    segments: Vec<Vec<Vec3>>,
}

#[derive(Debug, Default, Clone, Copy, PartialEq)]
enum FeatureKind {
    /// Single `at` + `radius` circle (Lake, Cut).
    #[default]
    Circle,
    /// `at` + `size` rect (TerrainPad, Plateau).
    Rect,
    /// Open `path` polyline (River, Road).
    Path,
    /// `<RoadNetwork>`: `<Way xz>` children as handles, `<Segment>`s drawn between them.
    Network,
}

/// The drag in progress: which handle, and the moving preview.
#[derive(Resource, Debug, Default, Clone)]
pub struct FeatureDrag {
    grabbed: bool,
    index: usize,
    moved: Option<Vec2>,
    start: Vec<String>,
}

/// True while a handle drag owns the pointer (the transform gizmo/pick stands down).
#[derive(Resource, Debug, Default)]
pub struct FeatureGrab;

pub struct FeatureHandlesPlugin;

impl Plugin for FeatureHandlesPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<FeaturePoints>()
            .init_resource::<FeatureDrag>()
            .init_resource::<FeatureGrab>()
            .add_systems(
                Update,
                (build_feature_points, draw_features, feature_interaction)
                    .chain()
                    .after(super::gizmo::GizmoSet),
            );
    }
}

/// (Re)builds [`FeaturePoints`] when the selection changes.
fn build_feature_points(
    selection: Res<Selection>,
    doc: Res<SceneDoc>,
    runtime: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    mut features: ResMut<FeaturePoints>,
) {
    if !selection.is_changed() && !doc.is_changed() && features.node.is_some() {
        return;
    }
    *features = FeaturePoints::default();
    let Some(node) = selection.primary().cloned() else {
        return;
    };
    let Some(xml) = doc.node(&node) else {
        return;
    };
    let tag = xml.tag.to_ascii_lowercase();
    let ground = |x: f32, z: f32| -> f32 {
        runtime
            .as_deref()
            .map(|runtime| runtime.sample(x, z))
            .unwrap_or(0.0)
    };
    let point = |value: &str| -> Option<Vec3> {
        let [x, z] = viber::xml::values::parse_vec2(value, "feature").ok()?;
        Some(Vec3::new(x, ground(x, z), z))
    };
    if tag == "roadnetwork" {
        // os <Way id xz> são os pontos; os <Segment a b via> ligam-nos
        let mut ways = Vec::new();
        let mut points = Vec::new();
        let mut by_id = std::collections::HashMap::new();
        for (index, child) in xml.children.iter().enumerate() {
            if !child.tag.eq_ignore_ascii_case("way") {
                continue;
            }
            let Some(at) = child.attr("xz").and_then(point) else {
                continue;
            };
            if let Some(id) = child.attr("id") {
                by_id.insert(id.to_owned(), at);
            }
            ways.push(node.child(index));
            points.push(at);
        }
        let segments = xml
            .children
            .iter()
            .filter(|child| child.tag.eq_ignore_ascii_case("segment"))
            .filter_map(|segment| {
                let a = *by_id.get(segment.attr("a")?)?;
                let b = *by_id.get(segment.attr("b")?)?;
                let via: Vec<f32> = segment
                    .attr("via")
                    .unwrap_or_default()
                    .split_whitespace()
                    .filter_map(|token| token.parse().ok())
                    .collect();
                let mut line = vec![a];
                line.extend(
                    via.as_chunks::<2>()
                        .0
                        .iter()
                        .map(|pair| Vec3::new(pair[0], ground(pair[0], pair[1]), pair[1])),
                );
                line.push(b);
                Some(line)
            })
            .collect();
        if points.is_empty() {
            return;
        }
        *features = FeaturePoints {
            node: Some(node),
            attr: "xz".to_owned(),
            points,
            kind: FeatureKind::Network,
            ways,
            segments,
        };
        return;
    }
    let (attr, points, kind) = if let Some(at) = xml.attr("at") {
        let Some(center) = point(at) else {
            return;
        };
        match tag.as_str() {
            "lake" | "cut" => ("at", vec![center], FeatureKind::Circle),
            "terrainpad" | "plateau" => ("at", vec![center], FeatureKind::Rect),
            _ => return,
        }
    } else if let Some(path) = xml.attr("path") {
        let values: Vec<f32> = path
            .split_whitespace()
            .filter_map(|token| token.parse::<f32>().ok())
            .collect();
        let points = values
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| Vec3::new(pair[0], ground(pair[0], pair[1]), pair[1]))
            .collect::<Vec<_>>();
        if points.len() < 2 {
            return;
        }
        ("path", points, FeatureKind::Path)
    } else {
        return;
    };
    *features = FeaturePoints {
        node: Some(node),
        attr: attr.to_owned(),
        points,
        kind,
        ..default()
    };
}

/// Draws the outline and the handles (the dragged one highlighted).
fn draw_features(
    tool: Res<GizmoTool>,
    features: Res<FeaturePoints>,
    doc: Res<SceneDoc>,
    drag: Res<FeatureDrag>,
    runtime: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    mut gizmos: Gizmos,
) {
    if tool.mode != Mode::Select || features.node.is_none() {
        return;
    }
    let ground = |x: f32, z: f32| {
        runtime
            .as_deref()
            .map(|runtime| runtime.sample(x, z))
            .unwrap_or(0.0)
    };
    let color = Srgba::rgb(0.45, 0.85, 1.0);
    let active = Srgba::rgb(1.0, 0.75, 0.3);
    // size/radius extra (Circle/Rect) para desenhar o contorno
    let extra = features
        .node
        .as_ref()
        .and_then(|node| doc.node(node))
        .and_then(|xml| {
            xml.attr("radius")
                .and_then(|r| r.trim().parse::<f32>().ok())
                .map(|r| (Vec2::splat(r), true))
                .or_else(|| {
                    xml.attr("size")
                        .and_then(|s| viber::xml::values::parse_vec2(s, "feature").ok())
                        .map(|s: [f32; 2]| (Vec2::new(s[0], s[1]), false))
                })
        });
    let center = features.points.first().copied();
    // PREVIEW do arrasto: o ponto agarrado segue o cursor (o contorno Path e
    // o handle acompanham; sem isto o drag era às cegas até ao release)
    let points = match (drag.grabbed, drag.moved, features.points.get(drag.index)) {
        (true, Some(moved), Some(_)) => {
            let mut preview = features.points.clone();
            preview[drag.index] = Vec3::new(moved.x, ground(moved.x, moved.y), moved.y);
            preview
        }
        _ => features.points.clone(),
    };
    let center = if drag.grabbed && drag.index == 0 {
        points.first().copied()
    } else {
        center
    };
    let outline: Vec<Vec3> = match (features.kind, extra, center) {
        (FeatureKind::Circle, Some((radius, true)), Some(center)) => (0..=48)
            .map(|i| {
                let angle = i as f32 / 48.0 * std::f32::consts::TAU;
                let p = Vec2::new(center.x, center.z) + Vec2::from_angle(angle) * radius.x;
                Vec3::new(p.x, ground(p.x, p.y) + 0.1, p.y)
            })
            .collect(),
        (FeatureKind::Rect, Some((size, _)), Some(center)) => {
            let half = size / 2.0;
            let corners = [
                Vec2::new(-half.x, -half.y),
                Vec2::new(half.x, -half.y),
                Vec2::new(half.x, half.y),
                Vec2::new(-half.x, half.y),
                Vec2::new(-half.x, -half.y),
            ];
            corners
                .iter()
                .map(|corner| {
                    let p = Vec2::new(center.x, center.z) + *corner;
                    Vec3::new(p.x, ground(p.x, p.y) + 0.1, p.y)
                })
                .collect()
        }
        (FeatureKind::Path, _, _) => points.clone(),
        _ => Vec::new(),
    };
    if !outline.is_empty() {
        gizmos.linestrip(outline, color);
    }
    for segment in &features.segments {
        gizmos.linestrip(segment.iter().map(|p| *p + Vec3::Y * 0.15), color);
    }
    for (index, point) in points.iter().enumerate() {
        let grabbed = drag.grabbed && drag.index == index;
        let handle_color = if grabbed { active } else { color };
        gizmos.circle(Isometry3d::from_translation(*point), HANDLE_R, handle_color);
    }
}

/// Grab (press near a handle), drag (cursor ray → terrain XZ), commit (ONE
/// `set` op). While grabbed, [`FeatureGrab`] tells the transform gizmo/pick to
/// stand down.
fn feature_interaction(world: &mut World) {
    let mode = world.resource::<GizmoTool>().mode;
    let has_feature = world.resource::<FeaturePoints>().node.is_some();
    if mode != Mode::Select || !has_feature {
        world.resource_mut::<FeatureDrag>().grabbed = false;
        world.remove_resource::<FeatureGrab>();
        return;
    }
    let cursor = *world.resource::<ViewportCursor>();
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let keys = world.resource::<ButtonInput<KeyCode>>().clone();
    let hover = world.resource::<ViewportHover>().0;
    let mut drag: FeatureDrag = world.resource::<FeatureDrag>().clone();

    // Esc cancela o arrasto: descarta o preview (o contrato do gizmo)
    if drag.grabbed && keys.just_pressed(KeyCode::Escape) {
        drag.grabbed = false;
        drag.moved = None;
        world.remove_resource::<FeatureGrab>();
        *world.resource_mut::<FeatureDrag>() = drag;
        return;
    }
    if buttons.just_pressed(MouseButton::Left)
        && hover
        && let Some(index) = hovered_handle(world, &cursor)
    {
        drag.grabbed = true;
        drag.index = index;
        drag.moved = None;
        drag.start = current_attr(world);
        world.insert_resource(FeatureGrab);
    }
    if drag.grabbed
        && buttons.pressed(MouseButton::Left)
        && let Some(ray) = cursor.ray
        && let Some(runtime) = world.get_resource::<viber::terrain::runtime::TerrainRuntime>()
        && let Some(at) = ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
            runtime.sample(x, z)
        })
    {
        drag.moved = Some(Vec2::new(at.x, at.z));
    }
    if drag.grabbed
        && (buttons.just_released(MouseButton::Left) || !buttons.pressed(MouseButton::Left))
    {
        drag.grabbed = false;
        world.remove_resource::<FeatureGrab>();
        if let Some(moved) = drag.moved {
            commit(world, drag.index, moved, &drag.start);
        }
        drag.moved = None;
    }
    *world.resource_mut::<FeatureDrag>() = drag;
}

/// The handle nearest the cursor (screen-space, within [`GRAB_PX`]).
fn hovered_handle(world: &World, cursor: &ViewportCursor) -> Option<usize> {
    let screen = cursor.screen?;
    let features = world.resource::<FeaturePoints>();
    let mut cameras =
        world.try_query_filtered::<(&Camera, &GlobalTransform), With<EditorCamera>>()?;
    let (camera, camera_global) = cameras.single(world).ok()?;
    let mut best: Option<(usize, f32)> = None;
    for (index, point) in features.points.iter().enumerate() {
        let Ok(projected) = camera.world_to_viewport(camera_global, *point) else {
            continue;
        };
        let distance = projected.distance(screen);
        if distance < GRAB_PX && best.is_none_or(|(_, d)| distance < d) {
            best = Some((index, distance));
        }
    }
    best.map(|(index, _)| index)
}

/// The attr's current tokens (so a rewrite keeps every other point untouched).
fn current_attr(world: &World) -> Vec<String> {
    let features = world.resource::<FeaturePoints>();
    let Some(node) = features.node.as_ref() else {
        return Vec::new();
    };
    let value = world
        .resource::<SceneDoc>()
        .node(node)
        .and_then(|xml| xml.attr(&features.attr))
        .unwrap_or_default();
    value.split_whitespace().map(str::to_owned).collect()
}

/// Writes the moved point back into the attr tokens and queues the `set`.
fn commit(world: &mut World, index: usize, moved: Vec2, start: &[String]) {
    let features = world.resource::<FeaturePoints>().clone();
    let Some(node) = features.node.clone() else {
        return;
    };
    if features.kind == FeatureKind::Network {
        let Some(way) = features.ways.get(index) else {
            return;
        };
        let selector = world.resource::<SceneDoc>().selector(way);
        let value = format!("{} {}", format_num(moved.x), format_num(moved.y));
        world.resource_mut::<PendingEdits>().push(
            "xz Way".to_owned(),
            vec![viber::scene::ops::EditOp::Set {
                node: selector,
                attrs: [("xz".to_owned(), Some(value))].into(),
            }],
        );
        return;
    }
    let mut tokens = start.to_vec();
    let needed = (index + 1) * 2;
    while tokens.len() < needed {
        tokens.push("0".to_owned());
    }
    tokens[index * 2] = format_num(moved.x);
    tokens[index * 2 + 1] = format_num(moved.y);
    let value = tokens.join(" ");
    let selector = world.resource::<SceneDoc>().selector(&node);
    let label = format!("{} {}", features.attr, node_tag(world, &node));
    world.resource_mut::<PendingEdits>().push(
        label,
        vec![viber::scene::ops::EditOp::Set {
            node: selector,
            attrs: [(features.attr.clone(), Some(value))].into(),
        }],
    );
}

fn node_tag(world: &World, node: &NodeRef) -> String {
    world
        .resource::<SceneDoc>()
        .node(node)
        .map(|xml| xml.tag.clone())
        .unwrap_or_default()
}
