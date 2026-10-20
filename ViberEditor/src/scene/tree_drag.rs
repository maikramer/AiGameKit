//! Tree drag-drop (Fase A3): reparent rows by dragging them onto other rows.
//! Three drop zones per row (top = before, middle = into, bottom = after),
//! one atomic `move` op with guards (never into the dragged subtree).

use bevy::picking::events::{PointerDragDrop, PointerDragEnd, PointerDragStart};
use bevy::picking::pointer::PointerButton;
use bevy::prelude::*;
use bevy::ui::{ComputedNode, UiGlobalTransform};

use crate::scene::PendingEdits;
use crate::ui::strings::{Lang, StrKey, t};
use viber::scene::doc::{NodeRef, SceneDoc};
use viber::scene::ops::EditOp;

/// Marker: a tree row that can be dragged and dropped on.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct DragRow(pub NodeRef);

/// The row currently being dragged.
#[derive(Resource, Debug, Default, Clone)]
pub struct TreeDrag(pub Option<NodeRef>);

/// Drag starts: record the node (left button only).
pub fn drag_start(drag: On<PointerDragStart>, rows: Query<&DragRow>, mut state: ResMut<TreeDrag>) {
    if drag.button != PointerButton::Primary {
        return;
    }
    if let Ok(row) = rows.get(drag.event_target()) {
        state.0 = Some(row.0.clone());
    }
}

/// Drag ends without a valid drop: clears.
pub fn drag_end(_drag: On<PointerDragEnd>, mut state: ResMut<TreeDrag>) {
    state.0 = None;
}

/// Drop on a row: top/middle/bottom zones → one `move` op (guarded).
pub fn drag_drop(
    drop: On<PointerDragDrop>,
    rows: Query<&DragRow>,
    nodes: Query<(&ComputedNode, &UiGlobalTransform)>,
    doc: Res<SceneDoc>,
    lang: Res<Lang>,
    mut state: ResMut<TreeDrag>,
    mut pending: ResMut<PendingEdits>,
) {
    let Some(dragged) = state.0.take() else {
        return;
    };
    let Ok(row) = rows.get(drop.event_target()) else {
        return;
    };
    let target = &row.0;
    if drop_would_cycle(&doc, &dragged, target) {
        return;
    }
    let Ok((node, transform)) = nodes.get(drop.event_target()) else {
        return;
    };
    // `pointer.position` é lógico (winit); `ComputedNode`/`UiGlobalTransform`
    // são FÍSICOS — sem o scale factor a zona antes/dentro/depois errava no HiDPI
    let pointer = drop.pointer.position / node.inverse_scale_factor();
    let size = node.size();
    let top_left = transform.translation - size * 0.5;
    let local_y = pointer.y - top_left.y;
    let (parent, index) = if local_y < size.y / 3.0 {
        let Some(parent) = target.parent() else {
            return;
        };
        (parent, target.path.last().copied().unwrap_or(0))
    } else if local_y > size.y * 2.0 / 3.0 {
        let Some(parent) = target.parent() else {
            return;
        };
        (parent, target.path.last().copied().unwrap_or(0) + 1)
    } else {
        (
            target.clone(),
            doc.children(target).map_or(0, |kids| kids.len()),
        )
    };
    // O motor remove o nó ANTES de inserir. No mesmo pai, um índice à frente
    // do sítio atual já desceu 1 — passar o índice cru caía uma posição à frente.
    let same_parent = dragged.parent().as_ref() == Some(&parent);
    let current = dragged.path.last().copied().unwrap_or(0);
    let Some(index) = adjusted_drop_index(same_parent, current, index) else {
        return;
    };
    let label = t(*lang, StrKey::MenuMove).to_owned();
    pending.push(
        format!("{label} → {}", doc.selector(&parent)),
        vec![EditOp::Move {
            node: doc.selector(&dragged),
            parent: doc.selector(&parent),
            index: Some(index),
        }],
    );
}

