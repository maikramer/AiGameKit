//! The Inspector dock: the selected node's type, source file and EVERY XML attribute (read
//! from the document, not the ECS — gameplay attributes the engine never reflects are
//! editable too). Attribute names show their friendly label with the raw XML key beside it
//! in small gray (that key is what the XML and the `viber scene` CLI use). Each value is a
//! text field; Enter commits it as a `set` operation (undoable, synced to the live world).
//! The last row takes `key=value` to add an attribute; an empty value removes one.

use bevy::feathers::controls::{
    ButtonVariant, FeathersTextInput, FeathersTextInputContainer, FeathersToolButton,
};
use bevy::input::ButtonState;
use bevy::input::keyboard::KeyboardInput;
use bevy::input_focus::InputFocus;
use bevy::prelude::*;
use bevy::text::{EditableText, TextEdit};
use bevy::ui_widgets::Activate;
use std::collections::BTreeMap;
use viber::scene::ops::EditOp;

use super::doc::{NodeRef, SceneDoc};
use super::{PendingEdits, Selection};
use crate::ui::icons::{self, fa, icon_caption, image_icon};
use crate::ui::strings::{
    Lang, StrKey, attr_display, fact_display, scatter_failed, set_label, t, tag_display,
};

/// The container the inspector rows are spawned into.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct InspectorBody;

/// A value field: which node and attribute it edits (`key: None` = the "add" row).
#[derive(Component, Debug, Clone, Default)]
pub struct AttrInput {
    pub node: NodeRef,
    pub key: Option<String>,
    /// The value it was filled with (commit only when it changed).
    pub initial: String,
    prefilled: bool,
}

/// The "Regenerate" button of a `<Scatter>` recipe.
#[derive(Component, Debug, Clone, Default)]
pub struct ScatterButton(pub NodeRef);

/// The "Settle on terrain" button of a posed node.
#[derive(Component, Debug, Clone, Default)]
pub struct SettleButton(pub NodeRef);

/// Runs the engine's `settle` tool (a group settles its children) as one undoable edit.
fn run_settle(activate: On<Activate>, buttons: Query<&SettleButton>, mut commands: Commands) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let node = button.0.clone();
    commands.queue(move |world: &mut World| {
        let result = world.resource_scope(|_, mut doc: Mut<SceneDoc>| {
            let selector = doc.selector(&node);
            viber::scene::tools::settle(&mut doc, &selector, 0.0)
        });
        match result {
            Ok(output) if output.ops.is_empty() => {
                world.resource_mut::<super::EditStatus>().0 = format!("{}: ok", output.label);
            }
            Ok(output) => world
                .resource_mut::<PendingEdits>()
                .push(output.label, output.ops),
            Err(error) => {
                world.resource_mut::<super::EditStatus>().0 = format!("settle: {error:#}");
            }
        }
    });
}

/// Runs the scatter tool on the recipe and queues its result as one undoable edit.
fn run_scatter(activate: On<Activate>, buttons: Query<&ScatterButton>, mut commands: Commands) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    let node = button.0.clone();
    commands.queue(move |world: &mut World| {
        let selector = world.resource::<SceneDoc>().selector(&node);
        match viber::scene::tools::scatter(world, &selector) {
            Ok(output) => {
                for warning in &output.warnings {
                    warn!("{warning}");
                }
                world
                    .resource_mut::<PendingEdits>()
                    .push(output.label, output.ops);
            }
            Err(error) => {
                let lang = *world.resource::<Lang>();
                world.resource_mut::<super::EditStatus>().0 = scatter_failed(lang, &error);
            }
        }
    });
}

#[derive(Resource, Debug, Default)]
pub struct InspectorView {
    built: bool,
}

/// One line of the inspector.
#[derive(Debug, Clone, PartialEq)]
pub enum Line {
    /// Read-only heading: the node's type tag (drives the icon) and its friendly title.
    Heading { tag: String, title: String },
    /// Read-only fact (`file`, `children`).
    Fact(String, String),
    /// An editable attribute.
    Attr(String, String),
    /// The `key=value` row that adds an attribute.
    Add,
    /// A generator recipe: a button that regenerates its output (`viber::scene::tools`).
    Scatter,
    /// A posed node (or a group of them): a button that settles it on the current terrain.
    Settle,
}

