//! OS drag-and-drop into the viewport (Fase 3): `.glb` files dropped anywhere over
//! the window become insert ops — [`viber::scene::catalog::glb_drop`] — dropped on
//! the terrain under the last viewport cursor ray (world origin as fallback).
//! Multiple files in one gesture land side by side (offset per index) as ONE
//! undoable transaction.

use bevy::prelude::*;
use bevy::window::FileDragAndDrop;
use viber::scene::doc::NodeRef;

use super::gizmo::ViewportCursor;
use crate::scene::PendingEdits;

pub struct DropPlugin;

impl Plugin for DropPlugin {
    fn build(&self, app: &mut App) {
        app.add_message::<FileDragAndDrop>()
            .add_systems(Update, handle_drop);
    }
}

fn handle_drop(mut events: MessageReader<FileDragAndDrop>, mut commands: Commands) {
    // os eventos de UM gesto chegam no mesmo frame: junta tudo num lote
    let mut batch: Vec<String> = Vec::new();
    for event in events.read() {
        let FileDragAndDrop::DroppedFile { path_buf, .. } = event else {
            continue;
        };
        let path = path_buf.to_string_lossy().to_string();
        if !path.to_ascii_lowercase().ends_with(".glb") {
            info!("drop: `{path}` não é .glb — ignorado");
            continue;
        }
        batch.push(path);
    }
    if batch.is_empty() {
        return;
    }
    commands.queue(move |world: &mut World| {
        let at = drop_point(world).unwrap_or(Vec3::ZERO);
        let doc = world.resource::<viber::scene::doc::SceneDoc>().clone();
        let parent = doc.selector(&NodeRef::root(0));
        let lang = *world.resource::<crate::ui::strings::Lang>();
        let mut ops = Vec::new();
        let mut names = Vec::new();
        for (index, path) in batch.iter().enumerate() {
            // N ficheiros: offset lateral por índice — sem isto caíam todos
            // exatamente sobrepostos
            let at = at + Vec3::X * (1.5 * index as f32);
            match viber::scene::catalog::glb_drop(&doc, &parent, path, [at.x, at.y, at.z]) {
                Ok(op) => {
                    names.push(
                        std::path::Path::new(path)
                            .file_stem()
                            .and_then(|stem| stem.to_str())
                            .unwrap_or("glb")
                            .to_owned(),
                    );
                    ops.push(op);
                }
                Err(error) => {
                    warn!("drop {path}: {error:#}");
                }
            }
        }
        if ops.is_empty() {
            return;
        }
        let label = if names.len() == 1 {
            format!(
                "{} {}",
                crate::ui::strings::pick(lang, "soltar", "drop"),
                names[0]
            )
        } else {
            format!(
                "{} {} {}",
                crate::ui::strings::pick(lang, "soltar", "drop"),
                names.len(),
                crate::ui::strings::pick(lang, "ficheiros", "files")
            )
        };
        world
            .resource_mut::<PendingEdits>()
            .push_selecting_inserted(label.clone(), ops);
        world.resource_mut::<crate::scene::EditStatus>().0 = format!(
            "{}: {}",
            crate::ui::strings::t(lang, crate::ui::strings::StrKey::DropGlb),
            label
        );
    });
}

/// The terrain point under the last viewport cursor ray.
fn drop_point(world: &mut World) -> Option<Vec3> {
    let cursor = *world.resource::<ViewportCursor>();
    let ray = cursor.ray?;
    let runtime = world.get_resource::<viber::terrain::runtime::TerrainRuntime>()?;
    viber::scene::terrain::ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
        runtime.sample(x, z)
    })
}
