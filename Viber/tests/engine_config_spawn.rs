//! `EngineConfig` tags (`<NavMesh>`, `<SpawnGate>`, `<ProjectileTemplate>`…):
//! every element of the world survives the spawn, side by side.

use bevy::app::TaskPoolPlugin;
use bevy::asset::AssetApp;
use bevy::prelude::*;
use bevy::time::TimePlugin;
use viber::recipes::parse_world;
use viber::recipes::spawn::{PendingWorld, startup};
use viber::worldsys::EngineConfigs;
use viber::xml::XmlNode;

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

#[test]
fn test_every_engine_config_element_survives_the_spawn() {
    let mut app = spawn_app();
    let nodes = [
        node("ProjectileTemplate", &[("id", "arrow"), ("speed", "16")]),
        node(
            "SpawnGate",
            &[("target-entity", "player"), ("y-fallback", "150")],
        ),
        node("ProjectileTemplate", &[("id", "bolt"), ("Damage", "4")]),
        node("NavMesh", &[("agent-radius", "0.6")]),
        node("PostFxDebugToggle", &[]),
    ];
    let world = parse_world(&[], &nodes).expect("world parses");
    app.insert_resource(PendingWorld {
        world,
        base_dir: None,
    });
    startup(app.world_mut());

    let configs = app.world().resource::<EngineConfigs>();
    assert_eq!(configs.list.len(), 5, "nenhuma tag sobrescreve outra");
    let ids: Vec<_> = configs
        .all("projectiletemplate")
        .filter_map(|c| c.attr("id"))
        .collect();
    assert_eq!(ids, ["arrow", "bolt"], "ordem do documento");
    let bolt = configs.all("projectiletemplate").nth(1).unwrap();
    assert_eq!(bolt.f32_attr("damage"), Some(4.0), "attrs em minúsculas");
    assert_eq!(
        configs
            .first("spawngate")
            .and_then(|c| c.f32_attr("y-fallback")),
        Some(150.0)
    );
    assert_eq!(
        configs
            .first("navmesh")
            .and_then(|c| c.f32_attr("agent-radius")),
        Some(0.6)
    );
    assert!(configs.first("postfxdebugtoggle").is_some());
    assert!(configs.first("adaptivequality").is_none());
}

#[test]
fn test_world_without_engine_config_still_has_an_empty_registry() {
    let mut app = spawn_app();
    let world = parse_world(&[], &[node("Box", &[])]).expect("world parses");
    app.insert_resource(PendingWorld {
        world,
        base_dir: None,
    });
    startup(app.world_mut());
    assert!(app.world().resource::<EngineConfigs>().list.is_empty());
}
