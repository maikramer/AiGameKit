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
    // Guard: never into itself or its own subtree.
    let inside_self = target.file == dragged.file
        && target.path.len() >= dragged.path.len()
        && target
            .path
            .iter()
            .zip(dragged.path.iter())
            .all(|(a, b)| a == b);
    if inside_self || *target == dragged {
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
    // Same place → no-op.
    if dragged.parent().as_ref() == Some(&parent) {
        let current = dragged.path.last().copied().unwrap_or(0);
        let effective = if index > current { index - 1 } else { index };
        if effective == current {
            return;
        }
    }
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

/// Attaches all three observers to a row entity (called from render_tree).
pub fn attach(commands: &mut Commands, entity: Entity, node: &NodeRef) {
    commands
        .entity(entity)
        .insert(DragRow(node.clone()))
        .observe(drag_start)
        .observe(drag_end)
        .observe(drag_drop);
}
