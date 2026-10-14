//! Seleção e picking de entidades para o ViberEditor: `viber.pick`
//! (clique na janela → ray → instância) e `viber.select` (highlight AABB
//! via gizmos). As entidades trocam-se em formato `"NvG"` — o mesmo do
//! cliente fork (ver [`super::fork_compat`]).
//!
//! Picking: o clique chega em coordenadas da JANELA do jogo (o editor mapeia
//! as do preview); a [`EditorCamera`] faz o unproject (`viewport_to_world`) e
//! o ray do Rapier resolve o hit. Os colliders vivem em FILHOS anónimos
//! ("collider"), portanto o hit sobe até à raiz nomeada da instância
//! (parando antes dos grupos `spawn/…` do mundo estático).

use bevy::camera::primitives::Aabb;
use bevy::ecs::system::In;
use bevy::prelude::*;
use bevy::remote::BrpResult;
use bevy::shape::Aabb3d;
use serde::Deserialize;
use serde_json::{Value, json};

use super::fork_compat::{entity_bits_to_string, entity_string_to_bits};
use super::invalid;

/// Entidade selecionada pelo editor (`viber.select`).
#[derive(Debug, Default, Resource)]
pub struct SelectedEntity(pub Option<Entity>);

/// `viber.pick {x, y}` — clique em coordenadas da janela do jogo →
/// `{hit, entity?, root?, name?, point?}`. `entity` é o collider atingido,
/// `root`/`name` a instância nomeada que o contém (o que o editor seleciona).
pub fn pick_method(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        max_toi: Option<f32>,
    }
    let params: Params = serde_json::from_value(params.0.unwrap_or(Value::Null))
        .map_err(|error| invalid(format!("pick: {error}")))?;

    // Unproject pela câmara de editor (sem ela o pick não faz sentido — o
    // jogo usa a terceira-pessoa, não a órbita do editor).
    let ray = {
        let Ok((camera, camera_global)) = world
            .query_filtered::<(&Camera, &GlobalTransform), With<crate::editor_camera::EditorCamera>>()
            .single(world)
        else {
            return Err(invalid(
                "pick: sem câmara de editor (`--camera free`)".into(),
            ));
        };
        camera.viewport_to_world(camera_global, Vec2::new(params.x, params.y))
    };
    let Ok(ray) = ray else {
        return Ok(json!({ "hit": false }));
    };
    let mut out = super::raycast_json(world, ray.origin, *ray.direction, params.max_toi, true)?;
    // Passe de AABB das instâncias: o collider de física é exato mas pode
    // cobrir só PARTE do asset (o precompute trimesh de um dos meshes do
    // glTF — medido: tronco sem a copa); o AABB de render é o que o
    // utilizador VÊ. Ganha o hit mais próximo entre os dois.
    let max_toi = params.max_toi.unwrap_or(100.0).max(0.0);
    let aabb_hit = pick_instance_aabb(world, ray.origin, *ray.direction, max_toi);
    let rapier_toi = out.get("toi").and_then(Value::as_f64).map(|toi| toi as f32);
    let use_aabb = match (aabb_hit, rapier_toi) {
        (Some((_, aabb_toi)), Some(rapier_toi)) => aabb_toi < rapier_toi,
        (Some(_), None) => true,
        (None, _) => false,
    };
    if let Some((entity, toi)) = aabb_hit
        && use_aabb
    {
        let name = world.get::<Name>(entity).map(|name| name.to_string());
        let point = ray.origin + *ray.direction * toi;
        out = json!({
            "hit": true,
            "entity": entity.to_bits(),
            "root": entity_bits_to_string(entity.to_bits())
                .map(Value::String)
                .unwrap_or_else(|| json!(entity.to_bits())),
            "toi": toi,
            "point": [point.x, point.y, point.z],
            "via": "aabb",
        });
        if let Some(name) = name {
            out["name"] = json!(name);
        }
        return Ok(out);
    }
    if out.get("hit") != Some(&json!(true)) {
        return Ok(out);
    }
    // Sobe do collider à instância nomeada.
    let bits = out["entity"].as_u64().unwrap_or_default();
    let Some(hit) = Entity::try_from_bits(bits) else {
        return Err(invalid("pick: entidade inválida".into()));
    };
    let (root, name) = named_root(world, hit);
    out["root"] = match entity_bits_to_string(root.to_bits()) {
        Some(text) => json!(text),
        None => json!(root.to_bits()),
    };
    if let Some(name) = name {
        out["name"] = json!(name);
    }
    Ok(out)
}

