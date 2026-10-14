//! The terrain brush (B): paints the pre-generated heightmap with the engine's `terrain_brush`
//! operation — the same one agents send through `viber terrain brush` / `viber.scene.apply`.
//! A stroke's dabs coalesce into ONE undoable transaction; the live terrain re-meshes as you
//! paint (engine sync), and Ctrl+S writes the `.ahgt`.
//!
//! Keys while the tool is active (pointer over the viewport): 1 raise · 2 lower · 3 smooth ·
//! 4 flatten · 5 crater · 6 paint material (`,` / `.` cycle the pool layer) · 7 noise · `[` / `]`
//! radius · `-` / `=` strength. Flatten holds the height under the cursor when the stroke
//! starts; painting dabs are `terrain_paint` ops (the agent/CLI one).

use bevy::input_focus::InputFocus;
use bevy::prelude::*;
use std::time::Duration;
use viber::scene::ops::EditOp;
use viber::scene::terrain::{BrushMode, ray_ground};
use viber::terrain::runtime::TerrainRuntime;

use super::ViewportHover;
use super::gizmo::{GizmoTool, Mode, ViewportCursor};
use crate::scene::EditStatus;

/// Time between dabs while dragging.
const DAB_INTERVAL: Duration = Duration::from_millis(50);
const RADIUS_RANGE: (f32, f32) = (1.0, 96.0);
const RING_SEGMENTS: usize = 64;

/// Brush settings (persist between strokes).
#[derive(Resource, Debug, Clone, Copy)]
pub struct TerrainBrush {
    pub mode: BrushMode,
    pub radius: f32,
    /// Metres per dab for raise/lower/crater; 0..1 strength for smooth/flatten.
    pub amount: f32,
    /// `Some(slot)` = material PAINT mode (key 6): the dabs paint this pool
    /// layer instead of sculpting heights.
    pub paint: Option<usize>,
}

impl Default for TerrainBrush {
    fn default() -> Self {
        Self {
            mode: BrushMode::Raise,
            radius: 8.0,
            amount: 0.4,
            paint: None,
        }
    }
}

impl TerrainBrush {
    /// A one-line description for the toolbar, in the editor's language.
    pub fn describe(&self, lang: crate::ui::strings::Lang) -> String {
        use crate::ui::strings::Lang::{En, Pt};
        let mode = match self.mode {
            BrushMode::Raise => match lang {
                En => "raise",
                Pt => "subir",
            },
            BrushMode::Lower => match lang {
                En => "lower",
                Pt => "descer",
            },
            BrushMode::Smooth => match lang {
                En => "smooth",
                Pt => "suavizar",
            },
            BrushMode::Flatten => match lang {
                En => "flatten",
                Pt => "nivelar",
            },
            BrushMode::Crater => match lang {
                En => "crater",
                Pt => "cratera",
            },
            BrushMode::Noise => match lang {
                En => "noise",
                Pt => "ruído",
            },
        };
        let prefix = match lang {
            En => "B terrain",
            Pt => "B terreno",
        };
        if let Some(slot) = self.paint {
            let layer = viber::terrain::splat::DEFAULT_LAYERS
                .get(slot)
                .copied()
                .unwrap_or("?");
            return format!(
                "{prefix} paint {layer} r={:.1} a={:.2}",
                self.radius, self.amount
            );
        }
        format!("{prefix} {mode} r={:.1} a={:.2}", self.radius, self.amount)
    }
}

/// The stroke in progress.
#[derive(Resource, Debug, Default)]
pub struct Stroke {
    /// Coalescing key of the current stroke (`None` = not painting).
    key: Option<u64>,
    next_key: u64,
    since_dab: Duration,
    /// Flatten target, captured at the start of the stroke.
    flatten_height: Option<f32>,
    /// Where the cursor ray meets the terrain this frame.
    hit: Option<Vec3>,
}

pub struct TerrainToolPlugin;

impl Plugin for TerrainToolPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<TerrainBrush>()
            .init_resource::<Stroke>()
            .add_systems(
                Update,
                (brush_keys, paint, draw_cursor)
                    .chain()
                    .after(super::gizmo::GizmoSet),
            );
    }
}

fn brush_keys(
    keys: Res<ButtonInput<KeyCode>>,
    buttons: Res<ButtonInput<MouseButton>>,
    focus: Option<Res<InputFocus>>,
    editable: Query<(), With<bevy::text::EditableText>>,
    hover: Res<ViewportHover>,
    mut tool: ResMut<GizmoTool>,
    mut brush: ResMut<TerrainBrush>,
) {
    let typing = focus
        .and_then(|f| f.get())
        .is_some_and(|entity| editable.contains(entity));
    if typing || buttons.pressed(MouseButton::Right) || !hover.0 {
        return;
    }
    if keys.just_pressed(KeyCode::KeyB) {
        tool.mode = Mode::Terrain;
    }
    if tool.mode != Mode::Terrain {
        return;
    }
    for (key, mode, amount) in [
        (KeyCode::Digit1, BrushMode::Raise, 0.4),
        (KeyCode::Digit2, BrushMode::Lower, 0.4),
        (KeyCode::Digit3, BrushMode::Smooth, 0.5),
        (KeyCode::Digit4, BrushMode::Flatten, 0.5),
        (KeyCode::Digit5, BrushMode::Crater, 2.0),
        (KeyCode::Digit7, BrushMode::Noise, 0.6),
    ] {
        if keys.just_pressed(key) {
            brush.mode = mode;
            brush.amount = amount;
            brush.paint = None;
        }
    }
    let layers = viber::terrain::splat::DEFAULT_LAYERS.len();
    if keys.just_pressed(KeyCode::Digit6) {
        // 6 = pintar MATERIAL (a camada mais recente ou a primeira)
        brush.paint = Some(brush.paint.unwrap_or(0));
        brush.amount = 0.6;
    }
    if brush.paint.is_some() {
        let current = brush.paint.unwrap_or(0);
        if keys.just_pressed(KeyCode::Comma) {
            brush.paint = Some((current + layers - 1) % layers);
        }
        if keys.just_pressed(KeyCode::Period) {
            brush.paint = Some((current + 1) % layers);
        }
    }
    if keys.just_pressed(KeyCode::BracketLeft) {
        brush.radius = (brush.radius * 0.8).max(RADIUS_RANGE.0);
    }
    if keys.just_pressed(KeyCode::BracketRight) {
        brush.radius = (brush.radius * 1.25).min(RADIUS_RANGE.1);
    }
    let strength_cap = match brush.mode {
        BrushMode::Smooth | BrushMode::Flatten => 1.0,
        _ => 16.0,
    };
    if keys.just_pressed(KeyCode::Minus) {
        brush.amount = (brush.amount * 0.8).max(0.02);
    }
    if keys.just_pressed(KeyCode::Equal) {
        brush.amount = (brush.amount * 1.25).min(strength_cap);
    }
}

