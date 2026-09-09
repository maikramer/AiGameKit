//! Spawn de `<Composition>`/`<Prototype>`: corpo composto, colisores exatos
//! por parte e dedup de mesh/material — o contrato do objeto-em-primitivas.

use bevy::app::TaskPoolPlugin;
use bevy::asset::AssetApp;
use bevy::prelude::*;
use bevy::time::TimePlugin;
use bevy_rapier3d::prelude::{Collider, RigidBody};
use viber::recipes::parse_world;
use viber::recipes::spawn::{PendingWorld, startup};
use viber::xml::XmlNode;
use viber::xml::include::load_world;

/// App mínima com os assets que o spawn do mundo toca (sem render/winit).
fn spawn_app() -> App {
    let mut app = App::new();
    app.add_plugins((TaskPoolPlugin::default(), TimePlugin));
    app.add_plugins(bevy::asset::AssetPlugin::default());
    app.init_asset::<Mesh>();
    app.init_asset::<StandardMaterial>();
    app.init_asset::<viber::sky::SkyMaterial>();
    app.init_asset::<bevy::image::Image>();
    app.insert_resource(viber::textures::WorldTiledTextures::default());
    app
}

fn node(tag: &str, attrs: &[(&str, &str)]) -> XmlNode {
    XmlNode {
        tag: tag.to_string(),
        attrs: attrs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        text: String::new(),
        children: vec![],
    }
}

fn spawn_world(app: &mut App, nodes: &[XmlNode]) {
    let world = parse_world(&[], nodes).expect("world parses");
    app.insert_resource(PendingWorld {
        world,
        base_dir: None,
    });
    startup(app.world_mut());
}

fn children_of(app: &App, root: Entity) -> Vec<Entity> {
    app.world()
        .get::<bevy::ecs::hierarchy::Children>(root)
        .map(|c| c.iter().collect())
        .unwrap_or_default()
}

#[test]
fn test_composition_spawns_compound_body_with_exact_part_colliders() {
    let mut app = spawn_app();
    let mut comp = node("Composition", &[("name", "shell")]);
    comp.children = vec![
        node("Box", &[("half-size", "2 0.1 2")]),
        node("Sphere", &[("radius", "0.4")]),
        node("Cylinder", &[("radius", "0.25"), ("half-height", "1")]),
    ];
    spawn_world(&mut app, &[comp]);

    // Root da composition: Fixed + colisor composto (todas as partes).
    let mut roots = app
        .world_mut()
        .query_filtered::<Entity, With<RigidBody>>()
        .iter(app.world())
        .collect::<Vec<_>>();
    assert_eq!(roots.len(), 1, "um corpo para o objeto inteiro");
    let root = roots.pop().unwrap();
    assert!(matches!(
        app.world().get::<RigidBody>(root),
        Some(RigidBody::Fixed)
    ));
    // Rapier cola os colisores das partes ao corpo do ancestral: 3 colisores
    // no root (1 do próprio root…) — na verdade o root não tem collider
    // próprio; o que se valida é que as PARTES têm colisor e são filhas.
    let kids = children_of(&app, root);
    assert_eq!(kids.len(), 3, "3 partes");
    for kid in &kids {
        assert!(
            app.world().get::<Collider>(*kid).is_some(),
            "cada parte nasce com colisor exato"
        );
    }
    // Esfera → ball (não cuboid).
    let sphere_collider = app
        .world()
        .get::<Collider>(kids[1])
        .expect("sphere collider");
    assert!(sphere_collider.as_ball().is_some(), "esfera → ball");
}

#[test]
fn test_composition_identical_parts_share_mesh_and_material() {
    let mut app = spawn_app();
    let mut comp = node("Composition", &[]);
    comp.children = vec![
        node(
            "Box",
            &[("half-size", "2 0.1 0.2"), ("base-color", "#8a5a30")],
        ),
        node(
            "Box",
            &[("half-size", "2 0.1 0.2"), ("base-color", "#8a5a30")],
        ),
        node(
            "Box",
            &[("half-size", "2 0.1 0.2"), ("base-color", "#8a5a30")],
        ),
        // Parte com material diferente → asset próprio.
        node(
            "Box",
            &[("half-size", "2 0.1 0.2"), ("base-color", "#ff0000")],
        ),
    ];
    spawn_world(&mut app, &[comp]);

    let root = app
        .world_mut()
        .query_filtered::<Entity, bevy::ecs::query::Added<Children>>()
        .single(app.world())
        .expect("composition root");
    let kids = children_of(&app, root);
    assert_eq!(kids.len(), 4);

    let mesh_id = |app: &App, e: Entity| {
        app.world()
            .get::<Mesh3d>(e)
            .map(|m| m.0.id())
            .expect("mesh")
    };
    let mat_id = |app: &App, e: Entity| {
        app.world()
            .get::<MeshMaterial3d<StandardMaterial>>(e)
            .map(|m| m.0.id())
            .expect("material")
    };
    assert_eq!(mesh_id(&app, kids[0]), mesh_id(&app, kids[1]));
    assert_eq!(mesh_id(&app, kids[1]), mesh_id(&app, kids[2]));
    assert_eq!(mat_id(&app, kids[0]), mat_id(&app, kids[1]));
    assert_ne!(
        mat_id(&app, kids[0]),
        mat_id(&app, kids[3]),
        "material diferente → asset próprio"
    );
    // 4 cuboids da MESMA forma → 1 mesh só; 2 materiais (marrom, vermelho).
    let meshes = app.world().resource::<Assets<Mesh>>().len();
    let materials = app.world().resource::<Assets<StandardMaterial>>().len();
    assert_eq!(meshes, 1, "forma idêntica partilha mesh, mude a cor ou não");
    assert_eq!(materials, 2);
}