/// `viber.select {entity?}` — seleciona (entidade em "NvG") ou limpa (sem
/// `entity`). A seleção desenha o AABB da instância via gizmos.
pub fn select_method(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        entity: Option<String>,
    }
    let params: Params = serde_json::from_value(params.0.unwrap_or(Value::Null))
        .map_err(|error| invalid(format!("select: {error}")))?;
    // Resolver ANTES do resource_mut (borrow do World).
    let selected = params
        .entity
        .as_deref()
        .and_then(entity_string_to_bits)
        .and_then(Entity::try_from_bits)
        .filter(|entity| world.get_entity(*entity).is_ok());
    world.resource_mut::<SelectedEntity>().0 = selected;
    let name = selected.and_then(|entity| world.get::<Name>(entity).map(|name| name.to_string()));
    Ok(json!({
        "selected": selected
            .and_then(|entity| entity_bits_to_string(entity.to_bits()))
            .map(Value::String)
            .unwrap_or(Value::Null),
        "name": name,
    }))
}

/// O hit sobe pela hierarquia até à raiz nomeada da INSTÂNCIA: o collider é
/// filho anónimo e o grupo do spawner tem nome `spawn/…` — ambos são saltados.
pub fn named_root(world: &World, hit: Entity) -> (Entity, Option<String>) {
    let mut best = hit;
    let mut best_name = world.get::<Name>(hit).map(|name| name.to_string());
    let mut current = Some(hit);
    while let Some(entity) = current
        && let Some(child_of) = world.get::<ChildOf>(entity)
    {
        let parent = child_of.0;
        let parent_name = world.get::<Name>(parent).map(|name| name.to_string());
        // Grupos de spawner (`spawn/…`) não são instâncias — para aqui.
        if parent_name
            .as_deref()
            .is_some_and(|name| name.starts_with("spawn/"))
        {
            break;
        }
        if let Some(name) = parent_name {
            best = parent;
            best_name = Some(name);
        }
        current = Some(parent);
    }
    (best, best_name)
}

/// Highlight da seleção: AABB da instância (a raiz nomeada) em amarelo.
pub fn selection_gizmos(
    selection: Res<SelectedEntity>,
    globals: Query<&GlobalTransform>,
    aabbs: Query<&Aabb>,
    children: Query<&Children>,
    mut gizmos: Gizmos,
) {
    let Some(entity) = selection.0 else {
        return;
    };
    let get_global = |entity| globals.get(entity).ok().copied();
    let get_aabb = |entity| aabbs.get(entity).ok().copied();
    let get_children = |entity: Entity| children.get(entity).ok().map(|kids| kids.iter().collect());
    let Some(world_aabb) = world_aabb(entity, &get_global, &get_aabb, &get_children) else {
        return;
    };
    gizmos.aabb_3d(
        world_aabb,
        Transform::IDENTITY,
        bevy::color::palettes::basic::YELLOW,
    );
}

/// AABB em espaço-mundo da entidade e dos descendentes (meshes têm `Aabb`
/// local; fold dos 8 cantos transformados). Getters por closure para ser
/// chamável de sistemas E de testes (Query vs QueryState).
fn world_aabb(
    entity: Entity,
    globals: &impl Fn(Entity) -> Option<GlobalTransform>,
    aabbs: &impl Fn(Entity) -> Option<Aabb>,
    children: &impl Fn(Entity) -> Option<Vec<Entity>>,
) -> Option<Aabb3d> {
    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    let mut any = false;
    let mut stack = vec![entity];
    while let Some(entity) = stack.pop() {
        if let (Some(global), Some(aabb)) = (globals(entity), aabbs(entity)) {
            let center = aabb.center;
            let half = aabb.half_extents;
            for corner in [
                Vec3A::new(-1.0, -1.0, -1.0),
                Vec3A::new(1.0, -1.0, -1.0),
                Vec3A::new(-1.0, 1.0, -1.0),
                Vec3A::new(1.0, 1.0, -1.0),
                Vec3A::new(-1.0, -1.0, 1.0),
                Vec3A::new(1.0, -1.0, 1.0),
                Vec3A::new(-1.0, 1.0, 1.0),
                Vec3A::new(1.0, 1.0, 1.0),
            ] {
                let point = global.transform_point((center + half * corner).to_vec3());
                min = min.min(point);
                max = max.max(point);
            }
            any = true;
        }
        if let Some(kids) = children(entity) {
            stack.extend(kids);
        }
    }
    any.then(|| Aabb3d::from_min_max(min, max))
}

/// O AABB de uma entidade direto do `World` (par de pick — handlers BRP
/// têm `&mut World`, não queries de sistema).
pub fn world_aabb_of(entity: Entity, world: &World) -> Option<Aabb3d> {
    world_aabb(
        entity,
        &|entity| world.get::<GlobalTransform>(entity).copied(),
        &|entity| world.get::<Aabb>(entity).copied(),
        &|entity| {
            world
                .get::<Children>(entity)
                .map(|kids| kids.iter().collect())
        },
    )
}

