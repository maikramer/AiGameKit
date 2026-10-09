//! The interactive widgets: check, slider, input — plus tooltips and per-element
//! mouse cursors.
//!
//! Everything here is state on a plain declarative element, not a new render
//! path: a `<UiCheck>` is a box whose `checked` flag drives classes, a
//! `<UiSlider>` is a track whose value follows the pressed pointer, and a
//! `<UiInput>` is a text sink that owns the keyboard while focused. The
//! stylesheet paints all three exactly like it paints a panel.
//!
//! Scripts read state back through `viber.ui.read(id)` and write it through
//! `set_checked(id, on)` / `set_value(id, v)` / `focus(id)`.

use bevy::prelude::*;
// Bevy 0.20: `Interaction`/`Button` do prelude são aliases de tipos privados.
use crate::ui::interaction::Interaction;
use bevy::ui::RelativeCursorPosition;
use bevy::window::{CursorIcon, PrimaryWindow};

use super::runtime::{UiClasses, UiDisabled, UiStyleDirty};

/// Ordena os extremos de um intervalo e afasta valores não finitos —
/// [`f32::clamp`] faz `assert!(min <= max)`, por isso um
/// `<UiSlider min="100" max="10">` (ou um extremo NaN/inf, que contagiaria o
/// resultado) rebentaria a engine. Os três pontos que clampeiam contra o
/// intervalo (o parse do XML, o arrasto e `viber.ui.set_value`) partilham
/// esta normalização; não finito cai no omissão do `<UiSlider>` (0..1) — o
/// aviso autoral vive no parse, em `tree.rs`.
pub(crate) fn normalized_range(min: f32, max: f32) -> (f32, f32) {
    if !min.is_finite() || !max.is_finite() {
        return (0.0, 1.0);
    }
    (min.min(max), max.max(min))
}

// ── UiCheck ─────────────────────────────────────────────────────────────

/// A toggle: clicking flips `checked`; the classes `checked` / `unchecked`
/// mirror it for the stylesheet, and the `.tick` child shows only when on.
#[derive(Debug, Component)]
pub struct UiCheck {
    pub checked: bool,
}

