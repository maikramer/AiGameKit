//! The 3D viewport: Viber's editor camera (`viber::editor_camera`, spawned by the engine in
//! `RunMode::Edit`) renders into an image shown by a [`ViewportNode`] in the editor layout.
//!
//! The game's own UI (the world's `<UiRoot>` HUD, menus, loading screen) targets the default UI
//! camera. That role goes to [`GameUiCamera`], kept inactive in the 3D view (like Godot, the
//! runtime HUD is not drawn over the 3D scene) — so the game UI never mixes with the editor's
//! panels, which live on their own camera. The UI editing mode (Phase 4) renders it on demand.
//!
//! Navigation (Godot-style, only while the pointer is over the viewport):
//! * middle drag — orbit; Shift + middle drag — pan; wheel — zoom;
//! * right drag — free look, with WASD / QE flying while held (wheel changes fly speed);
//! * F — frame the selection; Q/W/E/R/T and clicks drive the gizmo ([`gizmo`]).

pub mod brush_panel;
pub mod drop;
pub mod feature_handles;
pub mod gizmo;
pub mod gizmo_math;
pub mod nav;
pub mod terrain_tool;

use bevy::camera::RenderTarget;
use bevy::image::Image;
use bevy::picking::events::{PointerOut, PointerOver};
use bevy::prelude::*;
use bevy::render::render_resource::TextureFormat;
use bevy::ui::IsDefaultUiCamera;
use bevy::ui::widget::ViewportNode;
use viber::editor_camera::EditorCamera;

/// Initial render target size; the [`ViewportNode`] resizes it to the panel every frame.
const INITIAL_SIZE: UVec2 = UVec2::new(1280, 720);

/// The image the editor camera renders into.
#[derive(Resource, Debug, Clone)]
pub struct ViewportImage(pub Handle<Image>);

/// The layout pane that hosts the viewport image node.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ViewportBody;

/// The node displaying the editor camera.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct EditorViewport;

/// Default UI camera for the *game's* UI; inactive while editing the 3D scene (the engine's
/// editor mode deactivates every camera not marked `KeepCameraActive`).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct GameUiCamera;

/// Whether the pointer is over the viewport (navigation input is only taken then).
#[derive(Resource, Debug, Default, Clone, Copy)]
pub struct ViewportHover(pub bool);

pub struct ViewportPlugin;

impl Plugin for ViewportPlugin {
    fn build(&self, app: &mut App) {
        app.add_plugins((
            gizmo::GizmoPlugin,
            terrain_tool::TerrainToolPlugin,
            brush_panel::BrushPanelPlugin,
            drop::DropPlugin,
            feature_handles::FeatureHandlesPlugin,
        ))
        .init_resource::<ViewportHover>()
        .init_resource::<nav::FlyState>()
        .add_systems(PreStartup, (create_viewport_image, spawn_game_ui_camera))
        .add_systems(
            Update,
            (
                attach_editor_camera,
                (nav::orbit_pan_zoom, nav::fly).run_if(viewport_hovered_or_dragging),
            ),
        );
    }
}

fn create_viewport_image(mut commands: Commands, mut images: ResMut<Assets<Image>>) {
    let image = Image::new_target_texture(
        INITIAL_SIZE.x,
        INITIAL_SIZE.y,
        TextureFormat::Bgra8UnormSrgb,
        None,
    );
    commands.insert_resource(ViewportImage(images.add(image)));
}

fn spawn_game_ui_camera(mut commands: Commands) {
    commands.spawn((
        GameUiCamera,
        Name::new("GameUiCamera"),
        Camera2d,
        Camera {
            is_active: false,
            ..default()
        },
        IsDefaultUiCamera,
    ));
}

/// Points the engine's editor camera at the viewport image and shows it in the layout.
fn attach_editor_camera(
    mut commands: Commands,
    image: Res<ViewportImage>,
    cameras: Query<Entity, Added<EditorCamera>>,
    bodies: Query<Entity, With<ViewportBody>>,
) {
    let Ok(body) = bodies.single() else {
        return;
    };
    for camera in &cameras {
        commands.entity(camera).insert((
            RenderTarget::Image(image.0.clone().into()),
            // The world (layer 0) plus the editor-only gizmo layer.
            bevy::camera::visibility::RenderLayers::from_layers(&[0, gizmo::GIZMO_LAYER]),
        ));
        commands
            .spawn((
                EditorViewport,
                ViewportNode::new(camera),
                Node {
                    width: percent(100),
                    height: percent(100),
                    ..default()
                },
                ChildOf(body),
            ))
            .observe(|_: On<PointerOver>, mut hover: ResMut<ViewportHover>| hover.0 = true)
            .observe(|_: On<PointerOut>, mut hover: ResMut<ViewportHover>| hover.0 = false);
    }
}

/// Navigation keeps working when a drag leaves the viewport mid-gesture.
fn viewport_hovered_or_dragging(
    hover: Res<ViewportHover>,
    fly: Res<nav::FlyState>,
    buttons: Res<ButtonInput<MouseButton>>,
) -> bool {
    hover.0
        || (fly.dragging
            && (buttons.pressed(MouseButton::Middle) || buttons.pressed(MouseButton::Right)))
}
