//! The edited scene, on top of the engine's own editing API (`viber::scene`): the same
//! document, operations, history and live sync agents use through `viber scene` and the
//! `viber.scene.*` bridge. The editor adds no editing logic of its own — every change it makes
//! is an [`EditOp`] batch queued in [`PendingEdits`] and applied by `viber::scene::live`.

pub mod add_node;
pub mod inspector;
pub mod property;
pub mod script_props;
pub mod tree;
pub mod tree_drag;

pub use viber::scene::doc;

use bevy::input_focus::InputFocus;
use bevy::prelude::*;
use std::collections::HashSet;
use viber::scene::live;
use viber::scene::ops::EditOp;

use crate::ui::strings::{
    Lang, delete_label, duplicate_label, edit_failed, failed, nothing_to_redo, nothing_to_undo,
    redo_prefix, save_failed, save_nothing, saved, undo_prefix,
};

use doc::{NodeRef, SceneDoc};

/// Texto vivo de um input. O `FeathersTextInput` do Bevy 0.20 guarda o que foi
/// digitado em `EditableText`; o componente `Text` ou não existe ou fica no
/// valor do spawn. Preferir `EditableText` — senão Enter vê o valor antigo.
pub fn input_text(world: &World, entity: Entity) -> Option<String> {
    if let Some(editable) = world.get::<bevy::text::EditableText>(entity) {
        return Some(editable.value().to_string());
    }
    world.get::<Text>(entity).map(|text| text.0.clone())
}

/// The node selected in the Objects dock or the viewport.
#[derive(Resource, Debug, Default, Clone, PartialEq)]
pub struct Selection(pub Vec<NodeRef>);

impl Selection {
    /// The primary node (the one the gizmo/inspector/canvas act on).
    pub fn primary(&self) -> Option<&NodeRef> {
        self.0.first()
    }
    pub fn set_one(&mut self, node: NodeRef) {
        self.0 = vec![node];
    }
    /// Ctrl-click: toggle membership (never empties via toggle of primary? Godot allows empty).
    pub fn toggle(&mut self, node: &NodeRef) {
        if let Some(index) = self.0.iter().position(|current| current == node) {
            self.0.remove(index);
        } else {
            self.0.push(node.clone());
        }
    }
    /// Shift-click: add (dedup).
    pub fn extend(&mut self, node: &NodeRef) {
        if !self.0.contains(node) {
            self.0.push(node.clone());
        }
    }
}

/// Expanded nodes of the Scene tree.
#[derive(Resource, Debug, Default, Clone)]
pub struct TreeExpansion(pub HashSet<NodeRef>);

/// Edits requested by the UI this frame, applied in order by [`apply_pending_edits`].
#[derive(Resource, Debug, Default)]
pub struct PendingEdits(pub Vec<Edit>);

/// One undoable editor action.
#[derive(Debug, Clone)]
pub struct Edit {
    pub label: String,
    pub ops: Vec<EditOp>,
    /// Where the selection goes once the edit is applied (`None` keeps it).
    pub select_after: Option<Option<NodeRef>>,
    /// Select (and reveal) the first node the transaction inserted — drops, Add Node.
    pub select_inserted: bool,
}

impl PendingEdits {
    pub fn push(&mut self, label: impl Into<String>, ops: Vec<EditOp>) {
        self.0.push(Edit {
            label: label.into(),
            ops,
            select_after: None,
            select_inserted: false,
        });
    }

    /// Like [`Self::push`], then selects and reveals the node the edit inserts.
    pub fn push_selecting_inserted(&mut self, label: impl Into<String>, ops: Vec<EditOp>) {
        self.0.push(Edit {
            label: label.into(),
            ops,
            select_after: None,
            select_inserted: true,
        });
    }
}

/// The last editing outcome, shown in the toolbar.
#[derive(Resource, Debug, Default, Clone)]
pub struct EditStatus(pub String);

pub struct ScenePlugin;

impl Plugin for ScenePlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<Selection>()
            .init_resource::<TreeExpansion>()
            .init_resource::<PendingEdits>()
            .init_resource::<EditStatus>()
            .add_plugins((add_node::AddNodeDialogPlugin, property::PropertyPlugin))
            .add_systems(
                Update,
                (
                    property::apply_property_changes,
                    property::prefill_text_fields,
                    property::commit_text_fields,
                    property::sync_section_toggles,
                    autosave,
                ),
            )
            .init_resource::<tree_drag::TreeDrag>()
            .init_resource::<tree::RenameState>()
            .init_resource::<tree::TreeView>()
            .init_resource::<inspector::InspectorView>()
            .add_systems(
                Update,
                (
                    shortcuts,
                    apply_pending_edits,
                    validate_selection,
                    tree::apply_tree_search,
                    tree::prefill_rename,
                    tree::commit_rename,
                    tree::render_tree,
                    inspector::render_inspector,
                    inspector::prefill_inputs,
                    inspector::commit_inputs,
                )
                    .chain(),
            );
    }
}

