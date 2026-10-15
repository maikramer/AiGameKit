//! Marcador 3D sobre os `<DialogueNPC>` de quest — o "!"/"?" dos RPG.
//!
//! O marcador lê o estado da quest com o mesmo id do diálogo
//! ([`crate::quests::QuestLog::status`]):
//!
//! * **disponível** (`not_taken`) → losango dourado com "!";
//! * **pronta a entregar** (`ready`) → losango dourado com "?";
//! * **ativa** / **concluída** / sem quest → escondido (o NPC não chama).
//!
//! É um billboard (encara sempre a câmara) que flutua devagar — lê-se de
//! longe e diz ao jogador onde vale a pena ir. A antiga esfera amarela era
//! igual em todos os estados (até em quests acabadas).

use bevy::light::NotShadowCaster;
use bevy::prelude::*;

use crate::quests::{QuestLog, QuestStatus};

/// Marcador de um NPC de quest: o id do diálogo (= id da quest) e a altura
/// autoral (`marker-height`).
#[derive(Component, Debug, Clone)]
pub struct QuestMarker3d {
    pub dialogue_id: String,
    pub height: f32,
}

/// Mesh + materiais partilhados (criados no 1.º marcador).
#[derive(Resource)]
struct MarkerAssets {
    quad: Handle<Mesh>,
    available: Handle<StandardMaterial>,
    ready: Handle<StandardMaterial>,
}

/// Lado do billboard (m).
const MARKER_SIZE: f32 = 0.85;
/// Flutuação: amplitude (m) e frequência (Hz).
const BOB_AMPLITUDE: f32 = 0.09;
const BOB_HZ: f32 = 0.55;

pub struct QuestMarkerPlugin;

impl Plugin for QuestMarkerPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Update,
            (attach_markers, drive_markers, face_player_system).chain(),
        );
    }
}

fn attach_markers(
    added: Query<Entity, Added<QuestMarker3d>>,
    assets: Option<Res<MarkerAssets>>,
    mut meshes: Option<ResMut<Assets<Mesh>>>,
    mut materials: Option<ResMut<Assets<StandardMaterial>>>,
    mut images: Option<ResMut<Assets<Image>>>,
    mut commands: Commands,
) {
    if added.is_empty() {
        return;
    }
    let (Some(meshes), Some(materials), Some(images)) = (
        meshes.as_deref_mut(),
        materials.as_deref_mut(),
        images.as_deref_mut(),
    ) else {
        return; // app headless sem render: o marcador fica só lógico
    };
    let handles = match assets {
        Some(assets) => (assets.quad.clone(), assets.available.clone()),
        None => {
            let quad = meshes.add(Rectangle::new(MARKER_SIZE, MARKER_SIZE));
            let mut material = |image: Image, materials: &mut Assets<StandardMaterial>| {
                materials.add(StandardMaterial {
                    base_color_texture: Some(images.add(image)),
                    emissive: LinearRgba::rgb(0.35, 0.28, 0.1),
                    unlit: true,
                    alpha_mode: AlphaMode::Blend,
                    double_sided: true,
                    cull_mode: None,
                    ..Default::default()
                })
            };
            let available = material(crate::hud::assets::quest_marker_image(), materials);
            let ready = material(quest_ready_image(), materials);
            commands.insert_resource(MarkerAssets {
                quad: quad.clone(),
                available: available.clone(),
                ready,
            });
            (quad, available)
        }
    };
    for entity in &added {
        commands.entity(entity).try_insert((
            Mesh3d(handles.0.clone()),
            MeshMaterial3d(handles.1.clone()),
            NotShadowCaster,
        ));
    }
}

/// Estado → visibilidade/glifo; billboard + flutuação.
#[allow(clippy::type_complexity)]
fn drive_markers(
    time: Res<Time>,
    log: Option<Res<QuestLog>>,
    vault: Option<Res<crate::economy::Vault>>,
    assets: Option<Res<MarkerAssets>>,
    cameras: Query<&GlobalTransform, (With<Camera3d>, Without<QuestMarker3d>)>,
    mut markers: Query<(
        &QuestMarker3d,
        &mut Transform,
        &mut Visibility,
        Option<&mut MeshMaterial3d<StandardMaterial>>,
        Option<&ChildOf>,
    )>,
    parents: Query<&GlobalTransform>,
) {
    let camera_rotation = cameras
        .iter()
        .next()
        .map(|camera| camera.compute_transform().rotation);
    let t = time.elapsed_secs();
    for (marker, mut transform, mut visibility, material, parent) in &mut markers {
        let status = log.as_deref().and_then(|log| {
            log.def(&marker.dialogue_id)
                .map(|_| log.status(&marker.dialogue_id, vault.as_deref()))
        });
        let wanted = match status {
            Some(QuestStatus::NotTaken) => assets.as_deref().map(|a| a.available.clone()),
            Some(QuestStatus::Ready) => assets.as_deref().map(|a| a.ready.clone()),
            _ => None,
        };
        let Some(wanted) = wanted else {
            visibility.set_if_neq(Visibility::Hidden);
            continue;
        };
        visibility.set_if_neq(Visibility::Inherited);
        if let Some(mut material) = material
            && material.0 != wanted
        {
            material.0 = wanted;
        }
        let bob = (t * BOB_HZ * std::f32::consts::TAU).sin() * BOB_AMPLITUDE;
        transform.translation = Vec3::Y * (marker.height + bob);
        // Billboard: rotação MUNDO = a da câmara → local = pai⁻¹ × câmara.
        if let Some(rotation) = camera_rotation {
            let parent_rotation = parent
                .and_then(|p| parents.get(p.parent()).ok())
                .map(|g| g.compute_transform().rotation)
                .unwrap_or(Quat::IDENTITY);
            transform.rotation = parent_rotation.inverse() * rotation;
        }
    }
}

