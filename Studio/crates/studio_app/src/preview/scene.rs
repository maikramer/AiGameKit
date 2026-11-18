//! Cena de preview: ambiente, spawn do GLB e animação.

use std::time::Duration;

use bevy::{
    asset::RenderAssetUsages,
    mesh::{Indices, PrimitiveTopology, skinning::SkinnedMesh},
    prelude::*,
    world_serialization::{WorldAssetRoot, WorldInstanceReady},
};

use super::{
    Loaded, PreviewCommand, PreviewState, ReadyScene,
    camera::{OrbitCamera, PreviewCamera},
};

#[derive(Component)]
pub struct PreviewGround;

pub fn setup_environment(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    commands.spawn((
        PreviewCamera,
        Camera3d::default(),
        Msaa::Sample4,
        Transform::from_xyz(3.0, 2.0, 5.0).looking_at(Vec3::ZERO, Vec3::Y),
    ));

    // Key light com sombras.
    commands.spawn((
        DirectionalLight {
            illuminance: 9_000.0,
            shadow_maps_enabled: true,
            ..default()
        },
        Transform::from_rotation(Quat::from_euler(EulerRot::XYZ, -0.85, 0.65, 0.0)),
    ));
    // Fill frio.
    commands.spawn((
        DirectionalLight {
            illuminance: 2_200.0,
            color: Color::srgb(0.65, 0.75, 1.0),
            ..default()
        },
        Transform::from_rotation(Quat::from_euler(EulerRot::XYZ, -0.4, -1.9, 0.0)),
    ));
    // Rim quente.
    commands.spawn((
        DirectionalLight {
            illuminance: 3_500.0,
            color: Color::srgb(1.0, 0.78, 0.55),
            ..default()
        },
        Transform::from_rotation(Quat::from_euler(EulerRot::XYZ, -0.2, 2.6, 0.0)),
    ));

    commands.spawn((
        PreviewGround,
        Mesh3d(meshes.add(Plane3d::default().mesh().size(240.0, 240.0))),
        MeshMaterial3d(materials.add(StandardMaterial {
            base_color: Color::srgb(0.055, 0.06, 0.07),
            perceptual_roughness: 0.96,
            metallic: 0.0,
            ..default()
        })),
        Transform::from_xyz(0.0, -0.01, 0.0),
    ));
}

pub fn poll_scene_load(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    gltfs: Res<Assets<Gltf>>,
    mut graphs: ResMut<Assets<AnimationGraph>>,
    mut state: ResMut<PreviewState>,
    mut camera: ResMut<OrbitCamera>,
) {
    let Some(pending) = state.pending_scene.as_ref() else {
        return;
    };
    if !asset_server.is_loaded_with_dependencies(&pending.handle) {
        return;
    }
    let Some(gltf) = gltfs.get(&pending.handle) else {
        return;
    };
    let Some(scene) = gltf.default_scene.clone() else {
        let error = "GLB sem cena default".to_string();
        tracing::warn!("{error}");
        state.error = Some(error);
        state.pending_scene = None;
        return;
    };
    tracing::debug!(
        "scene ready: clips={} default_scene={:?}",
        gltf.named_animations.len(),
        scene.id()
    );

    let mut clips: Vec<(String, Handle<AnimationClip>)> = gltf
        .named_animations
        .iter()
        .map(|(name, handle)| (name.to_string(), handle.clone()))
        .collect();
    clips.sort_by(|a, b| a.0.cmp(&b.0));
    let names: Vec<String> = clips.iter().map(|(name, _)| name.clone()).collect();
    let (graph, indices) = if clips.is_empty() {
        (None, Vec::new())
    } else {
        let (graph, indices) =
            AnimationGraph::from_clips(clips.into_iter().map(|(_, handle)| handle));
        (Some(graphs.add(graph)), indices)
    };

    let pending = state.pending_scene.take().expect("pending");
    if let Some(old) = state.loaded.take() {
        commands.entity(old.root).despawn();
    }
    if let Some(bounds) = state.bounds {
        camera.fit(bounds);
        tracing::debug!(
            "fit bounds center={:?} distance={}",
            bounds.center(),
            camera.distance
        );
    }
    state.frame_pending = true;

    state.ready_scene = Some(ReadyScene {
        artifact: pending.artifact,
        source: pending.source,
        clips: names,
        indices,
        graph,
    });

    commands
        .spawn((
            WorldAssetRoot(scene),
            Transform::default(),
            Visibility::default(),
        ))
        .observe(on_scene_ready);
}

