//! The terrain brush settings panel (F4): radius + strength SLIDERS and the
//! material-layer dropdown floating top-right of the viewport while the brush
//! (B) is active — the keys (`[`/`]`, `-`/`=`, `,`/`.`) keep working and stay
//! in sync with the panel.

use bevy::feathers::controls::{FeathersSelect, FeathersSlider};
use bevy::feathers::controls::{OptionIndex, list_rows_from_strings};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui_widgets::{SliderValue, ValueChange};

use super::gizmo::{GizmoTool, Mode};
use super::terrain_tool::TerrainBrush;

/// The panel root.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushPanel;

/// The radius slider.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushRadiusSlider;

/// The strength slider.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushStrengthSlider;

/// The material-layer dropdown (paint mode only).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushLayerSelect;

/// The layer label span (rewritten on change).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushLayerLabel;

pub struct BrushPanelPlugin;

impl Plugin for BrushPanelPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Update,
            (
                show_brush_panel,
                sync_brush_panel,
                // sliders: o valor entra SEMPRE (o arrasto é contínuo)
            ),
        )
        .add_observer(
            |event: On<ValueChange<f32>>,
             radius: Query<(), With<BrushRadiusSlider>>,
             strength: Query<(), With<BrushStrengthSlider>>,
             mut brush: ResMut<TerrainBrush>| {
                if radius.contains(event.source) {
                    brush.radius = event.value.clamp(1.0, 96.0);
                } else if strength.contains(event.source) {
                    brush.amount = event.value.max(0.02);
                }
            },
        )
        .add_observer(
            |event: On<ValueChange<Entity>>,
             selects: Query<(), With<BrushLayerSelect>>,
             rows: Query<&OptionIndex>,
             mut brush: ResMut<TerrainBrush>| {
                if selects.get(event.source).is_err() {
                    return;
                }
                if let Ok(index) = rows.get(event.value) {
                    brush.paint = Some(index.0);
                }
            },
        );
    }
}

/// The panel, as a scene (child of the viewport container, top-right).
pub fn brush_panel() -> impl Scene {
    bsn! {
            BrushPanel
            Node {
                position_type: PositionType::Absolute,
                right: px(10),
                top: px(10),
                flex_direction: FlexDirection::Column,
                row_gap: px(6),
                padding: px(8),
                border: UiRect::all(px(1)),
                border_radius: px(6),
                display: Display::None,
            }
            BackgroundColor(Color::srgba(0.07, 0.08, 0.1, 0.92))
            BorderColor::all(Color::srgba(0.3, 0.33, 0.38, 0.9))
            Pickable::IGNORE
            Children [
                // raio
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                }
                Children [
                    @caption("raio") Pickable::IGNORE
                    --
                    @FeathersSlider {
                        @min: 1.0f32,
                        @max: 96.0f32,
                    }
                    Node { width: px(120) }
    BrushRadiusSlider
                ]
                --
                // força
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                }
                Children [
                    @caption("força") Pickable::IGNORE
                    --
                    @FeathersSlider {
                        @min: 0.05f32,
                        @max: 4.0f32,
                    }
                    Node { width: px(120) }
    BrushStrengthSlider
                ]
                --
                // camada do material (visível só no modo pintura)
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                }
                Children [ BrushLayerRow ]
            ]
        }
}

/// The layer row (built by [`sync_brush_panel`] so the dropdown list exists).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct BrushLayerRow;

/// Mostra o painel apenas com o pincel ativo.
fn show_brush_panel(tool: Res<GizmoTool>, mut panels: Query<&mut Node, With<BrushPanel>>) {
    let Ok(mut panel) = panels.single_mut() else {
        return;
    };
    let wanted = if tool.mode == Mode::Terrain {
        Display::Flex
    } else {
        Display::None
    };
    if panel.display != wanted {
        panel.display = wanted;
    }
}

/// Mantém sliders/etiqueta em sincronia com o `TerrainBrush` (as teclas
/// também mudam o pincel) e constrói o dropdown de camadas sob demanda.
fn sync_brush_panel(
    brush: Res<TerrainBrush>,
    mut commands: Commands,
    sliders: Query<(Entity, &SliderValue)>,
    radius: Query<(), With<BrushRadiusSlider>>,
    strength: Query<(), With<BrushStrengthSlider>>,
    rows: Query<Entity, With<BrushLayerRow>>,
) {
    for (entity, value) in sliders {
        let wanted = if radius.contains(entity) {
            brush.radius
        } else if strength.contains(entity) {
            brush.amount.min(4.0)
        } else {
            continue;
        };
        if (value.0 - wanted).abs() > 1e-3 {
            commands.entity(entity).insert(SliderValue(wanted));
        }
    }
    let Ok(row) = rows.single() else {
        return;
    };
    if brush.is_changed() {
        commands.entity(row).despawn_related::<Children>();
        let layers = viber::terrain::splat::DEFAULT_LAYERS;
        let current = brush.paint;
        let label = current
            .and_then(|slot| layers.get(slot))
            .copied()
            .unwrap_or("—");
        commands
            .spawn((
                Text::new(format!("camada: {label}")),
                TextFont::from_font_size(11.0),
                TextColor(Color::srgb(0.62, 0.66, 0.72)),
                Pickable::IGNORE,
                ChildOf(row),
            ))
            .insert(BrushLayerLabel);
        let options = list_rows_from_strings(layers.iter().copied(), current);
        commands
            .spawn_scene(bsn! {
                @FeathersSelect {
                    @options: {options},
                    @max_visible: 8usize,
                }
                Node { width: px(120) }
            })
            .insert((BrushLayerSelect, ChildOf(row)));
    }
}
