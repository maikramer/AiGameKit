//! World-anchored NPC name tags: a pooled set of pills reassigned every
//! frame to the nearest NPCs, projected through the main camera.

use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::DialogueNpc;

/// A world-anchored NPC name tag pill from the pooled set.
#[derive(Component)]
pub struct NameTag;

/// How many name-tag pills are kept in the pool (reassigned per frame).
pub const NAME_TAG_POOL: usize = 8;
/// Name tags show for NPCs between these distance bounds (meters).
pub const NAME_TAG_MIN_M: f32 = 2.0;
pub const NAME_TAG_MAX_M: f32 = 60.0;

/// Screen position (UI px, top-left origin) for a world point, if the camera
/// sees it (in front and within the viewport). `world_to_viewport` already
/// returns top-left-origin coordinates (bevy flips Y internally), so the
/// result plugs straight into UI `left`/`top`.
fn world_to_ui(
    camera: &Camera,
    camera_global: &GlobalTransform,
    world_pos: Vec3,
) -> Option<(f32, f32)> {
    // In front of the camera?
    let to_point = world_pos - camera_global.translation();
    if to_point.dot(camera_global.forward().as_vec3()) <= 0.0 {
        return None;
    }
    camera
        .world_to_viewport(camera_global, world_pos)
        .ok()
        .map(|vp| (vp.x, vp.y))
}

/// Reassign the pooled name-tag pills to the nearest NPCs: "<name> <d> m".
#[allow(clippy::type_complexity)]
pub fn hud_nametags_update(
    cameras: Query<(&Camera, &GlobalTransform), Without<Player>>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc, Option<&Name>)>,
    mut tags: Query<(&mut Node, &mut Visibility, &Children), With<NameTag>>,
    mut texts: Query<&mut Text>,
) {
    let Ok((camera, camera_global)) = cameras.single() else {
        return;
    };
    let Ok(player) = players.single() else {
        return;
    };
    let player_pos = player.translation();
    let mut candidates: Vec<(f32, Vec3, String)> = npcs
        .iter()
        .filter_map(|(global, npc, name)| {
            let pos = global.translation();
            let dist = pos.distance(player_pos);
            (NAME_TAG_MIN_M..NAME_TAG_MAX_M).contains(&dist).then(|| {
                let label = name
                    .map(|n| n.to_string())
                    .unwrap_or_else(|| npc.dialogue_id.clone());
                (dist, pos, label)
            })
        })
        .collect();
    candidates.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));

    for (index, (mut node, mut visibility, children)) in tags.iter_mut().enumerate() {
        match candidates.get(index) {
            Some(&(dist, pos, ref label_text)) => {
                // Anchor ~2 m above the NPC's feet (head height).
                if let Some((x, y)) = world_to_ui(camera, camera_global, pos + Vec3::Y * 2.1) {
                    node.left = Val::Px(x);
                    node.top = Val::Px(y);
                    *visibility = Visibility::Visible;
                    if let Some(child) = children.first() {
                        if let Ok(mut text) = texts.get_mut(*child) {
                            let next = format!("{label_text} {} m", dist.round() as i32);
                            if text.0 != next {
                                text.0 = next;
                            }
                        }
                    }
                } else {
                    *visibility = Visibility::Hidden;
                }
            }
            None => *visibility = Visibility::Hidden,
        }
    }
}