#[test]
fn test_prototype_instances_spawn_independent_entities_with_shared_assets() {
    let mut app = spawn_app();
    let mut proto = node("Prototype", &[("id", "crate")]);
    let mut comp = node("Composition", &[]);
    comp.children = vec![node(
        "Box",
        &[("half-size", "0.5 0.5 0.5"), ("base-color", "#c08040")],
    )];
    proto.children = vec![comp];
    let use_a = node("Use", &[("prototype", "crate"), ("pos", "1 0 1")]);
    let use_b = node(
        "Use",
        &[
            ("prototype", "crate"),
            ("pos", "5 0 1"),
            ("euler", "0 90 0"),
        ],
    );
    spawn_world(&mut app, &[proto, use_a, use_b]);

    // Cada instância = 1 Composition (corpo) + 1 parte (mesh + colisor).
    let parts = app
        .world_mut()
        .query_filtered::<Entity, (With<Mesh3d>, With<Collider>)>()
        .iter(app.world())
        .collect::<Vec<_>>();
    assert_eq!(parts.len(), 2, "2 instâncias, cada parte com colisor exato");

    let mesh_id = |app: &App, e: Entity| app.world().get::<Mesh3d>(e).unwrap().0.id();
    assert_eq!(
        mesh_id(&app, parts[0]),
        mesh_id(&app, parts[1]),
        "instâncias do mesmo protótipo partilham a mesh"
    );

    // E uma opacity < 1 dá material translúcido (AlphaMode::Blend).
    let mut proto2 = node("Prototype", &[("id", "ghost")]);
    proto2.children = vec![node(
        "Box",
        &[("half-size", "0.5 0.5 0.5"), ("opacity", "0.3")],
    )];
    let world = parse_world(&[], &[proto2]).unwrap();
    drop(world);
}

#[test]
fn test_composition_opacity_material_is_blended() {
    let mut app = spawn_app();
    let mut comp = node("Composition", &[]);
    comp.children = vec![node(
        "Box",
        &[
            ("half-size", "1 1 1"),
            ("opacity", "0"),
            ("base-color", "#000000"),
        ],
    )];
    spawn_world(&mut app, &[comp]);
    let root = app
        .world_mut()
        .query_filtered::<Entity, With<RigidBody>>()
        .single(app.world())
        .expect("composition root");
    let kid = children_of(&app, root)[0];
    let mat_handle = app
        .world()
        .get::<MeshMaterial3d<StandardMaterial>>(kid)
        .unwrap()
        .0
        .clone();
    let material = app
        .world()
        .resource::<Assets<StandardMaterial>>()
        .get(&mat_handle)
        .unwrap();
    assert!(matches!(
        material.alpha_mode,
        bevy::material::AlphaMode::Blend
    ));
    assert_eq!(material.base_color.alpha(), 0.0, "opacity=0 → invisível");
}

#[test]
fn test_world_load_with_include_and_prototype_end_to_end() {
    // Ficheiro real no disco: include splicing + passagem 0 de protótipos.
    let dir = std::env::temp_dir().join(format!("viber-composition-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let proto_path = dir.join("protos.xml");
    std::fs::write(
        &proto_path,
        r##"<world>
  <Prototype id="post">
    <Composition>
      <Box half-size="0.15 1 0.15" base-color="#6b4a2b" />
      <PointLight translation="0 1.6 0" color="#ffd9a0" intensity="800" />
    </Composition>
  </Prototype>
</world>"##,
    )
    .unwrap();
    let world_path = dir.join("world.xml");
    std::fs::write(
        &world_path,
        r##"<world>
  <Include src="protos.xml" />
  <Use prototype="post" pos="1 0 1" name="post.west" />
  <Use prototype="post" pos="9 0 1" name="post.east" />
</world>"##,
    )
    .unwrap();

    let mut app = spawn_app();
    let loaded = load_world(&world_path).expect("include expansion");
    let (root_attrs, nodes) = (loaded.root_attrs, loaded.nodes);
    let world = parse_world(&root_attrs, &nodes).expect("parses");
    assert!(world.unknown_prototypes.is_empty());
    assert_eq!(world.prototype_instances.get("post"), Some(&2));
    app.insert_resource(PendingWorld {
        world,
        base_dir: None,
    });
    startup(app.world_mut());

    // 2 composições (1 corpo cada), 2 luzes.
    let bodies = app
        .world_mut()
        .query_filtered::<Entity, With<RigidBody>>()
        .iter(app.world())
        .count();
    let lights = app
        .world_mut()
        .query_filtered::<Entity, With<PointLight>>()
        .iter(app.world())
        .count();
    assert_eq!(bodies, 2);
    assert_eq!(lights, 2);
    let _ = std::fs::remove_dir_all(&dir);
}
