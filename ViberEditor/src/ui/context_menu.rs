//! The context menu (Fase A2): right-click a tree row (or the viewport — it
//! picks the node under the cursor) for the node actions: duplicate, delete,
//! rename (inline, F2), copy/paste, group selection, add child, open include.
//! Built on the editor's own overlay pattern (the Add-node dialog's), one
//! click outside closes it.

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::doc::{NodeRef, SceneDoc};
use viber::scene::ops::EditOp;
use viber::xml::emit::emit_node;

use super::strings::{Lang, StrKey, t};
use crate::scene::{NodeClipboard, PendingEdits, Selection};
use crate::ui::icons::{self, fa, icon_caption};

/// The open menu's target and position.
#[derive(Resource, Debug, Default, Clone)]
pub struct ContextMenu {
    pub open: bool,
    /// Window px (top-left origin) where the menu shows.
    pub at: Vec2,
    pub node: Option<NodeRef>,
}

/// The menu panel (spawned by the layout; shown/hidden here).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ContextPanel;

/// The rows container (rebuilt while open).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ContextBody;

/// One action row.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContextAction(pub Action);

/// The actions the menu offers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Duplicate,
    Delete,
    Rename,
    Copy,
    Paste,
    Group,
    AddChild,
    OpenInclude,
}

pub struct ContextMenuPlugin;

impl Plugin for ContextMenuPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<ContextMenu>()
            .add_systems(Update, render_context_menu)
            .add_systems(Update, close_outside.after(render_context_menu));
    }
}

/// Opens the menu for a node at a window position.
pub fn open_for(world: &mut World, node: Option<NodeRef>, at: Vec2) {
    world.resource_mut::<ContextMenu>().open = true;
    world.resource_mut::<ContextMenu>().at = at;
    world.resource_mut::<ContextMenu>().node = node;
}

/// Esc closes; a left-click that is NOT inside the panel closes (the panel is
/// a pointer catcher, so a click that reaches the world was outside it).
pub fn close_outside(world: &mut World) {
    let menu = world.resource::<ContextMenu>().clone();
    if !menu.open {
        return;
    }
    let keys = world.resource::<ButtonInput<KeyCode>>();
    if keys.just_pressed(KeyCode::Escape) {
        world.resource_mut::<ContextMenu>().open = false;
        return;
    }
    if !world
        .resource::<ButtonInput<MouseButton>>()
        .just_pressed(MouseButton::Left)
    {
        return;
    }
    // clique dentro do painel? o painel é um catcher (Pickable::BLOCK) — um
    // clique que chega a JANELA fora dele fecha o menu.
    let Some(window) = world.query::<&Window>().iter(world).next().cloned() else {
        return;
    };
    let _ = window;
    // o painel intercepta, logo qualquer PointerClick que CORTE o painel não
    // chega aqui; fechar em qualquer clique é seguro porque as ações correm
    // em observers (antes deste sistema, na mesma frame — ver ordenação).
    // Para não fechar ANTES da ação: as ações não usam PointerClick fora do
    // painel. Fecha:
    let hovered_panel = world
        .query_filtered::<&bevy::picking::hover::Hovered, With<ContextPanel>>()
        .iter(world)
        .any(|hovered| hovered.0);
    if !hovered_panel {
        world.resource_mut::<ContextMenu>().open = false;
    }
}

