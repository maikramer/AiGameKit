//! View options (Fase F): a "Vista" popover on the toolbar — grid on/off,
//! colliders wireframe on/off (pushed to the RUNNING game via
//! `viber.profiler.extra_toggle {"id":"colliders"}`; the Play launcher sets
//! `VIBER_PHYSICS_DEBUG=1` so the debug render plugin exists), perspective /
//! orthographic projection and the ortho views (front/top/right; Numpad
//! 1/3/7, 5 = persp). Ortho views write `EditorCamState` directly (top uses
//! pitch 89.9 — the orbit clamp is 85) and swap the camera `Projection`,
//! scaling the ortho frustum by the orbit distance.

use bevy::camera::{OrthographicProjection, Projection};
use bevy::feathers::controls::{ButtonVariant, FeathersCheckbox, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui::Checked;
use bevy::ui_widgets::Activate;
use viber::editor_camera::{EditorCamState, EditorCamera};

use super::strings::{Lang, StrKey, t};
use super::theme;
use crate::bridge::{EngineStatus, ViberBridge, methods};

/// The view toggles.
#[derive(Resource, Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct ViewOptions {
    pub grid: bool,
    pub colliders: bool,
    pub ortho: bool,
}

/// Which camera view is active (persp + the three ortho planes).
#[derive(Resource, Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum CameraView {
    #[default]
    Perspective,
    Front,
    Top,
    Right,
}

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct GridToggle;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CollidersToggle;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct PerspButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct FrontButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct TopButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct RightButton;

/// The popover panel (spawned next to the toolbar; shown/hidden here).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ViewPanel;

/// The toolbar's "Vista" button.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ViewOptionsButton;

pub struct ViewOptionsPlugin;

impl Plugin for ViewOptionsPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<ViewOptions>()
            .init_resource::<CameraView>()
            .add_observer(on_view_toggle)
            .add_observer(on_view_button)
            .add_systems(
                Update,
                (
                    apply_startup_views,
                    render_view_panel,
                    apply_projection,
                    apply_grid,
                    ortho_view_keys,
                ),
            );
    }
}

/// The popover, as a scene (positioned by [`toggle_panel`]).
pub fn view_panel() -> impl Scene {
    bsn! {
        ViewPanel
        Node {
            position_type: PositionType::Absolute,
            display: Display::None,
            flex_direction: FlexDirection::Column,
            row_gap: px(6),
            padding: px(10),
            border: UiRect::all(px(1)),
            border_radius: px(6),
            width: px(190),
        }
        BackgroundColor(theme::BG_POPOVER)
        BorderColor::all(theme::BORDER_STRONG)
        Children [
            @caption(t(Lang::default(), StrKey::ViewTitle)) Pickable::IGNORE
            --
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
            }
            Children [
                @FeathersCheckbox
                GridToggle
                Node { min_width: px(0), flex_grow: 1.0 }
                @caption(t(Lang::default(), StrKey::ViewGrid))
            ]
            --
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
            }
            Children [
                @FeathersCheckbox
                CollidersToggle
                Node { min_width: px(0), flex_grow: 1.0 }
                @caption(t(Lang::default(), StrKey::ViewColliders))
            ]
            --
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(4),
            }
            Children [
                @persp_button()
                --
                @front_button()
                --
                @top_button()
                --
                @right_button()
            ]
        ]
    }
}

macro_rules! view_button_fn {
    ($fn_name:ident, $marker:ident, $key:ident) => {
        fn $fn_name() -> impl Scene {
            bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! { @caption(t(Lang::default(), StrKey::$key)) },
                }
                Node { flex_shrink: 0.0 }
                $marker
            }
        }
    };
}

view_button_fn!(persp_button, PerspButton, ViewPersp);
view_button_fn!(front_button, FrontButton, ViewFront);
view_button_fn!(top_button, TopButton, ViewTop);
view_button_fn!(right_button, RightButton, ViewRight);