#[allow(clippy::too_many_arguments)]
fn on_scene_ready(
    ready: On<WorldInstanceReady>,
    mut commands: Commands,
    children: Query<&Children>,
    players: Query<Entity, With<AnimationPlayer>>,
    mesh_query: Query<(&Mesh3d, Option<&SkinnedMesh>)>,
    material_query: Query<&MeshMaterial3d<StandardMaterial>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut state: ResMut<PreviewState>,
) {
    let root = ready.entity;
    let Some(scene) = state.ready_scene.take() else {
        tracing::warn!("WorldInstanceReady sem ready_scene");
        return;
    };
    tracing::debug!("scene spawned: root={:?}", root);
    let player = children
        .iter_descendants(root)
        .find(|entity| players.contains(*entity));
    if let (Some(player_entity), Some(graph)) = (player, scene.graph.clone()) {
        commands
            .entity(player_entity)
            .insert(AnimationGraphHandle(graph))
            .insert(AnimationTransitions::new());
    }

    // Ferramentas como o machado são folhas de uma só face; no viewport
    // queremos ver os dois lados.
    for entity in children.iter_descendants(root) {
        if let Ok(material) = material_query.get(entity) {
            if let Some(mut material) = materials.get_mut(&material.0) {
                material.cull_mode = None;
            }
        }
    }

    let wire_material = materials.add(StandardMaterial {
        base_color: Color::srgba(0.92, 0.94, 0.98, 0.55),
        unlit: true,
        alpha_mode: AlphaMode::Blend,
        ..default()
    });
    let mut wire_entities = Vec::new();
    for entity in children.iter_descendants(root) {
        let Ok((mesh3d, skinned)) = mesh_query.get(entity) else {
            continue;
        };
        if skinned.is_some() {
            // Os vértices do mesh estão em bind pose; um wireframe estático
            // não acompanha a skin. Fica só para malhas estáticas.
            continue;
        }
        let line_mesh = {
            let Some(mesh) = meshes.get(&mesh3d.0) else {
                continue;
            };
            build_wire_mesh(mesh)
        };
        let Some(line_mesh) = line_mesh else {
            continue;
        };
        let wire = commands
            .spawn((
                Mesh3d(meshes.add(line_mesh)),
                MeshMaterial3d(wire_material.clone()),
                Visibility::Hidden,
                ChildOf(entity),
            ))
            .id();
        wire_entities.push(wire);
    }

    state.loaded = Some(Loaded {
        artifact: scene.artifact,
        source: scene.source,
        root,
        player,
        clips: scene.clips,
        indices: scene.indices,
        active_clip: None,
        speed: 1.0,
        wire_entities,
    });
    state.playing = false;
}

fn build_wire_mesh(mesh: &Mesh) -> Option<Mesh> {
    let positions = mesh
        .attribute(Mesh::ATTRIBUTE_POSITION)?
        .as_float3()?
        .to_vec();
    let indices: Vec<u32> = match mesh.indices()? {
        Indices::U16(values) => values.iter().map(|value| *value as u32).collect(),
        Indices::U32(values) => values.to_vec(),
    };
    if indices.len() < 3 {
        return None;
    }
    let mut lines = Vec::with_capacity(indices.len() * 2);
    for triangle in indices.as_chunks::<3>().0 {
        for (a, b) in [(0, 1), (1, 2), (2, 0)] {
            lines.push(triangle[a]);
            lines.push(triangle[b]);
        }
    }
    let mut wire = Mesh::new(PrimitiveTopology::LineList, RenderAssetUsages::default());
    wire.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    wire.insert_indices(Indices::U32(lines));
    Some(wire)
}