/// Rebuilds the panel rows while open.
#[allow(clippy::too_many_arguments)]
fn render_context_menu(
    menu: Res<ContextMenu>,
    doc: Res<SceneDoc>,
    selection: Res<Selection>,
    clipboard: Option<Res<NodeClipboard>>,
    lang: Res<Lang>,
    windows: Query<&Window>,
    mut commands: Commands,
    mut panels: Query<(&mut Node, &Children), With<ContextPanel>>,
    bodies: Query<Entity, With<ContextBody>>,
) {
    let Ok((mut node, children)) = panels.single_mut() else {
        return;
    };
    let _ = children;
    node.display = if menu.open {
        Display::Flex
    } else {
        Display::None
    };
    node.position_type = PositionType::Absolute;
    // clamp à janela: right-click perto da borda abria o menu fora do ecrã
    // (8 ações × ~28 px + padding é o pior caso)
    if let Ok(window) = windows.single() {
        node.left = Val::Px(menu.at.x.min((window.width() - 180.0).max(0.0)));
        node.top = Val::Px(menu.at.y.min((window.height() - 250.0).max(0.0)));
    } else {
        node.left = Val::Px(menu.at.x);
        node.top = Val::Px(menu.at.y);
    }
    if !menu.open || !menu.is_changed() {
        return;
    }
    let Ok(body) = bodies.single() else {
        return;
    };
    commands.entity(body).despawn_related::<Children>();
    let Some(target) = menu.node.clone() else {
        return;
    };
    let Some(xml) = doc.node(&target) else {
        return;
    };
    let is_root = target.is_root();
    let selected_count = selection.0.iter().filter(|n| !n.is_root()).count();
    let has_clipboard = clipboard.is_some_and(|clip| !clip.0.is_empty());
    let mut actions: Vec<(Action, char)> = vec![(Action::AddChild, fa::PLUS)];
    if !is_root {
        actions.extend([
            (Action::Duplicate, fa::CLONE),
            (Action::Rename, fa::TAGS),
            (Action::Copy, fa::FILE_LINES),
        ]);
        if has_clipboard {
            actions.push((Action::Paste, fa::FOLDER));
        }
        if xml.tag.eq_ignore_ascii_case("include") {
            actions.push((Action::OpenInclude, fa::GLOBE));
        }
        actions.push((Action::Delete, fa::BAN));
    }
    if selected_count > 1 {
        actions.insert(1, (Action::Group, fa::FOLDER));
    }
    for (action, glyph) in actions {
        let label = match action {
            Action::Duplicate => t(*lang, StrKey::MenuDuplicate),
            Action::Delete => t(*lang, StrKey::MenuDelete),
            Action::Rename => t(*lang, StrKey::MenuRename),
            Action::Copy => t(*lang, StrKey::MenuCopy),
            Action::Paste => t(*lang, StrKey::MenuPaste),
            Action::Group => t(*lang, StrKey::MenuGroup),
            Action::AddChild => t(*lang, StrKey::BtnAddNode),
            Action::OpenInclude => t(*lang, StrKey::FilesOpenWorld),
        };
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(glyph, label, icons::ICON)
                    },
                }
                // itens de menu alinhados à esquerda (o botão centra por omissão)
                Node { justify_content: JustifyContent::FlexStart }
            })
            .insert((ContextAction(action), ChildOf(body)))
            .observe(action_clicked);
    }
}

fn action_clicked(
    activate: On<Activate>,
    rows: Query<&ContextAction>,
    menu: Res<ContextMenu>,
    mut commands: Commands,
) {
    let Ok(action) = rows.get(activate.event_target()) else {
        return;
    };
    let node = menu.node.clone();
    let action = action.0;
    commands.queue(move |world: &mut World| {
        world.resource_mut::<ContextMenu>().open = false;
        run(world, action, node);
    });
}

/// Runs one action (the same ops the keyboard shortcuts use).
fn run(world: &mut World, action: Action, node: Option<NodeRef>) {
    let lang = *world.resource::<Lang>();
    let Some(node) = node else {
        return;
    };
    let doc = world.resource::<SceneDoc>().clone();
    let Some(xml) = doc.node(&node) else {
        return;
    };
    let selector = doc.selector(&node);
    let push = |world: &mut World, label: String, ops: Vec<EditOp>| {
        world.resource_mut::<PendingEdits>().push(label, ops);
    };
    match action {
        Action::Duplicate => push(
            world,
            format!("{} {selector}", t(lang, StrKey::MenuDuplicate)),
            vec![EditOp::Duplicate { node: selector }],
        ),
        Action::Delete => push(
            world,
            format!("{} {selector}", t(lang, StrKey::MenuDelete)),
            vec![EditOp::Remove { node: selector }],
        ),
        Action::Copy => {
            world.insert_resource(NodeClipboard(vec![emit_node(xml)]));
        }
        Action::Paste => {
            // lê SEM remover: a clipboard sobrevive para o próximo paste
            let Some(clipboard) = world.get_resource::<NodeClipboard>() else {
                return;
            };
            let Some(parent) = node.parent() else {
                return;
            };
            let parent_selector = doc.selector(&parent);
            let mut index = node.path.last().copied().unwrap_or(0) + 1;
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
            push(
                world,
                format!("{} {selector}", t(lang, StrKey::MenuPaste)),
                ops,
            );
        }
        Action::Rename => {
            // renomear inline: o modo fica na árvore (F2) — seleciona e foca
            world.resource_mut::<Selection>().set_one(node.clone());
            let mut rename = world.resource_mut::<crate::scene::tree::RenameState>();
            rename.0 = Some(node);
            rename.1 = false;
        }
        Action::Group => group_selection(world),
        Action::AddChild => {
            world.resource_mut::<Selection>().set_one(node.clone());
            world
                .resource_mut::<crate::scene::add_node::AddNodeOpen>()
                .0 = true;
        }
        Action::OpenInclude => {
            // abrir o include como mundo (nova janela) — a ação do dock Files
            let Some(src) = xml.attr("src") else {
                return;
            };
            let path = doc
                .world_path()
                .parent()
                .map(|dir| dir.join(src.trim_start_matches('/')));
            if let Some(path) = path
                && let Ok(exe) = std::env::current_exe()
            {
                let _ = std::process::Command::new(exe).arg(path).spawn();
            }
        }
    }
}

