//! The "Add node" dialog (Fase 3): the [`viber::scene::catalog`] templates as a
//! clickable list — the SAME snippets `viber scene insert` would apply. Inserted
//! under the current selection (or the world root when nothing is selected).

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::catalog::{self, TemplateGroup};
use viber::scene::doc::SceneDoc;

use crate::scene::{PendingEdits, Selection};
use crate::ui::icons::{self, icon_caption};
use crate::ui::strings::{t, Lang, StrKey};

/// Whether the dialog is open (toggled by the Scene dock's `+`).
#[derive(Resource, Debug, Default)]
pub struct AddNodeOpen(pub bool);

/// The dialog root (spawned by the layout, shown/hidden here).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct AddNodePanel;

/// The dialog's scrollable list body.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct AddNodeBody;

/// A template row. O snippet é o do catálogo (duas linhas podem partilhar a
/// tag `entity` — o contentor vazio e o volume físico).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct TemplateButton {
    pub tag: &'static str,
    pub snippet: &'static str,
}

/// The Scene dock's `+` button.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct AddNodeButton;

/// Toggles the dialog (Scene dock's `+`).
pub fn open_add_node(
    activate: On<Activate>,
    buttons: Query<(), With<AddNodeButton>>,
    mut open: ResMut<AddNodeOpen>,
) {
    if buttons.contains(activate.event_target()) {
        open.0 = !open.0;
    }
}

pub struct AddNodeDialogPlugin;

impl Plugin for AddNodeDialogPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<AddNodeOpen>()
            .add_systems(Update, (toggle_add_node, render_add_node).chain());
    }
}

/// Esc closes the dialog.
fn toggle_add_node(
    keys: Res<ButtonInput<KeyCode>>,
    focus: Res<bevy::input_focus::InputFocus>,
    mut open: ResMut<AddNodeOpen>,
) {
    if open.0 && keys.just_pressed(KeyCode::Escape) && focus.get().is_none() {
        open.0 = false;
    }
}

/// Shows/hides the panel and rebuilds its rows while open.
fn render_add_node(
    mut commands: Commands,
    open: Res<AddNodeOpen>,
    doc: Res<SceneDoc>,
    selection: Res<Selection>,
    lang: Res<Lang>,
    mut panels: Query<&mut Node, With<AddNodePanel>>,
    bodies: Query<Entity, With<AddNodeBody>>,
) {
    // Só o `display` muda: o painel é o catcher absoluto + o cartão + o corpo
    // (antes um `insert(Node)` apagava o layout e o `despawn_related` levava o
    // cartão e o corpo — o diálogo nunca mais abria).
    let Ok(mut panel) = panels.single_mut() else {
        return;
    };
    let display = if open.0 { Display::Flex } else { Display::None };
    if panel.display != display {
        panel.display = display;
    }
    if !open.0 {
        return;
    }
    if !open.is_changed() && !lang.is_changed() && !selection.is_changed() {
        return;
    }
    let Ok(body) = bodies.single() else {
        return;
    };
    let _ = doc;
    commands.entity(body).despawn_related::<Children>();
    let mut last_group: Option<TemplateGroup> = None;
    for template in catalog::templates() {
        if last_group != Some(template.group) {
            last_group = Some(template.group);
            commands
                .spawn((
                    Text::new(group_label(template.group, *lang)),
                    TextFont::from_font_size(11.0),
                    TextColor(icons::ICON_DIM),
                    Node {
                        margin: UiRect::new(px(4), px(0), px(8), px(2)),
                        flex_shrink: 0.0,
                        ..default()
                    },
                    ChildOf(body),
                ))
                .insert(Pickable::IGNORE);
        }
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(
                            icons::tag_icon(template.tag),
                            template.label,
                            icons::tag_color(template.tag)
                        )
                    },
                }
                Node { justify_content: JustifyContent::FlexStart, flex_shrink: 0.0 }
            })
            .insert((
                TemplateButton {
                    tag: template.tag,
                    snippet: template.snippet,
                },
                ChildOf(body),
            ))
            .observe(insert_template);
    }
}

fn insert_template(
    activate: On<Activate>,
    buttons: Query<&TemplateButton>,
    selection: Res<Selection>,
    doc: Res<SceneDoc>,
    lang: Res<crate::ui::strings::Lang>,
    mut open: ResMut<AddNodeOpen>,
    mut commands: Commands,
) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let tag = button.tag;
    let snippet = button.snippet;
    let parent = match selection.primary().cloned() {
        // inserir DENTRO da seleção (um contentor) ou no raiz do mundo
        Some(node) => doc.selector(&node),
        None => doc.selector(&viber::scene::doc::NodeRef::root(0)),
    };
    let op = viber::scene::ops::EditOp::Insert {
        parent,
        index: None,
        xml: snippet.to_string(),
    };
    let label = format!("+ {}", crate::ui::strings::tag_display(*lang, tag));
    open.0 = false;
    commands.queue(move |world: &mut World| {
        world
            .resource_mut::<PendingEdits>()
            .push_selecting_inserted(label, vec![op]);
    });
}

pub fn group_label(group: TemplateGroup, lang: Lang) -> String {
    let key = match group {
        TemplateGroup::Content => StrKey::GroupContent,
        TemplateGroup::Shape => StrKey::GroupShape,
        TemplateGroup::Light => StrKey::GroupLight,
        TemplateGroup::Water => StrKey::GroupWater,
        TemplateGroup::Feature => StrKey::GroupFeature,
    };
    t(lang, key).to_string()
}