pub fn inspector_lines(doc: &SceneDoc, selection: &Selection, lang: Lang) -> Vec<Line> {
    let Some(node_ref) = selection.primary() else {
        return vec![Line::Heading {
            tag: String::new(),
            title: t(lang, StrKey::InspectNone).into(),
        }];
    };
    if node_ref.is_root() {
        let file = &doc.files[node_ref.file];
        let tag = file.doc.root_tag.clone();
        let title = tag_display(lang, &tag).to_string();
        let mut lines = vec![
            Line::Heading { tag, title },
            Line::Fact("file".into(), doc.relative_path(node_ref.file)),
        ];
        lines.extend(
            file.doc
                .root_attrs
                .iter()
                .map(|(k, v)| Line::Attr(k.clone(), v.clone())),
        );
        lines.push(Line::Add);
        return lines;
    }
    let Some(node) = doc.node(node_ref) else {
        return vec![Line::Heading {
            tag: String::new(),
            title: t(lang, StrKey::InspectGone).into(),
        }];
    };
    // "Objeto — hero": the friendly type, the node's own name when it has one.
    let title = ["name", "id"]
        .iter()
        .find_map(|key| node.attr(key))
        .map(|name| format!("{} — {name}", tag_display(lang, &node.tag)))
        .unwrap_or_else(|| tag_display(lang, &node.tag).to_string());
    let mut lines = vec![
        Line::Heading {
            tag: node.tag.clone(),
            title,
        },
        Line::Fact("file".into(), doc.relative_path(node_ref.file)),
        Line::Fact("selector".into(), doc.selector(node_ref)),
        Line::Fact("children".into(), node.children.len().to_string()),
    ];
    if node.tag.eq_ignore_ascii_case("scatter") {
        lines.push(Line::Scatter);
    }
    if ["group", "entity", "gltfscene", "composition"]
        .iter()
        .any(|tag| node.tag.eq_ignore_ascii_case(tag))
    {
        lines.push(Line::Settle);
    }
    let covered = super::property::covered_keys(&node.tag);
    lines.extend(
        node.attrs
            .iter()
            .filter(|(k, _)| !covered.contains(&k.as_str()))
            .map(|(k, v)| Line::Attr(k.clone(), v.clone())),
    );
    lines.push(Line::Add);
    lines
}

pub fn render_inspector(
    mut commands: Commands,
    doc: Res<SceneDoc>,
    selection: Res<Selection>,
    lang: Res<Lang>,
    open: Res<super::property::SectionOpen>,
    mut view: ResMut<InspectorView>,
    bodies: Query<Entity, With<InspectorBody>>,
) {
    let Ok(body) = bodies.single() else {
        return;
    };
    if view.built && !doc.is_changed() && !selection.is_changed() && !lang.is_changed() {
        return;
    }
    view.built = true;
    commands.entity(body).despawn_related::<Children>();
    if selection.0.is_empty() {
        spawn_empty_state(&mut commands, body, *lang);
        return;
    }
    let node = selection.primary().cloned().unwrap_or_default();
    for line in inspector_lines(&doc, &selection, *lang) {
        let row = commands
            .spawn((
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(8),
                    padding: UiRect::vertical(px(2)),
                    ..default()
                },
                ChildOf(body),
            ))
            .id();
        match line {
            Line::Heading { tag, title } => {
                // Type heading: the tag's icon in its category color, then the friendly name.
                commands.spawn((
                    icons::icon_text(icons::tag_icon(&tag), 13.0, icons::tag_color(&tag)),
                    Node {
                        width: px(18),
                        ..default()
                    },
                    Pickable::IGNORE,
                    ChildOf(row),
                ));
                commands.spawn((
                    text_node(title, 13.0),
                    TextColor(Color::srgb(0.95, 0.85, 0.55)),
                    ChildOf(row),
                ));
            }
            Line::Fact(key, value) => {
                commands.spawn((key_node(fact_display(*lang, &key).into()), ChildOf(row)));
                commands.spawn((text_node(value, 12.0), ChildOf(row)));
            }
            Line::Attr(key, value) => {
                commands.spawn((key_node(attr_display(*lang, &key).into()), ChildOf(row)));
                // The raw XML key in small gray: the name the XML and the CLI actually use.
                if attr_display(*lang, &key) != key {
                    commands.spawn((
                        text_node(key.clone(), 10.0),
                        TextColor(Color::srgb(0.45, 0.48, 0.54)),
                        Node {
                            flex_shrink: 0.0,
                            ..default()
                        },
                        Pickable::IGNORE,
                        ChildOf(row),
                    ));
                }
                spawn_input(&mut commands, row, node.clone(), Some(key), value);
            }
            Line::Add => {
                commands.spawn((
                    icons::icon_text(fa::PLUS, 12.0, icons::ICON_DIM),
                    Node {
                        width: px(18),
                        ..default()
                    },
                    Pickable::IGNORE,
                    ChildOf(row),
                ));
                commands.spawn((key_node(t(*lang, StrKey::AddAttr).into()), ChildOf(row)));
                spawn_input(&mut commands, row, node.clone(), None, String::new());
            }
            Line::Scatter => {
                commands
                    .spawn_scene(bsn! {
                        @FeathersToolButton {
                            @variant: ButtonVariant::Primary,
                            @caption: bsn! {
                                @icon_caption(fa::WAND_MAGIC_SPARKLES, t(*lang, StrKey::BtnRegen), Color::WHITE)
                            },
                        }
                    })
                    .insert((ScatterButton(node.clone()), ChildOf(row)))
                    .observe(run_scatter);
            }
            Line::Settle => {
                commands
                    .spawn_scene(bsn! {
                        @FeathersToolButton {
                            @variant: ButtonVariant::Plain,
                            @caption: bsn! {
                                @icon_caption(fa::MOUNTAIN, t(*lang, StrKey::BtnSettle), Color::WHITE)
                            },
                        }
                    })
                    .insert((SettleButton(node.clone()), ChildOf(row)))
                    .observe(run_settle);
            }
        }
    }
    // As SECÇÕES TIPODAS (Transform/Física/Luz/…) — no fim do painel, por baixo
    // dos attrs genéricos que não lhes pertencem.
    if let Some(node) = selection.primary().cloned() {
        super::property::render_sections(&mut commands, body, &doc, &node, &open, *lang);
    }
}

