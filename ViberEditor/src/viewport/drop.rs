//! OS drag-and-drop into the viewport (Fase 3): a `.glb` dropped anywhere over the
//! window becomes an insert op — [`viber::scene::catalog::glb_drop`] — dropped on
//! the terrain under the last viewport cursor ray (world origin as fallback).

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
    for event in events.read() {
        let FileDragAndDrop::DroppedFile { path_buf, .. } = event else {
            continue;
        };
        let path = path_buf.to_string_lossy().to_string();
        if !path.to_ascii_lowercase().ends_with(".glb") {
            info!("drop: `{path}` não é .glb — ignorado");
            continue;
        }
        commands.queue(move |world: &mut World| {
            let at = drop_point(world).unwrap_or(Vec3::ZERO);
            let parent = world
                .resource::<viber::scene::doc::SceneDoc>()
                .selector(&NodeRef::root(0));
            let op = match viber::scene::catalog::glb_drop(
                world.resource::<viber::scene::doc::SceneDoc>(),
                &parent,
                &path,
                [at.x, at.y, at.z],
            ) {
                Ok(op) => op,
                Err(error) => {
                    warn!("drop {path}: {error:#}");
                    return;
                }
            };
            let name = std::path::Path::new(&path)
                .file_stem()
                .and_then(|stem| stem.to_str())
                .unwrap_or("glb");
            world
                .resource_mut::<PendingEdits>()
                .push_selecting_inserted(format!("drop {name}"), vec![op]);
            let lang = *world.resource::<crate::ui::strings::Lang>();
            let label = format!(
                "{}: {name}",
                crate::ui::strings::t(lang, crate::ui::strings::StrKey::DropGlb)
            );
            world.resource_mut::<crate::scene::EditStatus>().0 = label;
        });
    }
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