/// O observador que o botão da toolbar usa (o painel vive no editor root).
pub fn view_options_clicked(
    event: On<PointerClick>,
    buttons: Query<(), With<ViewOptionsButton>>,
    mut panels: Query<&mut Node, With<ViewPanel>>,
) {
    if event.button != PointerButton::Primary || !buttons.contains(event.event_target()) {
        return;
    }
    if let Ok(mut panel) = panels.single_mut() {
        panel.display = if panel.display == Display::None {
            Display::Flex
        } else {
            Display::None
        };
    }
}

/// Rebuilds the toggle rows from state (also syncs `Checked`).
#[allow(clippy::type_complexity)]
fn render_view_panel(
    mut commands: Commands,
    options: Res<ViewOptions>,
    panels: Query<Entity, With<ViewPanel>>,
    grid: Query<(Entity, Has<Checked>), (With<GridToggle>, Changed<Checked>)>,
    colliders: Query<(Entity, Has<Checked>), (With<CollidersToggle>, Changed<Checked>)>,
) {
    let Ok(panel) = panels.single() else {
        return;
    };
    let _ = panel;
    if !options.is_changed() {
        return;
    }
    for (entity, checked) in &grid {
        let is_on = options.grid;
        if checked != is_on {
            if is_on {
                commands.entity(entity).insert(Checked);
            } else {
                commands.entity(entity).remove::<Checked>();
            }
        }
    }
    for (entity, checked) in &colliders {
        let is_on = options.colliders;
        if checked != is_on {
            if is_on {
                commands.entity(entity).insert(Checked);
            } else {
                commands.entity(entity).remove::<Checked>();
            }
        }
    }
}

/// Toggle → estado; colliders também empurra o comando ao jogo a correr.
fn on_view_toggle(
    event: On<Activate>,
    grid: Query<(), With<GridToggle>>,
    colliders: Query<(), With<CollidersToggle>>,
    checked: Query<(), With<Checked>>,
    bridge: Res<ViberBridge>,
    mut options: ResMut<ViewOptions>,
) {
    let target = event.event_target();
    let is_on = checked.contains(target);
    if grid.contains(target) {
        options.grid = is_on;
    } else if colliders.contains(target) {
        options.colliders = is_on;
        if matches!(bridge.status, EngineStatus::Connected { .. }) {
            methods::colliders_toggle(&bridge.client, is_on).detach();
        }
    }
}

/// View button → camera view + state write + projection swap.
fn on_view_button(
    event: On<Activate>,
    persp: Query<(), With<PerspButton>>,
    front: Query<(), With<FrontButton>>,
    top: Query<(), With<TopButton>>,
    right: Query<(), With<RightButton>>,
    mut view: ResMut<CameraView>,
    mut cam: ResMut<EditorCamState>,
) {
    let target = event.event_target();
    *view = if persp.contains(target) {
        CameraView::Perspective
    } else if front.contains(target) {
        CameraView::Front
    } else if top.contains(target) {
        CameraView::Top
    } else if right.contains(target) {
        CameraView::Right
    } else {
        return;
    };
    match *view {
        CameraView::Perspective => cam.pitch_deg = cam.pitch_deg.clamp(15.0, 85.0),
        CameraView::Front => {
            cam.yaw_deg = 0.0;
            cam.pitch_deg = 15.0;
        }
        CameraView::Right => {
            cam.yaw_deg = 90.0;
            cam.pitch_deg = 15.0;
        }
        CameraView::Top => {
            // 89.9 (o orbit clampe a 85; escrever o estado direto contorna)
            cam.pitch_deg = 89.9;
            cam.yaw_deg = 0.0;
        }
    }
}