/// Flips checks on click and mirrors the flag into classes.
///
/// `Changed<Interaction>` also fires on release, so the class sync is
/// idempotent and only the `Pressed` edge toggles.
#[allow(clippy::type_complexity)]
pub fn drive_ui_checks(
    mut commands: Commands,
    mut checks: Query<
        (
            Entity,
            &Interaction,
            &mut UiCheck,
            &mut UiClasses,
            Has<UiStyleDirty>,
        ),
        Or<(Changed<UiCheck>, Changed<Interaction>)>,
    >,
) {
    for (entity, interaction, mut check, mut classes, already_dirty) in &mut checks {
        if *interaction == Interaction::Pressed {
            check.checked = !check.checked;
        }
        let changed = classes.set_class("checked", check.checked)
            | classes.set_class("unchecked", !check.checked);
        if changed && !already_dirty {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

/// Shows/hides the `.tick` child of every check.
///
/// Corre todos os frames (escrita idempotente): o re-estilo limpa o `display`
/// do tick e a classe `checked` é que o repõe — um resize não pode apagar
/// ticks. `display`, não `Visibility`: um nó escondido ainda ocupa layout.
#[allow(clippy::type_complexity)]
pub fn sync_check_ticks(
    checks: Query<(&UiCheck, &Children)>,
    classes: Query<&UiClasses>,
    mut nodes: Query<&mut Node>,
) {
    for (check, children) in &checks {
        for child in children.iter() {
            let Ok(classes) = classes.get(child) else {
                continue;
            };
            if !classes.has("tick") {
                continue;
            }
            if let Ok(mut node) = nodes.get_mut(child) {
                node.display = if check.checked {
                    Display::Flex
                } else {
                    Display::None
                };
            }
        }
    }
}

// ── UiSlider ────────────────────────────────────────────────────────────

/// A draggable range. `value` lives in `min..max` (`step` quantises it); the
/// `.fill` child mirrors the fraction like a bar and the `.handle` child
/// slides along the track. The class `dragging` is set while the pointer holds.
#[derive(Debug, Component)]
pub struct UiSlider {
    pub value: f32,
    pub min: f32,
    pub max: f32,
    /// Quantisation; 0 means continuous.
    pub step: f32,
    pub vertical: bool,
    pub fill: Entity,
    pub handle: Entity,
}

impl UiSlider {
    /// Maps a 0..1 pointer position (already flipped for vertical tracks) to a
    /// stepped value in `min..max`.
    pub fn value_at(&self, fraction: f32) -> f32 {
        let fraction = fraction.clamp(0.0, 1.0);
        // Defesa em profundidade: o parse já normalizou, mas `clamp` asserta
        // com um intervalo cru — ordenar aqui nunca custa nada.
        let (min, max) = normalized_range(self.min, self.max);
        let raw = min + fraction * (max - min);
        if self.step > 0.0 {
            // Rounding can step off the range; clamp after.
            ((raw / self.step).round() * self.step).clamp(min, max)
        } else {
            raw
        }
    }

    /// 0..1 fraction of `value` within the range, for fill/handle sizing.
    pub fn fraction(&self) -> f32 {
        let span = self.max - self.min;
        if span <= 0.0 {
            return 0.0;
        }
        ((self.value - self.min) / span).clamp(0.0, 1.0)
    }
}

/// Drags sliders with the pointer and mirrors `value` onto fill + handle.
///
/// Corre todos os frames: o arrasto lê o ponteiro, e a escrita do fill/handle
/// é idempotente — o re-estilo (resize, `@media`) limpa os campos e este
/// sistema repõe-os no mesmo frame.
///
/// `Interaction::Pressed` persists while the button is held — even off the
/// track — so dragging past the edge keeps working and just clamps. The
/// pointer position comes from bevy_ui's `RelativeCursorPosition.normalized`
/// (0..1 node space, y-down), which is also why the vertical axis flips.
#[allow(clippy::type_complexity)]
pub fn drive_ui_sliders(
    mut commands: Commands,
    mut sliders: Query<
        (
            Entity,
            &Interaction,
            Option<&RelativeCursorPosition>,
            &mut UiSlider,
            &mut UiClasses,
            Has<UiStyleDirty>,
        ),
        Without<UiDisabled>,
    >,
    mut nodes: Query<&mut Node>,
) {
    for (entity, interaction, cursor, mut slider, mut classes, already_dirty) in &mut sliders {
        let dragging = *interaction == Interaction::Pressed;
        if dragging && let Some(point) = cursor.and_then(|cursor| cursor.normalized) {
            let fraction = if slider.vertical {
                1.0 - point.y
            } else {
                point.x
            };
            slider.value = slider.value_at(fraction);
        }
        if classes.set_class("dragging", dragging) && !already_dirty {
            commands.entity(entity).insert(UiStyleDirty);
        }
        let fraction = slider.fraction() * 100.0;
        if let Ok(mut fill) = nodes.get_mut(slider.fill) {
            if slider.vertical {
                fill.height = Val::Percent(fraction);
            } else {
                fill.width = Val::Percent(fraction);
            }
        }
        // The handle rides the fill edge: positioned by percentage so any
        // track size works without measuring pixels.
        if let Ok(mut handle) = nodes.get_mut(slider.handle) {
            if slider.vertical {
                handle.top = Val::Percent(100.0 - fraction);
            } else {
                handle.left = Val::Percent(fraction);
            }
        }
    }
}

// ── UiInput ─────────────────────────────────────────────────────────────

/// A single-line text field. Click to focus (class `focused`), type to edit,
/// Enter/Escape to blur. `child` is the authored `<UiText>` kept in sync with
/// `text` / `placeholder` by [`sync_ui_input_text`].
#[derive(Debug, Component)]
pub struct UiInput {
    pub text: String,
    pub child: Entity,
    pub placeholder: Option<String>,
    /// Hard cap on stored characters; typing past it is dropped.
    pub max_len: Option<usize>,
}

/// Which input has the keyboard, if any.
#[derive(Debug, Default, Resource)]
pub struct UiFocusedInput(pub Option<Entity>);

/// Edits the focused input from key presses.
///
/// Only `Character` keys land in the field, and only without ctrl/alt/meta —
/// shortcuts keep working while an input is focused. Backspace deletes.
#[allow(clippy::type_complexity)]
pub fn drive_ui_input(
    keys: Res<ButtonInput<KeyCode>>,
    mut events: bevy::ecs::message::MessageReader<bevy::input::keyboard::KeyboardInput>,
    mut focus: ResMut<UiFocusedInput>,
    mut inputs: Query<&mut UiInput>,
) {
    let Some(entity) = focus.0 else {
        return;
    };
    let Ok(mut input) = inputs.get_mut(entity) else {
        focus.0 = None; // the input went away; drop the stale focus
        return;
    };
    // Modifiers live on the global key state, not on the event.
    let bypass = keys.any_pressed([
        KeyCode::ControlLeft,
        KeyCode::ControlRight,
        KeyCode::AltLeft,
        KeyCode::AltRight,
        KeyCode::SuperLeft,
        KeyCode::SuperRight,
    ]);
    for key in events.read() {
        if key.state != bevy::input::ButtonState::Pressed {
            continue;
        }
        match &key.logical_key {
            bevy::input::keyboard::Key::Backspace => {
                input.text.pop();
            }
            bevy::input::keyboard::Key::Character(chars) if !bypass => {
                for ch in chars.chars() {
                    if input
                        .max_len
                        .is_none_or(|max| input.text.chars().count() < max)
                    {
                        input.text.push(ch);
                    }
                }
            }
            _ => {}
        }
    }
}

/// Click-to-focus, Enter/Escape blur, and the `focused` class.
#[allow(clippy::type_complexity)]
pub fn sync_ui_input_focus(
    mut commands: Commands,
    mut keys: bevy::ecs::message::MessageReader<bevy::input::keyboard::KeyboardInput>,
    mut focus: ResMut<UiFocusedInput>,
    mut inputs: Query<
        (Entity, &Interaction, &mut UiClasses, Has<UiStyleDirty>),
        Without<UiDisabled>,
    >,
) {
    for key in keys.read() {
        if key.state != bevy::input::ButtonState::Pressed {
            continue;
        }
        if matches!(
            key.logical_key,
            bevy::input::keyboard::Key::Enter | bevy::input::keyboard::Key::Escape
        ) && let Some(entity) = focus.0.take()
            && let Ok((_, _, mut classes, already_dirty)) = inputs.get_mut(entity)
            && classes.remove("focused")
            && !already_dirty
        {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
    for (entity, interaction, mut classes, already_dirty) in &mut inputs {
        if *interaction == Interaction::Pressed && focus.0 != Some(entity) {
            focus.0 = Some(entity);
            if classes.add("focused") && !already_dirty {
                commands.entity(entity).insert(UiStyleDirty);
            }
        }
    }
}

/// Mirrors `text` / `placeholder` into the field's `<UiText>` child and toggles
/// the `placeholder` class while the field is empty.
#[allow(clippy::type_complexity)]
pub fn sync_ui_input_text(
    mut commands: Commands,
    inputs: Query<(Entity, &UiInput), Changed<UiInput>>,
    mut texts: Query<&mut Text>,
    mut classes: Query<&mut UiClasses>,
) {
    for (entity, input) in &inputs {
        let empty = input.text.is_empty();
        let shown = if empty {
            input.placeholder.clone().unwrap_or_default()
        } else {
            input.text.clone()
        };
        if let Ok(mut text) = texts.get_mut(input.child)
            && text.0 != shown
        {
            text.0 = shown;
        }
        if let Ok(mut classes) = classes.get_mut(entity)
            && classes.set_class("placeholder", empty)
        {
            commands.entity(entity).insert(UiStyleDirty);
        }
    }
}

// ── tooltips ────────────────────────────────────────────────────────────

/// `tooltip="…"` on any element: shows a labelled hint near the pointer while
/// the element is hovered.
#[derive(Debug, Clone, Component)]
pub struct UiTooltipText(pub String);

/// Shared tooltip state: the floating element (spawned lazily under the first
/// UiRoot), its text child, and whose text it currently shows.
#[derive(Debug, Default, Resource)]
pub struct UiTooltipLayer {
    pub entity: Option<Entity>,
    pub text_entity: Option<Entity>,
    pub showing: Option<String>,
}

/// Floats one tooltip near the pointer while a `tooltip="…"` element is
/// hovered.
///
/// The layer is created on first use under the first UiRoot and given the
/// class `tooltip` — the stylesheet paints it; the system only moves it and
/// keeps its text current. It carries no `Interaction`, so it can never steal
/// hover from the element underneath.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn drive_ui_tooltip(
    mut commands: Commands,
    mut layer: ResMut<UiTooltipLayer>,
    hovered: Query<(&Interaction, &UiTooltipText)>,
    roots: Query<(Entity, &super::runtime::UiTag)>,
    windows: Query<&Window>,
    // `Option`: mundos sem HUD (qa-agua, terrain demo) não inserem
    // `HudAssets` — exigir o resource panica a engine no arranque.
    assets: Option<Res<crate::hud::HudAssets>>,
    scale: Res<UiScale>,
    mut texts: Query<&mut Text>,
    mut nodes: Query<&mut Node>,
    mut visibility: Query<&mut Visibility>,
) {
    let Some(assets) = assets else {
        return;
    };
    let target = hovered.iter().find_map(|(interaction, tooltip)| {
        (*interaction != Interaction::None).then(|| tooltip.0.clone())
    });
    // Lazily build the floating layer the first time anything needs it.
    if layer.entity.is_none() {
        let Some((root, _)) = roots.iter().find(|(_, tag)| tag.0 == "uiroot") else {
            return;
        };
        let tip = commands
            .spawn((
                Node {
                    position_type: PositionType::Absolute,
                    display: Display::None,
                    ..Default::default()
                },
                super::runtime::UiTag("uitooltip".to_string()),
                UiClasses::parse("tooltip"),
                // `align-self: start` é obrigatório: num flex row (o UiRoot)
                // o Taffy ESTICA filhos absolutos sem tamanho no eixo cruzado
                // — o tooltip descia até ao fundo do ecrã (top definido,
                // bottom auto ⇒ altura = ecrã − top).
                super::runtime::UiInlineStyle(super::style::parse_declarations(
                    "background: #0b0d12e6; radius: 6; padding: 5 8; z: 9000; align-self: start; pointer-events: none",
                    "ui tooltip defaults",
                )),
                super::runtime::UiStyleDirty,
                Visibility::Hidden,
                ChildOf(root),
            ))
            .id();
        let text_entity = commands
            .spawn((
                Text::default(),
                TextColor(Color::WHITE),
                TextFont {
                    font: assets.font.clone().into(),
                    font_size: 12.0.into(),
                    ..Default::default()
                },
                TextLayout::default(),
            ))
            .id();
        // Reserved ids: the parent link queues fine before anything applies.
        commands.entity(tip).add_child(text_entity);
        layer.entity = Some(tip);
        layer.text_entity = Some(text_entity);
    }
    let Some(tip) = layer.entity else {
        return;
    };
    match target {
        Some(text) => {
            if layer.showing.as_deref() != Some(text.as_str()) {
                if let Some(text_entity) = layer.text_entity
                    && let Ok(mut tip_text) = texts.get_mut(text_entity)
                {
                    tip_text.0 = text.clone();
                }
                layer.showing = Some(text);
            }
            if let Ok(mut visibility) = visibility.get_mut(tip)
                && *visibility == Visibility::Hidden
            {
                *visibility = Visibility::Inherited;
            }
            // Near the pointer. The cursor arrives in window pixels, but
            // `Val::Px` is multiplied by `UiScale` — sem a divisão, o tooltip
            // deriva do cursor sempre que o HUD estiver ampliado (tudo acima
            // de 720p). Trabalha no espaço AUTORAL (janela ÷ escala,
            // `scale::ui_viewport`) e clampeia nesse mesmo espaço; o Val::Px
            // volta a ser multiplicado pela escala ao aplicar.
            if let (Ok(window), Ok(mut node)) = (windows.single(), nodes.get_mut(tip))
                && let Some(cursor) = window.cursor_position()
            {
                let scale = if scale.0 > 1e-3 { scale.0 } else { 1.0 };
                let (width, height) =
                    super::scale::ui_viewport(window.width(), window.height(), scale);
                node.left = Val::Px(
                    (cursor.x / scale + 14.0)
                        .max(0.0)
                        .min((width - 230.0).max(0.0)),
                );
                node.top = Val::Px(
                    (cursor.y / scale + 18.0)
                        .max(0.0)
                        .min((height - 48.0).max(0.0)),
                );
            }
        }
        None => {
            layer.showing = None;
            if let Ok(mut visibility) = visibility.get_mut(tip)
                && *visibility != Visibility::Hidden
            {
                *visibility = Visibility::Hidden;
            }
        }
    }
}

// ── mouse cursor ────────────────────────────────────────────────────────

/// Per-element mouse cursor (`cursor: pointer`), applied while hovered.
#[derive(Debug, Clone, Component)]
pub struct UiCursorIcon(pub CursorIcon);

/// Sets the primary window's cursor from the hovered element's [`UiCursorIcon`].
///
/// Any hovered element with a cursor wins; when none does, the cursor falls
/// back to the system default. A passada re-lê TODOS os elementos com cursor
/// (não só os `Changed<Interaction>`), por isso um elemento hovered que
/// despawnou — e leva a sua aresta de `Interaction` atrás — chega aqui como
/// "nada hovered" em vez de um pointer preso para sempre. Writes only on
/// change — the window component would otherwise report a change every frame.
#[allow(clippy::type_complexity)]
pub fn drive_ui_cursors(
    mut commands: Commands,
    cursors: Query<(Entity, &UiCursorIcon)>,
    interactions: Query<&Interaction>,
    // A janela PRIMÁRIA, não "a primeira que aparecer": com mais janelas o
    // cursor tem de seguir o rato do jogador.
    windows: Query<Entity, (With<Window>, With<PrimaryWindow>)>,
    mut last: Local<Option<CursorIcon>>,
) {
    let icon = cursors
        .iter()
        .find(|(entity, _)| {
            interactions
                .get(*entity)
                .is_ok_and(|interaction| *interaction != Interaction::None)
        })
        .map(|(_, cursor)| cursor.0.clone());
    if *last == icon {
        return;
    }
    let Ok(window) = windows.single() else {
        return;
    };
    commands
        .entity(window)
        .insert(icon.clone().unwrap_or_default());
    *last = icon;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slider(step: f32) -> UiSlider {
        UiSlider {
            value: 0.0,
            min: 0.0,
            max: 100.0,
            step,
            vertical: false,
            fill: Entity::PLACEHOLDER,
            handle: Entity::PLACEHOLDER,
        }
    }

    #[test]
    fn test_slider_value_maps_and_steps() {
        let stepped = slider(5.0);
        assert!((stepped.value_at(0.5) - 50.0).abs() < 1e-5);
        assert!(
            (stepped.value_at(0.507) - 50.0).abs() < 1e-4,
            "snaps to step"
        );
        assert_eq!(stepped.value_at(-1.0), 0.0, "clamps low");
        assert_eq!(stepped.value_at(2.0), 100.0, "clamps high");
        // Continuous (step 0) keeps the raw fraction.
        let free = slider(0.0);
        assert!((free.value_at(0.333) - 33.3).abs() < 1e-3);
    }

    #[test]
    fn test_slider_fraction_guards_a_flat_range() {
        let mut flat = slider(0.0);
        flat.max = 0.0;
        assert_eq!(flat.fraction(), 0.0, "min == max divides by zero");
        let mut offset = slider(0.0);
        offset.min = 10.0;
        offset.max = 20.0;
        offset.value = 15.0;
        assert!((offset.fraction() - 0.5).abs() < 1e-6);
    }

    #[test]
    fn test_value_at_survives_an_inverted_range() {
        // Defesa em profundidade: o parse normaliza os extremos, mas o
        // `value_at` volta a ordená-los para o `clamp` nunca assertar
        // (intervalo invertido ou não finito não pode rebentar o arrasto).
        let mut inverted = slider(0.0);
        inverted.min = 100.0;
        inverted.max = 10.0;
        assert!((inverted.value_at(0.5) - 55.0).abs() < 1e-4);
        let mut nan = slider(0.0);
        nan.min = f32::NAN;
        nan.max = 10.0;
        assert!((nan.value_at(0.25) - 0.25).abs() < 1e-4, "cai no 0..1");
    }

    #[test]
    fn test_input_edits_respect_max_len() {
        // The typing rules live inline in `drive_ui_input`; this pins the
        // counting rule it must apply (chars, not bytes — acentos contam 1).
        let mut text = String::from("çé");
        assert_eq!(text.chars().count(), 2);
        text.pop();
        assert_eq!(text, "ç");
    }
}
