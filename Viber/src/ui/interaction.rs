//! `Interaction` / `Button` da engine — o modelo de foco do `bevy_ui` 0.19.
//!
//! O Bevy 0.20 tornou `bevy::ui::Interaction` e `bevy::ui::Button` aliases
//! deprecados de tipos **privados** (`DeprecatedInteraction`,
//! `DeprecatedButton`): deixaram de poder aparecer numa query de fora do
//! crate. O substituto oficial (`picking::hover::Hovered` + `ui::Pressed` do
//! `bevy_ui_widgets`) tem outra semântica — press/release por observers de
//! picking, sem o estado `Pressed` que dura enquanto o botão está em baixo —
//! e toda a UI declarativa (cliques, sliders, checkboxes, modais, a
//! `UiClicks` do bridge) foi escrita contra a de sempre.
//!
//! Este módulo é o [`ui_focus_system`] do Bevy 0.19 portado tal e qual, sobre
//! componentes nossos. Lê exactamente os mesmos inputs (rato, toques,
//! cursor da janela) e a mesma `UiStack`, portanto os cliques sintéticos do
//! bridge continuam a funcionar sem mudança. Quando a UI migrar para o
//! picking, é este ficheiro que sai.

use bevy::camera::visibility::InheritedVisibility;
use bevy::camera::{Camera, NormalizedRenderTarget, RenderTarget};
use bevy::ecs::entity::{ContainsEntity, EntityHashMap};
use bevy::ecs::query::QueryData;
use bevy::input::InputSystems;
use bevy::input::touch::Touches;
use bevy::prelude::*;
use bevy::ui::{
    CalculatedClip, ComputedNode, ComputedUiTargetCamera, FocusPolicy, UiGlobalTransform, UiStack,
    UiSystems,
};
use bevy::window::PrimaryWindow;

/// Tipo de interação do ponteiro com um nó de UI (semântica do Bevy 0.19).
///
/// Actualizado por [`ui_focus_system`] no `PreUpdate`. Um nó invisível
/// (`InheritedVisibility` falsa) fica sempre em [`Interaction::None`].
#[derive(Component, Copy, Clone, Eq, PartialEq, Debug, Default, Reflect)]
#[reflect(Component, Default, PartialEq, Debug, Clone)]
pub enum Interaction {
    /// O botão principal foi premido sobre o nó e ainda não foi solto.
    Pressed,
    /// O ponteiro está por cima do nó.
    Hovered,
    /// Nada.
    #[default]
    None,
}

/// Marcador de botão: nó que captura o foco e recebe [`Interaction`].
#[derive(Component, Debug, Default, Clone, Copy, PartialEq, Eq, Reflect)]
#[reflect(Component, Default, Debug, PartialEq, Clone)]
#[require(Node, FocusPolicy::Block, Interaction)]
pub struct Button;

/// Regista o [`ui_focus_system`]. Idempotente (HUD e UI declarativa pedem-no
/// ambos).
pub struct UiInteractionPlugin;

impl Plugin for UiInteractionPlugin {
    fn build(&self, app: &mut App) {
        app.register_type::<Interaction>()
            .register_type::<Button>()
            .add_systems(
                PreUpdate,
                ui_focus_system.in_set(UiSystems::Focus).after(InputSystems),
            );
    }
}

/// Garante o [`UiInteractionPlugin`] uma só vez.
pub fn ensure_plugin(app: &mut App) {
    if !app.is_plugin_added::<UiInteractionPlugin>() {
        app.add_plugins(UiInteractionPlugin);
    }
}

/// Nós premidos e soltos no mesmo frame — voltam a `None` no seguinte.
#[derive(Default)]
pub struct FocusState {
    entities_to_reset: Vec<Entity>,
}

#[derive(QueryData)]
#[query_data(mutable)]
pub struct NodeQuery {
    entity: Entity,
    node: &'static ComputedNode,
    transform: &'static UiGlobalTransform,
    interaction: Option<&'static mut Interaction>,
    focus_policy: Option<&'static FocusPolicy>,
    inherited_visibility: Option<&'static InheritedVisibility>,
    target_camera: &'static ComputedUiTargetCamera,
    calculated_clip: Option<&'static CalculatedClip>,
}