/// Dentro deste raio (m) o NPC de missão vira-se para o herói.
const FACE_RADIUS_M: f32 = 7.0;
/// Rapidez da viragem (1/s, low-pass).
const FACE_RATE: f32 = 6.0;

/// Rotação autoral do NPC (para onde volta quando o herói se afasta).
#[derive(Component, Debug, Clone, Copy)]
pub struct HomeRotation(pub Quat);

/// Os NPC de missão olham para quem se aproxima — uma estátua a falar para a
/// parede lia-se como bug. Roda a entidade do NPC (o pai do `<DialogueNPC>`)
/// e devolve-a à pose autoral quando o herói sai do raio.
#[allow(clippy::type_complexity)]
fn face_player_system(
    time: Res<Time>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    npcs: Query<&ChildOf, With<crate::recipes::spawn::DialogueNpc>>,
    mut bodies: Query<
        (
            Entity,
            &mut Transform,
            &GlobalTransform,
            Option<&HomeRotation>,
            Option<&ChildOf>,
        ),
        Without<crate::player::Player>,
    >,
    any_parent: Query<&GlobalTransform, Without<crate::recipes::spawn::DialogueNpc>>,
    mut commands: Commands,
) {
    let Some(hero) = players.iter().next().map(GlobalTransform::translation) else {
        return;
    };
    let k = 1.0 - (-FACE_RATE * time.delta_secs()).exp();
    for child_of in &npcs {
        let Ok((entity, mut transform, global, home, parent)) = bodies.get_mut(child_of.parent())
        else {
            continue;
        };
        let home = match home {
            Some(home) => home.0,
            None => {
                commands
                    .entity(entity)
                    .try_insert(HomeRotation(transform.rotation));
                transform.rotation
            }
        };
        let here = global.translation();
        let delta = (hero - here).with_y(0.0);
        let wanted = if delta.length() <= FACE_RADIUS_M && delta.length_squared() > 1e-4 {
            // Modelos olham +Z (convenção da pipeline): yaw = atan2(x, z),
            // levado ao referencial do pai.
            let world = Quat::from_rotation_y(delta.x.atan2(delta.z));
            let parent_rotation = parent
                .and_then(|p| any_parent.get(p.parent()).ok())
                .map(|g| g.compute_transform().rotation)
                .unwrap_or(Quat::IDENTITY);
            parent_rotation.inverse() * world
        } else {
            home
        };
        if transform.rotation.angle_between(wanted) > 1e-3 {
            transform.rotation = transform.rotation.slerp(wanted, k);
        }
    }
}

/// "?" de entrega: o mesmo losango do "!" com o glifo de pergunta.
pub(crate) fn quest_ready_image() -> Image {
    const GOLD: [u8; 4] = [232, 200, 96, 255];
    const INK: [u8; 4] = [43, 29, 16, 255];
    crate::hud::assets::supersampled_image(48, 4, |x, y| {
        let c = 24.0;
        let (dx, dy) = (x - c, y - c);
        let d = (dx.abs().powf(3.2) + dy.abs().powf(3.2)).powf(1.0 / 3.2);
        let edge = 17.5;
        if d > edge + 1.0 {
            return [0, 0, 0, 0];
        }
        // Gancho: anel (centro (0,-4), r≈5.5) do lado de cima e da direita.
        let (ax, ay) = (dx, dy + 4.0);
        let r = (ax * ax + ay * ay).sqrt();
        let ring = (r - 5.5).abs() < 2.3 && (ay < 0.5 || ax > 0.0) && !(ax < -1.0 && ay > 0.5);
        let stem = dx.abs() < 2.4 && (1.0..=4.5).contains(&dy);
        let dot = dx * dx + (dy - 9.0) * (dy - 9.0) < 2.9 * 2.9;
        if ring || stem || dot || d > edge - 1.6 {
            return INK;
        }
        GOLD
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_glyph_differs_from_available() {
        let ready = quest_ready_image();
        let available = crate::hud::assets::quest_marker_image();
        assert_eq!(ready.width(), 48);
        assert_ne!(ready.data, available.data);
    }
}