/// Índice de inserção depois de o nó sair da lista. `None` = largar no sítio
/// onde já está.
pub fn adjusted_drop_index(same_parent: bool, current: usize, requested: usize) -> Option<usize> {
    let effective = if same_parent && requested > current {
        requested - 1
    } else {
        requested
    };
    (effective != current || !same_parent).then_some(effective)
}

/// Recusa largar um nó dentro de si, da sua subárvore, ou dentro de um
/// `<Include>` que essa subárvore carrega (os filhos do include vivem noutro
/// ficheiro — a guarda por prefixo de caminho não os vê).
pub fn drop_would_cycle(doc: &SceneDoc, dragged: &NodeRef, target: &NodeRef) -> bool {
    if target == dragged {
        return true;
    }
    let inside_self = target.file == dragged.file
        && target.path.len() >= dragged.path.len()
        && target
            .path
            .iter()
            .zip(dragged.path.iter())
            .all(|(a, b)| a == b);
    if inside_self {
        return true;
    }
    let Some(file) = doc.files.get(dragged.file) else {
        return false;
    };
    file.includes.iter().any(|(path, included)| {
        path.starts_with(dragged.path.as_slice()) && file_reaches(doc, *included, target.file, 0)
    })
}

fn file_reaches(
    doc: &SceneDoc,
    from: viber::scene::doc::FileId,
    goal: viber::scene::doc::FileId,
    depth: usize,
) -> bool {
    if from == goal {
        return true;
    }
    if depth > 8 {
        return false;
    }
    let Some(file) = doc.files.get(from) else {
        return false;
    };
    file.includes
        .iter()
        .any(|(_, child)| file_reaches(doc, *child, goal, depth + 1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn doc(dir: &Path) -> SceneDoc {
        std::fs::write(
            dir.join("world.xml"),
            r#"<world static="true">
  <Group name="props"><Entity name="crate" /><Entity name="barrel" /><Entity name="chest" /></Group>
  <Include src="part.xml" />
</world>"#,
        )
        .unwrap();
        std::fs::write(
            dir.join("part.xml"),
            r#"<world static="true"><Entity name="well" /></world>"#,
        )
        .unwrap();
        SceneDoc::load(&dir.join("world.xml")).unwrap()
    }

    #[test]
    fn moving_down_among_siblings_uses_the_index_after_removal() {
        assert_eq!(adjusted_drop_index(true, 0, 2), Some(1));
        assert_eq!(
            adjusted_drop_index(true, 0, 1),
            None,
            "já está antes do alvo"
        );
        assert_eq!(adjusted_drop_index(true, 2, 0), Some(0));
        assert_eq!(adjusted_drop_index(false, 0, 2), Some(2));
    }

    #[test]
    fn include_cannot_be_dropped_into_its_own_file() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = doc(tmp.path());
        let include = NodeRef {
            file: 0,
            path: vec![1],
        };
        let well = NodeRef {
            file: 1,
            path: vec![0],
        };
        let crate_node = NodeRef {
            file: 0,
            path: vec![0, 0],
        };
        let barrel = NodeRef {
            file: 0,
            path: vec![0, 1],
        };
        assert!(drop_would_cycle(&doc, &include, &well));
        assert!(drop_would_cycle(
            &doc,
            &NodeRef {
                file: 0,
                path: vec![0]
            },
            &crate_node
        ));
        assert!(!drop_would_cycle(&doc, &crate_node, &barrel));
        assert!(!drop_would_cycle(&doc, &include, &crate_node));
    }
}

/// Attaches all three observers to a row entity (called from render_tree).
pub fn attach(commands: &mut Commands, entity: Entity, node: &NodeRef) {
    commands
        .entity(entity)
        .insert(DragRow(node.clone()))
        .observe(drag_start)
        .observe(drag_end)
        .observe(drag_drop);
}
