//! Inspector › Script — the Godot `@export` panel. A node with `script=` shows the props
//! its Luau script declares (`--@export name: type = default -- doc`, read by
//! [`viber::luau::exports`], the same contract the runtime, `viber scene schema` and the lint
//! use) as typed fields. A value set here is a plain `prop-<name>` attribute — one undoable
//! `set` op, synced live without respawning the entity (the script keeps its state).
//!
//! Overridden props carry a revert button (back to the script's default = the attribute is
//! removed); `prop-*` attributes the script does not export show in red with a remove button.

use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::luau::exports::{
    ExportDef, PropType, SignalDef, connect_attr, connect_signal, parse_exports, prop_attr,
    prop_name,
};
use viber::scene::ops::EditOp;
use viber::xml::XmlNode;

use super::doc::{NodeRef, SceneDoc};
use super::property::{FieldDef, FieldKind, FieldTarget, SectionId, SectionOpen};
use super::{PendingEdits, Selection};
use crate::ui::icons::{self, fa, icon_caption};
use crate::ui::strings::{Lang, StrKey, t};
use crate::ui::tooltip::Tooltip;

/// Revert / remove button of one attribute (`prop-<name>`, an unknown `connect-*`).
#[derive(Component, Debug, Clone)]
pub struct PropReset(pub String);

/// "Open script" button: the script's path relative to the world folder.
#[derive(Component, Debug, Clone)]
pub struct OpenScriptButton(pub String);

/// The script of a node, resolved and parsed (None when the node has no `script=`).
pub struct NodeScript {
    pub script: String,
    /// Path relative to the world folder (what the Script mode lists).
    pub world_rel: String,
    pub exports: viber::luau::exports::ScriptExports,
    /// Read error (missing file) — the section still shows, with the error.
    pub error: Option<String>,
}

pub fn node_script(doc: &SceneDoc, xml: &XmlNode) -> Option<NodeScript> {
    let script = xml.attr("script")?.trim().to_owned();
    let scripts_dir = viber::scene::scripts_dir(doc);
    let path = viber::lint::resolve_script(&scripts_dir, &script);
    let world_dir = doc
        .world_path()
        .parent()
        .map(std::path::Path::to_path_buf)
        .unwrap_or_default();
    let world_rel = path
        .strip_prefix(&world_dir)
        .unwrap_or(&path)
        .display()
        .to_string();
    let (exports, error) = match std::fs::read_to_string(&path) {
        Ok(source) => (parse_exports(&source), None),
        Err(error) => (
            Default::default(),
            Some(format!("{}: {error}", path.display())),
        ),
    };
    Some(NodeScript {
        script,
        world_rel,
        exports,
        error,
    })
}

/// The typed field of one export.
pub fn field_for(def: &ExportDef, lang: Lang) -> FieldDef {
    let kind = match &def.ty {
        PropType::Number { .. } => FieldKind::Number { step: 0.1 },
        PropType::Int { .. } => FieldKind::Number { step: 1.0 },
        PropType::Bool => FieldKind::Bool,
        PropType::Color => FieldKind::Color,
        PropType::Enum(options) => FieldKind::Choice(options.clone()),
        PropType::String | PropType::StringList | PropType::Vec3 | PropType::Entity => {
            FieldKind::Text
        }
    };
    let default = def.default.as_ref().map(|value| value.to_attr());
    let mut tip = format!("{} · {}", prop_attr(&def.name), def.ty);
    if let Some(default) = &default {
        tip.push_str(&format!(" · {}: {default}", t(lang, StrKey::PropDefault)));
    }
    if matches!(def.ty, PropType::StringList) {
        tip.push_str(" · a|b|c");
    }
    if !def.doc.is_empty() {
        tip = format!("{} — {tip}", def.doc);
    }
    FieldDef {
        target: FieldTarget::Prop {
            name: def.name.clone(),
            default,
            int: matches!(def.ty, PropType::Int { .. }),
        },
        kind,
        tip: intern(tip),
    }
}