pub fn apply_preview_requests(
    mut commands: ResMut<PreviewCommand>,
    mut state: ResMut<PreviewState>,
    mut camera: ResMut<OrbitCamera>,
    mut players: Query<&mut AnimationPlayer>,
    mut transitions: Query<&mut AnimationTransitions>,
    mut visibilities: Query<&mut Visibility>,
) {
    if let Some(turntable) = commands.turntable.take() {
        camera.turntable = turntable;
    }
    if let Some(show_grid) = commands.show_grid.take() {
        state.show_grid = show_grid;
    }
    if commands.frame || state.frame_pending {
        if let Some(bounds) = state.bounds {
            camera.fit(bounds);
        }
        commands.frame = false;
        state.frame_pending = false;
    }

    let mut playing = state.playing;
    if let Some(loaded) = state.loaded.as_mut() {
        if let Some(on) = commands.wireframe.take() {
            for entity in &loaded.wire_entities {
                if let Ok(mut visibility) = visibilities.get_mut(*entity) {
                    *visibility = if on {
                        Visibility::Inherited
                    } else {
                        Visibility::Hidden
                    };
                }
            }
        }
        let Some(player_entity) = loaded.player else {
            commands.set_clip = None;
            commands.toggle_play = false;
            state.playing = playing;
            return;
        };
        let Ok(mut player) = players.get_mut(player_entity) else {
            state.playing = playing;
            return;
        };

        let requested = commands
            .set_clip
            .take()
            .and_then(|name| loaded.clips.iter().position(|clip| *clip == name));
        let desired = requested
            .or(loaded.active_clip)
            .or_else(|| default_clip_index(&loaded.clips));

        if let Some(index) = desired {
            if loaded.active_clip != Some(index) {
                if let Ok(mut transition) = transitions.get_mut(player_entity) {
                    transition
                        .play(
                            &mut player,
                            loaded.indices[index],
                            Duration::from_millis(180),
                        )
                        .repeat();
                } else {
                    player.play(loaded.indices[index]).repeat();
                }
                loaded.active_clip = Some(index);
                playing = true;
            }
        }

        if let Some(index) = loaded.active_clip {
            if let Some(animation) = player.animation_mut(loaded.indices[index]) {
                if animation.speed() != loaded.speed {
                    animation.set_speed(loaded.speed);
                }
            }
        }

        if commands.toggle_play {
            if let Some(index) = loaded.active_clip {
                if let Some(animation) = player.animation_mut(loaded.indices[index]) {
                    if animation.is_paused() {
                        animation.resume();
                        playing = true;
                    } else {
                        animation.pause();
                        playing = false;
                    }
                }
            }
            commands.toggle_play = false;
        }

        if let Some(speed) = commands.speed.take() {
            loaded.speed = speed;
            if let Some(index) = loaded.active_clip {
                if let Some(animation) = player.animation_mut(loaded.indices[index]) {
                    animation.set_speed(speed);
                }
            }
        }
    }
    state.playing = playing;
}

fn default_clip_index(clips: &[String]) -> Option<usize> {
    if clips.is_empty() {
        return None;
    }
    for wanted in ["idle", "walk", "run"] {
        if let Some(index) = clips
            .iter()
            .position(|clip| clip.eq_ignore_ascii_case(wanted))
        {
            return Some(index);
        }
    }
    Some(0)
}

pub fn draw_grid(
    mut gizmos: Gizmos,
    state: Res<PreviewState>,
    thumbs: Res<super::thumbs::ThumbnailState>,
) {
    if !state.show_grid || thumbs.active.is_some() {
        return;
    }
    let y = state.bounds.map(|bounds| bounds.min.y).unwrap_or(0.0);
    let minor = Color::srgba(0.38, 0.41, 0.46, 0.22);
    let major = Color::srgba(0.62, 0.65, 0.70, 0.40);
    for i in -24..=24 {
        let color = if i % 5 == 0 { major } else { minor };
        gizmos.line(
            Vec3::new(i as f32, y, -24.0),
            Vec3::new(i as f32, y, 24.0),
            color,
        );
        gizmos.line(
            Vec3::new(-24.0, y, i as f32),
            Vec3::new(24.0, y, i as f32),
            color,
        );
    }
    gizmos.line(
        Vec3::new(0.0, y, 0.0),
        Vec3::new(1.6, y, 0.0),
        Color::srgb(0.85, 0.25, 0.25),
    );
    gizmos.line(
        Vec3::new(0.0, y, 0.0),
        Vec3::new(0.0, y, 1.6),
        Color::srgb(0.25, 0.45, 0.95),
    );
}

pub fn move_ground(
    state: Res<PreviewState>,
    mut grounds: Query<&mut Transform, With<PreviewGround>>,
) {
    let y = state.bounds.map(|bounds| bounds.min.y).unwrap_or(0.0) - 0.002;
    for mut transform in &mut grounds {
        if (transform.translation.y - y).abs() > f32::EPSILON {
            transform.translation.y = y;
        }
    }
}