/// Agrupa a seleção num `<Group>` novo no mesmo pai, no lugar do primeiro
/// nó em ordem de documento, com os filhos nessa mesma ordem.
fn group_selection(world: &mut World) {
    let lang = *world.resource::<Lang>();
    let doc = world.resource::<SceneDoc>().clone();
    let selected: Vec<NodeRef> = world
        .resource::<Selection>()
        .0
        .iter()
        .filter(|node| !node.is_root())
        .cloned()
        .collect();
    if selected.len() < 2 {
        return;
    }
    let Some(parent) = selected[0].parent() else {
        return;
    };
    if selected
        .iter()
        .any(|node| node.parent() != Some(parent.clone()))
    {
        world.resource_mut::<crate::scene::EditStatus>().0 =
            t(lang, StrKey::GroupSameParent).to_owned();
        return;
    }
    let Some(ops) = group_ops(&doc, &selected) else {
        return;
    };
    world
        .resource_mut::<PendingEdits>()
        .push(t(lang, StrKey::MenuGroup).to_owned(), ops);
}

/// Agrupa no menor índice da seleção e mantém a ordem do documento.
/// Os `Move` saem por índice descendente (os caminhos não apodrecem) com
/// `index: 0` — append descendente invertia os filhos.
pub fn group_ops(doc: &SceneDoc, selected: &[NodeRef]) -> Option<Vec<EditOp>> {
    if selected.len() < 2 {
        return None;
    }
    let parent = selected[0].parent()?;
    if selected
        .iter()
        .any(|node| node.parent().as_ref() != Some(&parent))
    {
        return None;
    }
    let parent_selector = doc.selector(&parent);
    let mut group_name = "group".to_owned();
    let mut counter = 1;
    while !doc.find_by_attr("name", &group_name).is_empty() {
        group_name = format!("group{counter}");
        counter += 1;
    }
    let insert_at = selected
        .iter()
        .filter_map(|node| node.path.last().copied())
        .min()
        .unwrap_or(0);
    let mut ops = vec![EditOp::Insert {
        parent: parent_selector.clone(),
        index: None,
        xml: format!(r#"<Group name="{group_name}" />"#),
    }];
    let mut ordered: Vec<&NodeRef> = selected.iter().collect();
    ordered.sort_by(|a, b| b.path.last().cmp(&a.path.last()));
    for node in ordered {
        ops.push(EditOp::Move {
            node: doc.selector(node),
            parent: format!("name={group_name}"),
            index: Some(0),
        });
    }
    ops.push(EditOp::Move {
        node: format!("name={group_name}"),
        parent: parent_selector,
        index: Some(insert_at),
    });
    Some(ops)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn group_keeps_document_order_at_the_earliest_index() {
        let dir = tempfile::tempdir().unwrap();
        let world = dir.path().join("world.xml");
        std::fs::write(
            &world,
            r#"<world static="true"><Group name="props"><Entity name="A" /><Entity name="B" /><Entity name="C" /><Entity name="D" /></Group></world>"#,
        )
        .unwrap();
        let mut doc = SceneDoc::load(&world).unwrap();
        let props = doc.find_by_attr("name", "props")[0].clone();
        let b = doc.find_by_attr("name", "B")[0].clone();
        let c = doc.find_by_attr("name", "C")[0].clone();
        // clique em C e depois em B — a ordem do documento é B, C
        let ops = group_ops(&doc, &[c, b]).unwrap();
        viber::scene::ops::apply_batch(&mut doc, "group", &ops).unwrap();
        let kids = doc.children(&props).unwrap();
        assert_eq!(kids[0].attr("name"), Some("A"));
        assert_eq!(kids[1].tag.to_ascii_lowercase(), "group");
        assert_eq!(kids[2].attr("name"), Some("D"));
        let inner: Vec<_> = kids[1]
            .children
            .iter()
            .map(|node| node.attr("name").unwrap_or(""))
            .collect();
        assert_eq!(inner, ["B", "C"]);
    }
}