/// Copied node XMLs (Ctrl+C → Ctrl+V), one deep each.
#[derive(Resource, Debug, Default)]
pub struct NodeClipboard(pub Vec<String>);

fn paste_str(lang: crate::ui::strings::Lang) -> &'static str {
    crate::ui::strings::t(lang, crate::ui::strings::StrKey::EditPaste)
}

/// Keys that act on the document, unless a text field has focus:
/// Ctrl+Z undo · Ctrl+Shift+Z / Ctrl+Y redo · Ctrl+S save · Ctrl+D duplicate · Delete remove.
fn shortcuts(world: &mut World) {
    let typing = world
        .get_resource::<InputFocus>()
        .and_then(InputFocus::get)
        .is_some_and(|entity| world.get::<bevy::text::EditableText>(entity).is_some());
    if typing {
        return;
    }
    let keys = world.resource::<ButtonInput<KeyCode>>();
    let ctrl = keys.any_pressed([KeyCode::ControlLeft, KeyCode::ControlRight]);
    let shift = keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]);
    let pressed = |key| keys.just_pressed(key);
    let undo = ctrl && pressed(KeyCode::KeyZ) && !shift;
    let redo = ctrl && ((pressed(KeyCode::KeyZ) && shift) || pressed(KeyCode::KeyY));
    let save = ctrl && pressed(KeyCode::KeyS);
    let duplicate = ctrl && pressed(KeyCode::KeyD);
    let reload = ctrl && pressed(KeyCode::KeyR);
    let delete = pressed(KeyCode::Delete);
    let copy = ctrl && pressed(KeyCode::KeyC);
    let paste = ctrl && pressed(KeyCode::KeyV);
    let rename = pressed(KeyCode::F2);

    if reload {
        crate::app::reload_world(world);
        return;
    }
    let lang = *world.resource::<Lang>();
    if undo {
        let result = live::undo(world).map(|tx| tx.map(|tx| undo_prefix(lang, &tx.label)));
        report(world, result, nothing_to_undo(lang));
    } else if redo {
        let result = live::redo(world).map(|tx| tx.map(|tx| redo_prefix(lang, &tx.label)));
        report(world, result, nothing_to_redo(lang));
    }
    if save {
        save_world(world);
    }
    let selected = world.resource::<Selection>().0.clone();
    let Some(primary) = selected.first().cloned() else {
        return;
    };
    let selector = world.resource::<SceneDoc>().selector(&primary);
    // Lote em ordem DESCENDENTE (file, path): os ops usam seletores de caminho
    // resolvidos no momento de cada op — remover/duplicar um irmão anterior
    // desloca os índices dos seguintes (Delete [A,C] apagava A e D; Duplicate
    // [A,C] duplicava A e B). Do fim para o princípio, nada se desloca —
    // a mesma convenção do group_selection.
    let mut editable: Vec<NodeRef> = selected
        .iter()
        .filter(|node| !node.is_root())
        .cloned()
        .collect();
    editable.sort();
    editable.dedup();
    editable.reverse();
    if !editable.is_empty() {
        // OS TRÊS atalhos agem em LOTE: uma transação (um undo) com todos os nós.
        if duplicate {
            let ops: Vec<EditOp> = editable
                .iter()
                .map(|node| EditOp::Duplicate {
                    node: world.resource::<SceneDoc>().selector(node),
                })
                .collect();
            let copy = primary.parent().map(|parent| {
                let index = primary.path.last().copied().unwrap_or(0) + 1;
                parent.child(index)
            });
            world.resource_mut::<PendingEdits>().0.push(Edit {
                label: duplicate_label(lang, &selector),
                ops,
                select_after: Some(copy),
                select_inserted: false,
            });
        }
        if rename {
            let mut rename = world.resource_mut::<tree::RenameState>();
            rename.0 = Some(primary.clone());
            rename.1 = false;
        }
        if copy {
            // clipboard em ordem ASCENDENTE (a cola reproduz a ordem relativa)
            let mut ascending = editable.clone();
            ascending.reverse();
            let xmls: Vec<String> = ascending
                .iter()
                .filter_map(|node| world.resource::<SceneDoc>().node(node))
                .map(viber::xml::emit::emit_node)
                .collect();
            world.insert_resource(NodeClipboard(xmls));
        }
        if delete {
            let ops: Vec<EditOp> = editable
                .iter()
                .map(|node| EditOp::Remove {
                    node: world.resource::<SceneDoc>().selector(node),
                })
                .collect();
            world.resource_mut::<PendingEdits>().0.push(Edit {
                label: delete_label(lang, &selector),
                ops,
                select_after: Some(None),
                select_inserted: false,
            });
        }
    }
    if paste
        && let Some(clipboard) = world.get_resource::<NodeClipboard>()
        && !clipboard.0.is_empty()
        && let Some(parent) = primary.parent()
    {
        // lê SEM remover: a clipboard sobrevive para o próximo paste
        let parent_selector = world.resource::<SceneDoc>().selector(&parent);
        let mut index = primary.path.last().copied().unwrap_or(0) + 1;
        let ops: Vec<EditOp> = clipboard
            .0
            .iter()
            .map(|xml| {
                let op = EditOp::Insert {
                    parent: parent_selector.clone(),
                    index: Some(index),
                    xml: xml.clone(),
                };
                index += 1;
                op
            })
            .collect();
        world
            .resource_mut::<PendingEdits>()
            .push_selecting_inserted(format!("{} {selector}", paste_str(lang)), ops);
    }
}