/// Nothing selected: the text2d-generated pick illustration plus guidance, centered.
fn spawn_empty_state(commands: &mut Commands, body: Entity, lang: Lang) {
    let message = t(lang, StrKey::InspectNone);
    commands
        .spawn_scene(bsn! {
            Node {
                width: percent(100),
                flex_direction: FlexDirection::Column,
                align_items: AlignItems::Center,
                row_gap: px(10),
                padding: {UiRect::vertical(px(16))},
            }
            Pickable::IGNORE
            Children [
                @image_icon(icons::PICK_ICON, 96.0)
                --
                Text(message)
                TextFont { font_size: {FontSize::Px(12.0)} }
                TextColor(icons::ICON_DIM)
                TextLayout { justify: Justify::Center }
            ]
        })
        .insert(ChildOf(body));
}

fn text_node(text: String, size: f32) -> impl Bundle {
    (Text::new(text), TextFont::from_font_size(size))
}

fn key_node(key: String) -> impl Bundle {
    (
        Text::new(key),
        TextFont::from_font_size(12.0),
        TextColor(Color::srgb(0.6, 0.65, 0.72)),
        Node {
            width: px(96),
            flex_shrink: 0.0,
            ..default()
        },
    )
}

fn spawn_input(
    commands: &mut Commands,
    row: Entity,
    node: NodeRef,
    key: Option<String>,
    value: String,
) {
    let container = commands
        .spawn_scene(bsn! { @FeathersTextInputContainer Node { flex_grow: 1.0 } })
        .insert(ChildOf(row))
        .id();
    commands.spawn_scene(bsn! { @FeathersTextInput }).insert((
        AttrInput {
            node,
            key,
            initial: value,
            prefilled: false,
        },
        ChildOf(container),
    ));
}

/// Fills each new field with its current value (the widget starts empty).
pub fn prefill_inputs(mut inputs: Query<(&mut AttrInput, &mut EditableText)>) {
    for (mut input, mut text) in &mut inputs {
        if input.prefilled {
            continue;
        }
        input.prefilled = true;
        if !input.initial.is_empty() {
            text.queue_edit(TextEdit::Insert(input.initial.as_str().into()));
        }
    }
}