/// Renders the Script section (nothing when the node has no script).
pub fn render(
    commands: &mut Commands,
    parent: Entity,
    doc: &SceneDoc,
    node: &NodeRef,
    xml: &XmlNode,
    open: &SectionOpen,
    lang: Lang,
) {
    let Some(script) = node_script(doc, xml) else {
        return;
    };
    let title = format!("{} — {}", t(lang, StrKey::SecScript), script.script);
    let body = super::property::spawn_section(commands, parent, SectionId::Script, open, title);
    let open_row = row(commands, body);
    commands
        .spawn_scene(bsn! {
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    @icon_caption(fa::TERMINAL, t(lang, StrKey::ScriptOpen), Color::WHITE)
                },
            }
        })
        .insert((
            OpenScriptButton(script.world_rel.clone()),
            ChildOf(open_row),
        ))
        .observe(open_script);
    let problems = script.error.iter().cloned().chain(
        script
            .exports
            .errors
            .iter()
            .map(|e| format!("--@export {e}")),
    );
    for problem in problems {
        let line = row(commands, body);
        commands.spawn((
            Text::new(problem),
            TextFont::from_font_size(11.0),
            TextColor(ERROR),
            ChildOf(line),
        ));
    }
    for def in &script.exports.defs {
        let field = field_for(def, lang);
        let field_row = super::property::spawn_field(commands, body, doc, node, xml, &field, lang);
        if xml.attr(&prop_attr(&def.name)).is_some() {
            reset_button(
                commands,
                field_row,
                &prop_attr(&def.name),
                fa::ROTATE_LEFT,
                StrKey::PropReset,
                lang,
            );
        }
    }
    // `prop-*` the script does not export (typos, leftovers of a renamed export).
    let exported: HashSet<&str> = script
        .exports
        .defs
        .iter()
        .map(|def| def.name.as_str())
        .collect();
    for (key, value) in &xml.attrs {
        let Some(name) = prop_name(key) else {
            continue;
        };
        if exported.contains(name) {
            continue;
        }
        let line = row(commands, body);
        commands.spawn((
            Text::new(format!("{key} = {value}")),
            TextFont::from_font_size(12.0),
            TextColor(ERROR),
            Node {
                flex_grow: 1.0,
                ..default()
            },
            ChildOf(line),
        ));
        reset_button(
            commands,
            line,
            key,
            fa::CIRCLE_XMARK,
            StrKey::PropUnknown,
            lang,
        );
    }
    // Sinais (`--@signal`): quem ouve cada um (`connect-<sinal>`) + os grupos do nó.
    if !script.exports.signals.is_empty() {
        let line = row(commands, body);
        commands.spawn((
            Text::new(t(lang, StrKey::ScriptSignals)),
            TextFont::from_font_size(11.0),
            TextColor(Color::srgb(0.62, 0.66, 0.74)),
            ChildOf(line),
        ));
    }
    for signal in &script.exports.signals {
        let field = signal_field(signal, lang);
        super::property::spawn_field(commands, body, doc, node, xml, &field, lang);
    }
    for (key, value) in &xml.attrs {
        let Some(signal) = connect_signal(key) else {
            continue;
        };
        if script.exports.signal(signal).is_some() {
            continue;
        }
        let line = row(commands, body);
        commands.spawn((
            Text::new(format!("{key} = {value}")),
            TextFont::from_font_size(12.0),
            TextColor(ERROR),
            Node {
                flex_grow: 1.0,
                ..default()
            },
            ChildOf(line),
        ));
        reset_button(
            commands,
            line,
            key,
            fa::CIRCLE_XMARK,
            StrKey::PropUnknown,
            lang,
        );
    }
    let groups = FieldDef {
        target: FieldTarget::Link {
            key: "groups".into(),
        },
        kind: FieldKind::Text,
        tip: "groups=\"a b\" — viber.group / viber.call_group / @grupo",
    };
    super::property::spawn_field(commands, body, doc, node, xml, &groups, lang);
}

/// The text field of one declared signal: its `connect-<signal>` attribute.
pub fn signal_field(signal: &SignalDef, lang: Lang) -> FieldDef {
    let mut tip = format!(
        "{}({}) · {} · {}",
        signal.name,
        signal.args.join(", "),
        connect_attr(&signal.name),
        t(lang, StrKey::SignalTip)
    );
    if !signal.doc.is_empty() {
        tip = format!("{} — {tip}", signal.doc);
    }
    FieldDef {
        target: FieldTarget::Link {
            key: connect_attr(&signal.name),
        },
        kind: FieldKind::Text,
        tip: intern(tip),
    }
}

const ERROR: Color = Color::srgb(0.95, 0.45, 0.42);

