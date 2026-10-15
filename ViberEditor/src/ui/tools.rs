//! The Godot-style tool strip floating over the viewport: select / move / rotate / scale,
//! local/global axes and frame-selection — the same actions as the Q/W/E/R/T/F keys,
//! clickable. The active tool stays highlighted.

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::prelude::*;
use bevy::ui_widgets::Activate;

use super::icons::{self, fa, icon};
use super::strings::{Lang, LangTooltip, StrKey, t};
use super::theme;
use super::tooltip::Tooltip;
use crate::viewport::gizmo::{GizmoTool, Mode, SelectedEntity};

/// A gizmo-mode button; `0` is the mode.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ToolButton(pub Mode);

/// The local/global axes toggle button.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SpaceToggle;

/// The icon span of the axes toggle (cube = local, globe = global).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SpaceIcon;

/// The frame-selection button.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct FrameButton;

/// Marks the strip (hidden outside the 3D mode).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ToolStrip;

/// The strip, as a scene (child of the viewport container, above the game image).
pub fn tool_strip() -> impl Scene {
    bsn! {
        ToolStrip
        Node {
            position_type: PositionType::Absolute,
            left: px(10),
            top: px(10),
            flex_direction: FlexDirection::Column,
            row_gap: px(4),
            padding: px(4),
            border: UiRect::all(px(1)),
            border_radius: px(6),
        }
        GlobalZIndex(10)
        BackgroundColor(theme::BG_OVERLAY)
        BorderColor::all(theme::BORDER_STRONG)
        Children [
            @tool_button(Mode::Select, fa::ARROW_POINTER, StrKey::TipSelect)
            --
            @tool_button(Mode::Translate, fa::ARROWS_UP_DOWN_LEFT_RIGHT, StrKey::TipMove)
            --
            @tool_button(Mode::Rotate, fa::ROTATE_RIGHT, StrKey::TipRotate)
            --
            @tool_button(Mode::Scale, fa::UP_RIGHT_AND_DOWN_LEFT_FROM_CENTER, StrKey::TipScale)
            --
            @tool_button(Mode::Terrain, fa::MOUNTAIN, StrKey::TipTerrainBrush)
            --
            @strip_separator()
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! { @icon(fa::GLOBE, 13.0, icons::ICON) SpaceIcon },
            }
            SpaceToggle
            Tooltip(t(Lang::default(), StrKey::TipSpace))
            LangTooltip(StrKey::TipSpace)
            on(space_clicked)
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! { @icon(fa::CROSSHAIRS, 13.0, icons::ICON) },
            }
            FrameButton
            Tooltip(t(Lang::default(), StrKey::TipFrame))
            LangTooltip(StrKey::TipFrame)
            on(frame_clicked)
        ]
    }
}

fn tool_button(mode: Mode, glyph: char, tip: StrKey) -> impl Scene {
    bsn! {
        @FeathersToolButton {
            @variant: ButtonVariant::Plain,
            @caption: bsn! { @icon(glyph, 13.0, icons::ICON) },
        }
        ToolButton(mode)
        Tooltip(t(Lang::default(), tip))
        LangTooltip(tip)
        on(tool_clicked)
    }
}

fn strip_separator() -> impl Scene {
    bsn! {
        Node {
            height: px(1),
            width: percent(100),
            margin: {UiRect::vertical(px(2))},
        }
        BackgroundColor(Color::srgb(0.3, 0.33, 0.38))
        Pickable::IGNORE
    }
}

pub struct ToolStripPlugin;

impl Plugin for ToolStripPlugin {
    fn build(&self, app: &mut App) {
        // tool_clicked/space_clicked/frame_clicked are `on(...)` observers, not systems.
        app.add_systems(Update, refresh_tool_strip);
    }
}

fn tool_clicked(activate: On<Activate>, buttons: Query<&ToolButton>, mut tool: ResMut<GizmoTool>) {
    if let Ok(button) = buttons.get(activate.event_target())
        && tool.mode != button.0
    {
        tool.mode = button.0;
    }
}

fn space_clicked(
    activate: On<Activate>,
    toggles: Query<(), With<SpaceToggle>>,
    mut tool: ResMut<GizmoTool>,
) {
    if toggles.contains(activate.event_target()) {
        tool.local = !tool.local;
    }
}

fn frame_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<FrameButton>>,
    selected: Res<SelectedEntity>,
    globals: Query<&GlobalTransform>,
    mut camera: Option<ResMut<viber::editor_camera::EditorCamState>>,
) {
    if !buttons.contains(activate.event_target()) {
        return;
    }
    if let (Some(entity), Some(camera)) = (selected.0, camera.as_deref_mut())
        && let Ok(global) = globals.get(entity)
    {
        let _ = viber::editor_camera::look_at(camera, global.translation());
        camera.distance = camera.distance.min(60.0);
    }
}

/// Highlights the active tool and flips the axes toggle's icon (cube = local, globe = global).
/// Runs every frame over six entities; writes only on actual changes.
fn refresh_tool_strip(
    tool: Res<GizmoTool>,
    mut buttons: Query<(&ToolButton, &mut ButtonVariant)>,
    mut space: Query<(&SpaceToggle, &mut ButtonVariant), Without<ToolButton>>,
    mut icons: Query<(&SpaceIcon, &mut Text), Without<ToolButton>>,
) {
    for (button, mut variant) in &mut buttons {
        let wanted = if tool.mode == button.0 {
            ButtonVariant::Primary
        } else {
            ButtonVariant::Plain
        };
        if *variant != wanted {
            *variant = wanted;
        }
    }
    for (_, mut variant) in &mut space {
        let wanted = if tool.local {
            ButtonVariant::Normal
        } else {
            ButtonVariant::Plain
        };
        if *variant != wanted {
            *variant = wanted;
        }
    }
    for (_, mut text) in &mut icons {
        let wanted = if tool.local {
            fa::CUBE.to_string()
        } else {
            fa::GLOBE.to_string()
        };
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}
