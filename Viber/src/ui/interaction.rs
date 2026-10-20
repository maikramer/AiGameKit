//! `Interaction` / `Button` da engine — o modelo de interação de sempre,
//! alimentado pelo pipeline NATIVO de picking do Bevy 0.20.
//!
//! O Bevy 0.20 tornou `bevy::ui::Interaction`/`Button` aliases deprecados de
//! tipos privados (`DeprecatedInteraction`/`DeprecatedButton`) e o substituto
//! oficial (`picking::hover::Hovered` + os widgets de `bevy_ui_widgets`) tem
//! outra semântica — press/release por observers, sem o estado `Pressed` que
//! dura enquanto o botão está em baixo. Toda a UI declarativa (cliques,
//! sliders, checkboxes, modais, a `UiClicks` do bridge) continua escrita
//! contra a de sempre, por isso este módulo MANTÉM os componentes e troca
//! apenas o motor: o hit-testing é o do `bevy_ui::picking_backend` (a mesma
//! travessia UiStack/clip/visibilidade que o port do 0.19 replicava à mão)
//! e [`interaction_from_picking`] traduz a cadeia `PointerHits` — já truncada
//! pelo backend no 1.º bloqueador — para o estado `Interaction` que os
//! consumidores leram sempre.
//!
//! O blocking vive em `Pickable` (o backend ignora `FocusPolicy`):
//! `runtime::ui_pickable` traduz a política de sempre — `Block` → sem
//! `Pickable` (ausência bloqueia por default), `Pass` → `Pickable` com
//! `should_block_lower: false`.

use bevy::ecs::message::MessageReader;
use bevy::input::touch::Touches;
use bevy::picking::{PickingSystems, backend::PointerHits, pointer::PointerId};
use bevy::prelude::*;
use bevy::ui::{FocusPolicy, UiSystems};

/// Tipo de interação do ponteiro com um nó de UI.
///
/// Actualizado por [`interaction_from_picking`] no `PreUpdate`. Um nó
/// invisível nunca interage (o backend de picking nem o devolve).
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

/// Regista [`interaction_from_picking`]. Idempotente (HUD e UI declarativa
/// pedem-no ambos).
pub struct UiInteractionPlugin;

impl Plugin for UiInteractionPlugin {
    fn build(&self, app: &mut App) {
        app.register_type::<Interaction>()
            .register_type::<Button>()
            .add_systems(
                PreUpdate,
                // O MESMO set do sistema portado que isto substituiu: os
                // injetores de input sintético do bridge correm antes dele.
                interaction_from_picking
                    .in_set(UiSystems::Focus)
                    .after(PickingSystems::Backend),
            );
    }
}

/// Garante o [`UiInteractionPlugin`] uma só vez.
pub fn ensure_plugin(app: &mut App) {
    if !app.is_plugin_added::<UiInteractionPlugin>() {
        app.add_plugins(UiInteractionPlugin);
    }
}

/// Traduz a cadeia `PointerHits` do frame para o estado `Interaction` de
/// sempre. Mesmo contrato do port 0.19 que isto substituiu:
///
/// * o rato tem prioridade; sem rato sob o cursor, o 1.º toque premido;
/// * entre grupos para o mesmo pointer, ganha o `order` mais alto (a UI
///   escreve `camera.order + 0.5` e vive acima do mesh picking);
/// * `Pressed` dura enquanto o botão estiver em baixo, MESMO se o cursor
///   sair do nó; o clique é o frame em que o nó entra em `Pressed` (é isso
///   que o `collect_ui_clicks` publica para os scripts);
/// * premido+solto no mesmo frame continua a marcar UM clique — o reset vai
///   no frame seguinte (é o contrato que o release adiado do bridge assume);
/// * nós fora da cadeia voltam a `None` sem marcar `Changed` à toa
///   (`set_if_neq` — o cascade de estilos lê `Changed<Interaction>`).
pub fn interaction_from_picking(
    mut to_reset: Local<Vec<Entity>>,
    mut hits: MessageReader<PointerHits>,
    mouse: Res<ButtonInput<MouseButton>>,
    touches: Res<Touches>,
    mut interactions: Query<(Entity, &mut Interaction)>,
) {
    // Premidos e soltos no frame anterior.
    for entity in to_reset.drain(..) {
        if let Ok((_, mut interaction)) = interactions.get_mut(entity) {
            interaction.set_if_neq(Interaction::None);
        }
    }

    let clicked = mouse.just_pressed(MouseButton::Left) || touches.any_just_pressed();
    let released = mouse.just_released(MouseButton::Left) || touches.any_just_released();
    if released {
        for (_, mut interaction) in &mut interactions {
            if *interaction == Interaction::Pressed {
                interaction.set_if_neq(Interaction::None);
            }
        }
    }

    // A cadeia do pointer ativo. O backend de UI já truncou no 1.º bloqueador
    // (a caminhada topmost→baixo com `Pickable`), por isso o que aqui chega é
    // exatamente a cadeia de hover de sempre — decoarações `Pass` incluídas.
    let mut mouse_chain: Option<(f32, Vec<Entity>)> = None;
    let mut touch_chain: Option<(f32, Vec<Entity>)> = None;
    for hit in hits.read() {
        let slot = match hit.pointer {
            PointerId::Mouse => &mut mouse_chain,
            PointerId::Touch(_) if touches.first_pressed_position().is_some() => &mut touch_chain,
            _ => continue,
        };
        let replaces = match slot {
            None => true,
            Some((order, _)) => hit.order > *order,
        };
        if replaces {
            *slot = Some((
                hit.order,
                hit.picks.iter().map(|(entity, _)| *entity).collect(),
            ));
        }
    }
    let chain = mouse_chain
        .map(|(_, chain)| chain)
        .or_else(|| touch_chain.map(|(_, chain)| chain))
        .unwrap_or_default();

    for (entity, mut interaction) in &mut interactions {
        if *interaction == Interaction::Pressed {
            // Premido persiste enquanto o botão está em baixo.
            continue;
        }
        if !chain.contains(&entity) {
            if *interaction == Interaction::Hovered {
                interaction.set_if_neq(Interaction::None);
            }
            continue;
        }
        if clicked {
            if *interaction != Interaction::Pressed {
                interaction.set_if_neq(Interaction::Pressed);
                if released {
                    to_reset.push(entity);
                }
            }
        } else {
            interaction.set_if_neq(Interaction::Hovered);
        }
    }
}