/// Dabs along a left-drag over the viewport; one coalesced transaction per stroke.
fn paint(world: &mut World) {
    if world.resource::<GizmoTool>().mode != Mode::Terrain {
        world.resource_mut::<Stroke>().key = None;
        return;
    }
    let cursor = *world.resource::<ViewportCursor>();
    let hit = cursor.ray.and_then(|ray| {
        let runtime = world.get_resource::<TerrainRuntime>()?;
        ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
            runtime.sample(x, z)
        })
    });
    world.resource_mut::<Stroke>().hit = hit;
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let delta = world.resource::<Time<Real>>().delta();
    let brush = *world.resource::<TerrainBrush>();

    if !buttons.pressed(MouseButton::Left) {
        world.resource_mut::<Stroke>().key = None;
        return;
    }
    let Some(at) = hit else {
        return;
    };
    let starting = buttons.just_pressed(MouseButton::Left);
    if starting && !world.resource::<ViewportHover>().0 {
        return;
    }
    let key = {
        let mut stroke = world.resource_mut::<Stroke>();
        if starting || stroke.key.is_none() {
            stroke.next_key += 1;
            stroke.key = Some(stroke.next_key);
            stroke.since_dab = DAB_INTERVAL;
            stroke.flatten_height = Some(at.y);
        }
        stroke.since_dab += delta;
        if stroke.since_dab < DAB_INTERVAL {
            return;
        }
        stroke.since_dab = Duration::ZERO;
        stroke.key.expect("set above")
    };
    let op = if let Some(slot) = brush.paint {
        let layer = viber::terrain::splat::DEFAULT_LAYERS
            .get(slot)
            .copied()
            .unwrap_or("grass");
        EditOp::TerrainPaint {
            at: [at.x, at.z],
            radius: brush.radius,
            layer: layer.to_owned(),
            amount: brush.amount.clamp(0.0, 1.0),
        }
    } else {
        let height = (brush.mode == BrushMode::Flatten)
            .then(|| world.resource::<Stroke>().flatten_height)
            .flatten();
        EditOp::TerrainBrush {
            mode: brush.mode,
            at: [at.x, at.z],
            radius: brush.radius,
            amount: brush.amount,
            height,
        }
    };
    let lang = *world.resource::<crate::ui::strings::Lang>();
    let status = match viber::scene::live::apply_coalesced(world, &brush.describe(lang), &[op], key)
    {
        Ok(_) => brush.describe(lang),
        Err(error) => crate::ui::strings::terrain_failed(lang, &error),
    };
    world.resource_mut::<EditStatus>().0 = status;
}

/// The brush ring, laid on the terrain under the cursor.
fn draw_cursor(
    tool: Res<GizmoTool>,
    brush: Res<TerrainBrush>,
    stroke: Res<Stroke>,
    runtime: Option<Res<TerrainRuntime>>,
    mut gizmos: Gizmos,
) {
    let (Mode::Terrain, Some(at), Some(runtime)) = (tool.mode, stroke.hit, runtime) else {
        return;
    };
    let color = if brush.paint.is_some() {
        Srgba::rgb(0.75, 0.5, 0.95)
    } else {
        match brush.mode {
            BrushMode::Raise => Srgba::rgb(0.4, 0.9, 0.4),
            BrushMode::Lower | BrushMode::Crater => Srgba::rgb(0.95, 0.45, 0.35),
            BrushMode::Noise => Srgba::rgb(0.7, 0.75, 0.8),
            BrushMode::Smooth => Srgba::rgb(0.45, 0.7, 1.0),
            BrushMode::Flatten => Srgba::rgb(1.0, 0.85, 0.3),
        }
    };
    let ring: Vec<Vec3> = (0..=RING_SEGMENTS)
        .map(|i| {
            let angle = i as f32 / RING_SEGMENTS as f32 * std::f32::consts::TAU;
            let p = Vec2::new(at.x, at.z) + Vec2::from_angle(angle) * brush.radius;
            Vec3::new(p.x, runtime.sample(p.x, p.y) + 0.15, p.y)
        })
        .collect();
    gizmos.linestrip(ring, color);
    gizmos.line(at, at + Vec3::Y * brush.radius * 0.3, color);
}