fn row(commands: &mut Commands, parent: Entity) -> Entity {
    commands
        .spawn((
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::vertical(px(1)),
                ..default()
            },
            ChildOf(parent),
        ))
        .id()
}

/// The revert/remove icon, right after the row's label (a long text value would push a
/// trailing button out of the panel).
fn reset_button(
    commands: &mut Commands,
    row: Entity,
    attr: &str,
    glyph: char,
    tip: StrKey,
    lang: Lang,
) {
    let button = commands
        .spawn((
            icons::icon_text(glyph, 12.0, icons::ICON_DIM),
            Node {
                width: px(16),
                flex_shrink: 0.0,
                ..default()
            },
            PropReset(attr.to_owned()),
            Tooltip(t(lang, tip)),
        ))
        .observe(reset_prop)
        .id();
    commands.entity(row).insert_children(1, &[button]);
}

/// Removes the attribute (`prop-<name>`, `connect-*`) from the selected node (one undoable op).
fn reset_prop(
    click: On<PointerClick>,
    buttons: Query<&PropReset>,
    selection: Res<Selection>,
    doc: Res<SceneDoc>,
    mut edits: ResMut<PendingEdits>,
) {
    let Ok(button) = buttons.get(click.event_target()) else {
        return;
    };
    let Some(node) = selection.primary() else {
        return;
    };
    let attr = button.0.clone();
    edits.push(
        format!("{attr}: repor default"),
        vec![EditOp::Set {
            node: doc.selector(node),
            attrs: [(attr, None)].into_iter().collect(),
        }],
    );
}

/// Switches to Script mode with the node's script selected.
fn open_script(
    activate: On<Activate>,
    buttons: Query<&OpenScriptButton>,
    mut mode: ResMut<crate::ui::mode_strip::EditorModeState>,
    mut scripts: ResMut<crate::ui::mode_strip::ScriptState>,
) {
    let Ok(button) = buttons.get(activate.event_target()) else {
        return;
    };
    scripts.selected = Some(button.0.clone());
    mode.0 = crate::ui::mode_strip::EditorMode::Script;
}

/// Tooltips are `&'static str`: script docs are interned once per distinct text (bounded by
/// what the world's scripts declare).
fn intern(text: String) -> &'static str {
    static POOL: OnceLock<Mutex<HashSet<&'static str>>> = OnceLock::new();
    let mut pool = POOL
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(existing) = pool.get(text.as_str()) {
        return existing;
    }
    let leaked: &'static str = Box::leak(text.into_boxed_str());
    pool.insert(leaked);
    leaked
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exports_map_to_typed_fields() {
        let exports = parse_exports(
            "--@export speed: number(0..4) = 1.1 -- m/s\n--@export gold: int = 3\n--@export mood: enum(calm, grumpy) = calm\n--@export tint: color\n--@export lines: string[] = a | b\n--@export on: bool = true\n",
        );
        let fields: Vec<FieldDef> = exports
            .defs
            .iter()
            .map(|def| field_for(def, Lang::Pt))
            .collect();
        assert!(matches!(fields[0].kind, FieldKind::Number { .. }));
        assert!(fields[0].tip.starts_with("m/s — prop-speed · number(0..4)"));
        assert!(matches!(
            fields[1].target,
            FieldTarget::Prop { int: true, .. }
        ));
        assert!(matches!(&fields[2].kind, FieldKind::Choice(o) if o.len() == 2));
        assert!(matches!(fields[3].kind, FieldKind::Color));
        assert!(matches!(fields[4].kind, FieldKind::Text));
        assert!(matches!(fields[5].kind, FieldKind::Bool));
        // default visível quando a instância não tem valor
        let node = viber::xml::parse_fragment(r#"<Entity script="npc.lua" prop-gold="7" />"#)
            .unwrap()
            .remove(0);
        assert_eq!(
            super::super::property::field_text(&node, &fields[4].target),
            "a|b"
        );
        assert_eq!(
            super::super::property::field_number(&node, &fields[1].target),
            Some(7.0)
        );
        let signals = parse_exports("--@signal opened(gold) -- abriu\n");
        let field = signal_field(&signals.signals[0], Lang::Pt);
        assert!(matches!(&field.target, FieldTarget::Link { key } if key == "connect-opened"));
        assert!(
            field
                .tip
                .starts_with("abriu — opened(gold) · connect-opened")
        );
        assert_eq!(
            intern("x".into()) as *const str,
            intern("x".into()) as *const str
        );
    }
}