/// Aplica `--views grid,colliders,ortho` e `top` uma vez no arranque.
pub fn apply_startup_views(
    config: Res<crate::app::EditorConfig>,
    mut ran: Local<bool>,
    mut options: ResMut<ViewOptions>,
    mut view: ResMut<CameraView>,
    mut cam: ResMut<EditorCamState>,
) {
    if *ran {
        return;
    }
    *ran = true;
    let Some(views) = config.startup_views.as_deref() else {
        return;
    };
    for token in views.split(',') {
        match token.trim().to_ascii_lowercase().as_str() {
            "grid" => options.grid = true,
            "colliders" => options.colliders = true,
            "ortho" => options.ortho = true,
            "top" => {
                *view = CameraView::Top;
                cam.pitch_deg = 89.9;
                cam.yaw_deg = 0.0;
            }
            "front" => {
                *view = CameraView::Front;
                cam.pitch_deg = 15.0;
                cam.yaw_deg = 0.0;
            }
            "right" => {
                *view = CameraView::Right;
                cam.pitch_deg = 15.0;
                cam.yaw_deg = 90.0;
            }
            _ => {}
        }
    }
}

/// Numpad 1/3/7/5 → vistas (iguais aos botões).
fn ortho_view_keys(
    keys: Res<ButtonInput<KeyCode>>,
    focus: Option<Res<bevy::input_focus::InputFocus>>,
    typing: Query<(), With<bevy::text::EditableText>>,
    mut view: ResMut<CameraView>,
    mut cam: ResMut<EditorCamState>,
) {
    if focus
        .and_then(|f| f.get())
        .is_some_and(|e| typing.contains(e))
    {
        return;
    }
    let pressed = |code| keys.just_pressed(code);
    let next = if pressed(KeyCode::Numpad1) {
        Some(CameraView::Front)
    } else if pressed(KeyCode::Numpad3) {
        Some(CameraView::Right)
    } else if pressed(KeyCode::Numpad7) {
        Some(CameraView::Top)
    } else if pressed(KeyCode::Numpad5) {
        Some(CameraView::Perspective)
    } else {
        None
    };
    if let Some(next) = next
        && *view != next
    {
        // reusa o fluxo dos botões: escreve o estado diretamente
        *view = next;
        match next {
            CameraView::Perspective => cam.pitch_deg = cam.pitch_deg.clamp(15.0, 85.0),
            CameraView::Front => {
                cam.yaw_deg = 0.0;
                cam.pitch_deg = 15.0;
            }
            CameraView::Right => {
                cam.yaw_deg = 90.0;
                cam.pitch_deg = 15.0;
            }
            CameraView::Top => {
                cam.pitch_deg = 89.9;
                cam.yaw_deg = 0.0;
            }
        }
    }
}

/// Troca a projeção da câmara quando a vista muda; em orto a "distância" vira
/// escala do frustum.
fn apply_projection(
    view: Res<CameraView>,
    options: Res<ViewOptions>,
    mut cameras: Query<&mut Projection, With<EditorCamera>>,
    cam: Res<EditorCamState>,
) {
    if !view.is_changed() && !options.is_changed() {
        return;
    }
    for mut projection in &mut cameras {
        if options.ortho && matches!(view.as_ref(), CameraView::Perspective) {
            let mut ortho = OrthographicProjection::default_3d();
            ortho.scale = (cam.distance / 30.0).max(0.2);
            *projection = Projection::Orthographic(ortho);
            continue;
        }
        match *view {
            CameraView::Perspective => {
                *projection = Projection::Perspective(Default::default());
            }
            _ => {
                let mut ortho = OrthographicProjection::default_3d();
                let scale = (cam.distance / 30.0).max(0.2);
                ortho.scale = scale;
                ortho.far *= 10.0;
                *projection = Projection::Orthographic(ortho);
            }
        }
    }
}

/// O grid de chão, por frame (o grupo/layers do gizmo isolam no viewport).
fn apply_grid(options: Res<ViewOptions>, cam: Res<EditorCamState>, mut gizmos: Gizmos) {
    if !options.grid {
        return;
    }
    let step = 10.0f32;
    gizmos.grid_3d(
        Vec3::new(cam.target.x, 0.05, cam.target.z),
        UVec3::new(40, 1, 40),
        Vec3::new(step, 0.0, step),
        Color::srgba(0.5, 0.6, 0.75, 0.22),
    );
}