/// Passe de picking por AABB sobre as instâncias do mundo (`WorldInstance` —
/// as instâncias baked/spawned pelo `world_serialization`). Sem índice
/// espacial: ~12 k instâncias × fold raso, ordem de grandeza de dezenas de
/// ms — um clique, não um frame.
fn pick_instance_aabb(
    world: &mut World,
    origin: Vec3,
    dir: Vec3,
    max_toi: f32,
) -> Option<(Entity, f32)> {
    let mut instances =
        world.query_filtered::<Entity, With<bevy::world_serialization::WorldInstance>>();
    instances
        .iter(world)
        .filter_map(|entity| {
            let aabb = world_aabb_of(entity, world)?;
            let toi = ray_aabb(origin, dir, aabb)?;
            (toi <= max_toi).then_some((entity, toi))
        })
        .min_by(|a, b| a.1.total_cmp(&b.1))
}

/// Interseção raio↔AABB (slab test). `dir` DEVE estar normalizado; devolve
/// o `t` de entrada (0 se a origem está dentro).
pub(crate) fn ray_aabb(origin: Vec3, dir: Vec3, aabb: Aabb3d) -> Option<f32> {
    let mut t_enter = 0.0f32;
    let mut t_exit = f32::INFINITY;
    let min = aabb.min.to_vec3();
    let max = aabb.max.to_vec3();
    for axis in 0..3 {
        let (o, d) = (origin[axis], dir[axis]);
        if d.abs() < 1e-8 {
            if o < min[axis] || o > max[axis] {
                return None;
            }
            continue;
        }
        let (t1, t2) = ((min[axis] - o) / d, (max[axis] - o) / d);
        let (t1, t2) = if t1 <= t2 { (t1, t2) } else { (t2, t1) };
        t_enter = t_enter.max(t1);
        t_exit = t_exit.min(t2);
        if t_enter > t_exit {
            return None;
        }
    }
    Some(t_enter)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_named_root_skips_colliders_and_groups() {
        let mut world = World::new();
        let group = world
            .spawn((Name::new("spawn/village-trees"), Transform::default()))
            .with_children(|parent| {
                parent
                    .spawn((Name::new("inst-village-trees-0007"), Transform::default()))
                    .with_children(|instance| {
                        instance.spawn((Name::new("collider"), Transform::default()));
                    });
            })
            .id();
        let instance = world.get::<Children>(group).unwrap()[0];
        let collider = world.get::<Children>(instance).unwrap()[0];

        let (root, name) = named_root(&world, collider);
        assert_eq!(name.as_deref(), Some("inst-village-trees-0007"));
        assert_eq!(root, instance);

        // Hit direto na instância também resolve a si própria.
        let (root, name) = named_root(&world, instance);
        assert_eq!(name.as_deref(), Some("inst-village-trees-0007"));
        assert_eq!(root, instance);
    }

    #[test]
    fn test_named_root_without_names_falls_back_to_hit() {
        let mut world = World::new();
        let bare = world.spawn(Transform::default()).id();
        let (root, name) = named_root(&world, bare);
        assert_eq!(root, bare);
        assert_eq!(name, None);
    }

    #[test]
    fn test_ray_aabb_hits_and_misses() {
        let aabb = Aabb3d::from_min_max(Vec3::new(-1.0, -1.0, -1.0), Vec3::new(1.0, 1.0, 1.0));
        // De frente, +Z → entra em z=-1 (a 9 m da origem do ray).
        assert_eq!(
            ray_aabb(Vec3::new(0.0, 0.0, 8.0), Vec3::new(0.0, 0.0, -1.0), aabb),
            Some(7.0)
        );
        // Paralelo fora do slab → None.
        assert_eq!(
            ray_aabb(Vec3::new(5.0, 0.0, 8.0), Vec3::new(0.0, 0.0, -1.0), aabb),
            None
        );
        // Origem DENTRO → 0.
        assert_eq!(
            ray_aabb(Vec3::ZERO, Vec3::new(0.0, 0.0, -1.0), aabb),
            Some(0.0)
        );
        // Aponta ao contrário → None.
        assert_eq!(
            ray_aabb(Vec3::new(0.0, 0.0, 8.0), Vec3::new(0.0, 0.0, 1.0), aabb),
            None
        );
    }

    #[test]
    fn test_world_aabb_folds_children() {
        let mut world = World::new();
        let parent = world
            .spawn(Transform::default())
            .with_children(|kids| {
                // GlobalTransform explícito: mundo cru sem o sistema de
                // propagação — o fold lê o global tal como estiver.
                kids.spawn((
                    Transform::from_xyz(10.0, 0.0, 0.0),
                    GlobalTransform::from_translation(Vec3::new(10.0, 0.0, 0.0)),
                    Aabb {
                        center: Vec3A::ZERO,
                        half_extents: Vec3A::splat(1.0),
                    },
                ));
            })
            .id();
        let bounds = world_aabb(
            parent,
            &|entity| world.get::<GlobalTransform>(entity).copied(),
            &|entity| world.get::<Aabb>(entity).copied(),
            &|entity| {
                world
                    .get::<Children>(entity)
                    .map(|kids| kids.iter().collect())
            },
        )
        .unwrap();
        assert_eq!(bounds.min, Vec3A::new(9.0, -1.0, -1.0));
        assert_eq!(bounds.max, Vec3A::new(11.0, 1.0, 1.0));
    }
}