/// Port do `bevy::ui::ui_focus_system` 0.19 (sem o `RelativeCursorPosition`,
/// que o sistema do Bevy continua a manter).
#[allow(clippy::too_many_arguments)]
pub fn ui_focus_system(
    mut hovered_nodes: Local<Vec<Entity>>,
    mut state: Local<FocusState>,
    camera_query: Query<(Entity, &Camera, &RenderTarget)>,
    primary_window: Query<Entity, With<PrimaryWindow>>,
    windows: Query<&Window>,
    mouse_button_input: Res<ButtonInput<MouseButton>>,
    touches_input: Res<Touches>,
    ui_stack: Res<UiStack>,
    mut node_query: Query<NodeQuery>,
) {
    let primary_window = primary_window.iter().next();

    // Premidos e soltos no frame anterior.
    for entity in state.entities_to_reset.drain(..) {
        if let Ok(NodeQueryItem {
            interaction: Some(mut interaction),
            ..
        }) = node_query.get_mut(entity)
        {
            *interaction = Interaction::None;
        }
    }

    let mouse_released =
        mouse_button_input.just_released(MouseButton::Left) || touches_input.any_just_released();
    if mouse_released {
        for node in &mut node_query {
            if let Some(mut interaction) = node.interaction
                && *interaction == Interaction::Pressed
            {
                *interaction = Interaction::None;
            }
        }
    }

    let mouse_clicked =
        mouse_button_input.just_pressed(MouseButton::Left) || touches_input.any_just_pressed();

    let camera_cursor_positions: EntityHashMap<Vec2> = camera_query
        .iter()
        .filter_map(|(entity, camera, render_target)| {
            // Só câmaras que desenham numa janela.
            let Some(NormalizedRenderTarget::Window(window_ref)) =
                render_target.normalize(primary_window)
            else {
                return None;
            };
            let window = windows.get(window_ref.entity()).ok()?;
            let viewport_position = camera
                .physical_viewport_rect()
                .map(|rect| rect.min.as_vec2())
                .unwrap_or_default();
            window
                .physical_cursor_position()
                .or_else(|| {
                    touches_input
                        .first_pressed_position()
                        .map(|pos| pos * window.scale_factor())
                })
                .map(|cursor_position| (entity, cursor_position - viewport_position))
        })
        .collect();

    // Nós sob o cursor, do topo para o fundo; os que deixaram de o estar
    // voltam a `None`.
    hovered_nodes.clear();
    for uinodes in ui_stack
        .partition
        .iter()
        .rev()
        .map(|range| &ui_stack.uinodes[range.clone()])
    {
        let Ok(root_node) = node_query.get_mut(uinodes[0]) else {
            continue;
        };
        let Some(camera_entity) = root_node.target_camera.get() else {
            continue;
        };
        let cursor_position = camera_cursor_positions.get(&camera_entity);

        for entity in uinodes.iter().rev().cloned() {
            let Ok(node) = node_query.get_mut(entity) else {
                continue;
            };
            let Some(inherited_visibility) = node.inherited_visibility else {
                continue;
            };
            // Nó não desenhado não é interagível.
            if !inherited_visibility.get() {
                if let Some(mut interaction) = node.interaction {
                    interaction.set_if_neq(Interaction::None);
                }
                continue;
            }
            let contains_cursor = cursor_position.is_some_and(|point| {
                node.node.contains_point(*node.transform, *point)
                    && node
                        .calculated_clip
                        .is_none_or(|clip| clip.contains_point(*point))
            });
            if contains_cursor {
                hovered_nodes.push(entity);
            } else {
                let normalized = cursor_position
                    .and_then(|point| node.node.normalize_point(*node.transform, *point));
                if let Some(mut interaction) = node.interaction
                    && (*interaction == Interaction::Hovered || normalized.is_none())
                {
                    interaction.set_if_neq(Interaction::None);
                }
            }
        }
    }

    // Pressed/Hovered nos nós do topo; um `FocusPolicy::Block` captura.
    let mut hovered_nodes = hovered_nodes.iter();
    let mut iter = node_query.iter_many_mut(hovered_nodes.by_ref()).matched();
    while let Some(node) = iter.fetch_next() {
        if let Some(mut interaction) = node.interaction {
            if mouse_clicked {
                if *interaction != Interaction::Pressed {
                    *interaction = Interaction::Pressed;
                    if mouse_released {
                        state.entities_to_reset.push(node.entity);
                    }
                }
            } else if *interaction == Interaction::None {
                *interaction = Interaction::Hovered;
            }
        }
        match node.focus_policy.unwrap_or(&FocusPolicy::Block) {
            FocusPolicy::Block => break,
            FocusPolicy::Pass => {}
        }
    }
    // O resto (abaixo do bloqueio) volta a `None`, excepto os premidos.
    let mut iter = node_query.iter_many_mut(hovered_nodes).matched();
    while let Some(node) = iter.fetch_next() {
        if let Some(mut interaction) = node.interaction
            && *interaction != Interaction::Pressed
        {
            interaction.set_if_neq(Interaction::None);
        }
    }
}