/// Enter in a focused field commits it as a `set` (an empty value removes the attribute).
pub fn commit_inputs(
    mut keys: MessageReader<KeyboardInput>,
    focus: Option<Res<InputFocus>>,
    inputs: Query<(&AttrInput, &EditableText)>,
    doc: Res<SceneDoc>,
    lang: Res<Lang>,
    mut pending: ResMut<PendingEdits>,
) {
    let enter = keys
        .read()
        .any(|key| key.key_code == KeyCode::Enter && key.state == ButtonState::Pressed);
    if !enter {
        return;
    }
    let Some(focused) = focus.as_deref().and_then(InputFocus::get) else {
        return;
    };
    let Ok((input, text)) = inputs.get(focused) else {
        return;
    };
    let value = text.value().to_string();
    let Some(attrs) = attr_change(input.key.as_deref(), &input.initial, &value) else {
        return;
    };
    let selector = doc.selector(&input.node);
    let keys: Vec<String> = attrs.keys().cloned().collect();
    pending.push(
        set_label(*lang, &keys, &selector),
        vec![EditOp::Set {
            node: selector,
            attrs,
        }],
    );
}

/// The attribute change a committed field asks for, or `None` when there is nothing to do.
pub fn attr_change(
    key: Option<&str>,
    initial: &str,
    value: &str,
) -> Option<BTreeMap<String, Option<String>>> {
    match key {
        Some(key) => {
            if value == initial {
                return None;
            }
            let value = (!value.trim().is_empty()).then(|| value.to_owned());
            Some(BTreeMap::from([(key.to_owned(), value)]))
        }
        None => {
            let (key, value) = value.split_once('=')?;
            let key = key.trim();
            if key.is_empty() {
                return None;
            }
            Some(BTreeMap::from([(
                key.to_owned(),
                Some(value.trim().to_owned()),
            )]))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(dir: &std::path::Path) -> SceneDoc {
        std::fs::write(
            dir.join("world.xml"),
            r#"<world static="true"><Entity name="hero" translation="0 1 0" /></world>"#,
        )
        .unwrap();
        SceneDoc::load(&dir.join("world.xml")).unwrap()
    }

    #[test]
    fn node_lines_list_facts_then_editable_attributes() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = doc(tmp.path());
        let lines = inspector_lines(&doc, &Selection(vec![NodeRef::new(0, vec![0])]), Lang::Pt);
        assert_eq!(
            lines[0],
            Line::Heading {
                tag: "Entity".into(),
                title: "Objeto — hero".into()
            }
        );
        assert!(lines.contains(&Line::Fact("selector".into(), "world.xml#0".into())));
        // `translation` pertence agora à secção TIPODAS (Transform, com scrub)
        // — o fallback só traz o que nenhuma secção cobre.
        assert!(!lines.contains(&Line::Attr("translation".into(), "0 1 0".into())));
        assert!(crate::scene::property::covered_keys("entity").contains(&"translation"));
        assert_eq!(lines.last(), Some(&Line::Add));
    }

    #[test]
    fn english_headings_use_english_names() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = doc(tmp.path());
        let lines = inspector_lines(&doc, &Selection(vec![NodeRef::new(0, vec![0])]), Lang::En);
        assert_eq!(
            lines[0],
            Line::Heading {
                tag: "Entity".into(),
                title: "Object — hero".into()
            }
        );
    }

    #[test]
    fn root_selection_edits_root_attributes() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = doc(tmp.path());
        let lines = inspector_lines(&doc, &Selection(Some(NodeRef::root(0))), Lang::Pt);
        assert!(lines.contains(&Line::Attr("static".into(), "true".into())));
    }

    #[test]
    fn empty_and_stale_selection_explain_themselves() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = doc(tmp.path());
        assert!(matches!(
            inspector_lines(&doc, &Selection(None), Lang::Pt)[0],
            Line::Heading { .. }
        ));
        let stale = Selection(Some(NodeRef::new(0, vec![3])));
        assert!(matches!(
            &inspector_lines(&doc, &stale, Lang::Pt)[0],
            Line::Heading { title, .. } if title.contains("já não existe")
        ));
    }

    #[test]
    fn committed_fields_become_set_ops() {
        assert_eq!(attr_change(Some("scale"), "1", "1"), None, "unchanged");
        assert_eq!(
            attr_change(Some("scale"), "1", "2"),
            Some(BTreeMap::from([("scale".into(), Some("2".into()))]))
        );
        assert_eq!(
            attr_change(Some("scale"), "1", "  "),
            Some(BTreeMap::from([("scale".into(), None)])),
            "empty removes"
        );
        assert_eq!(
            attr_change(None, "", "tag = always-active"),
            Some(BTreeMap::from([(
                "tag".into(),
                Some("always-active".into())
            )]))
        );
        assert_eq!(attr_change(None, "", "no equals"), None);
        assert_eq!(attr_change(None, "", "=x"), None);
    }
}