/// Autosave (Fase A5): every 2 minutes with dirty files, saves silently.
pub fn autosave(world: &mut World) {
    world.get_resource_or_insert_with::<AutosaveTimer>(|| AutosaveTimer(0.0));
    let delta = world.resource::<Time<Real>>().delta_secs();
    world.resource_mut::<AutosaveTimer>().0 += delta;
    if world.resource::<AutosaveTimer>().0 < 120.0 {
        return;
    }
    world.resource_mut::<AutosaveTimer>().0 = 0.0;
    let dirty = world.resource::<SceneDoc>().dirty_paths();
    if dirty.is_empty() {
        return;
    }
    save_world(world);
    info!("autosave: {} file(s)", dirty.len());
}

/// O cronómetro do autosave (segundos desde o último).
#[derive(Resource, Debug, Default)]
pub struct AutosaveTimer(pub f32);

/// Writes every edited file (Ctrl+S, the Save button, and before ▶ Play).
pub fn save_world(world: &mut World) {
    let lang = *world.resource::<Lang>();
    let status = match live::save(world) {
        Ok(files) if files.is_empty() => save_nothing(lang).to_owned(),
        Ok(files) => saved(lang, &files),
        Err(error) => save_failed(lang, &error),
    };
    info!("{status}");
    world.resource_mut::<EditStatus>().0 = status;
}

fn report(world: &mut World, result: anyhow::Result<Option<String>>, empty: &str) {
    let status = match result {
        Ok(Some(label)) => label,
        Ok(None) => empty.to_owned(),
        Err(error) => failed(*world.resource::<Lang>(), &error),
    };
    world.resource_mut::<EditStatus>().0 = status;
}

/// Applies the queued edits through the engine's live editing API (history + live sync).
pub fn apply_pending_edits(world: &mut World) {
    let edits = std::mem::take(&mut world.resource_mut::<PendingEdits>().0);
    for edit in edits {
        let status = match live::apply(world, &edit.label, &edit.ops) {
            Ok(tx) => {
                if let Some(selection) = edit.select_after {
                    world.resource_mut::<Selection>().0 = selection.into_iter().collect();
                }
                let inserted = tx.changes.iter().find_map(|change| match change {
                    viber::scene::ops::Change::Inserted { node } => Some(node.clone()),
                    _ => None,
                });
                if edit.select_inserted
                    && let Some(node) = inserted
                {
                    world.resource_scope(|world, mut expansion: Mut<TreeExpansion>| {
                        reveal(world.resource::<SceneDoc>(), &node, &mut expansion);
                    });
                    world.resource_mut::<Selection>().set_one(node);
                }
                edit.label
            }
            Err(error) => {
                warn!("{}: {error:#}", edit.label);
                edit_failed(*world.resource::<Lang>(), &edit.label, &error)
            }
        };
        world.resource_mut::<EditStatus>().0 = status;
    }
}

/// Drops a selection whose node no longer exists (deleted, or undone insertion).
fn validate_selection(doc: Res<SceneDoc>, mut selection: ResMut<Selection>) {
    if !doc.is_changed() {
        return;
    }
    let before = selection.0.len();
    selection
        .0
        .retain(|node| node.is_root() || doc.node(node).is_some());
    if selection.0.len() != before {
        // mudou (dispara re-render dos docks)
    }
}

/// Expands every ancestor of `node` in the Scene tree (including the `<Include>` chain), so a
/// node picked in the viewport is visible in the dock.
pub fn reveal(doc: &SceneDoc, node: &NodeRef, expansion: &mut TreeExpansion) {
    let mut current = node.clone();
    for _ in 0..256 {
        match current.parent() {
            Some(parent) if !parent.is_root() => {
                expansion.0.insert(parent.clone());
                current = parent;
            }
            _ => {
                let includer = (0..doc.files.len()).find_map(|file| {
                    doc.files[file]
                        .includes
                        .iter()
                        .find(|(_, target)| *target == current.file)
                        .map(|(path, _)| NodeRef::new(file, path.clone()))
                });
                let Some(include) = includer else {
                    return;
                };
                expansion.0.insert(include.clone());
                current = include;
            }
        }
    }
}
