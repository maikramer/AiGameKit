//! Testes headless do debug bridge: App mínima + bridge real sobre HTTP
//! (loopback) — sem janela, sem render. Os testes e2e correm num único
//! `#[test]` para não competir pela mesma porta.

use super::client::BridgeClient;
use super::*;
use bevy::MinimalPlugins;
use std::thread::JoinHandle;

const TEST_PORT: u16 = 35702;

/// Bombeia frames até a chamada responder (a resposta só é escrita quando
/// `app.update()` processa o pedido em RemoteLast).
fn settle(app: &mut App, handle: JoinHandle<Result<Value, String>>) -> Value {
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    handle.join().unwrap().expect("bridge call responde")
}

/// Variante para chamadas que DEVEM falhar (devolve a mensagem de erro BRP).
fn settle_err(app: &mut App, handle: JoinHandle<Result<Value, String>>) -> String {
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    handle.join().unwrap().expect_err("chamada devia falhar")
}

fn call_async(method: &'static str, params: Value) -> JoinHandle<Result<Value, String>> {
    call_async_on(TEST_PORT, method, params)
}

/// Chamada `viber.lua` síncrona para testes (bombeia frames até responder).
fn lua_call(app: &mut App, port: u16, code: &str) -> Value {
    let handle = call_async_on(port, METHOD_LUA, serde_json::json!({ "code": code }));
    settle(app, handle)
}

/// Variante com porta explícita — cada App de teste tem a sua, porque os
/// testes correm em paralelo no mesmo binário.
fn call_async_on(
    port: u16,
    method: &'static str,
    params: Value,
) -> JoinHandle<Result<Value, String>> {
    std::thread::spawn(move || {
        BridgeClient::localhost(port)
            .call(method, params)
            .map_err(|error| error.to_string())
    })
}

#[test]
fn test_bridge_end_to_end_headless() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(bevy::input::InputPlugin)
        .add_plugins(BridgePlugin { port: TEST_PORT });
    app.world_mut()
        .spawn((Name::new("hero"), Transform::default()));
    // Sem WindowPlugin (headless), registamos o message de cursor à mão.
    app.add_message::<CursorMoved>();
    app.update(); // Startup: liga o servidor HTTP

    // ping
    let pong = settle(&mut app, call_async(METHOD_PING, serde_json::json!({})));
    let _ = "ping responde";
    assert_eq!(pong["pong"], serde_json::json!(true));
    assert!(pong["version"].is_string());

    // tree: hero presente
    let tree = settle(&mut app, call_async(METHOD_TREE, serde_json::json!({})));
    let _ = "tree responde";
    let entries = tree.as_array().expect("tree é lista");
    assert!(
        entries
            .iter()
            .any(|n| n.get("name").and_then(Value::as_str) == Some("hero")),
        "hero na árvore: {tree}"
    );

    // input.key → evento KeyboardInput + ButtonInput
    settle(
        &mut app,
        call_async(METHOD_KEY, serde_json::json!({ "key": "KeyW" })),
    );
    let keyboard = app.world().resource::<Messages<KeyboardInput>>();
    assert!(
        keyboard.len() >= 2,
        "press+release enviados: {}",
        keyboard.len()
    );

    // input.text
    let text = settle(
        &mut app,
        call_async(METHOD_TEXT, serde_json::json!({ "text": "aB" })),
    );
    assert_eq!(text["chars"], serde_json::json!(2));

    // input.click + input.move → CursorMoved
    let click = call_async(METHOD_CLICK, serde_json::json!({ "x": 10.0, "y": 20.0 }));
    let mouse_move = call_async(METHOD_MOVE, serde_json::json!({ "x": 30.0, "y": 40.0 }));
    for _ in 0..600 {
        app.update();
        if click.is_finished() && mouse_move.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    click.join().unwrap().expect("input.click responde");
    mouse_move.join().unwrap().expect("input.move responde");
    // As escritas do cursor acontecem no frame em que cada pedido é drenado;
    // se click e move caírem em frames DIFERENTES, o duplo buffer do
    // `Messages` evicta a mais velha ao fim de DOIS swaps — contar aqui, com
    // as duas a ≤1 frame de distância, e só depois dar os frames do release.
    let cursor_count = app.world().resource::<Messages<CursorMoved>>().len();
    assert!(
        cursor_count >= 2,
        "CursorMoved de click+move: {cursor_count}"
    );
    // O clique é injectado no PreUpdate do frame SEGUINTE (press agora,
    // release depois — ver `deferred_mouse_release`): dá-lhe dois frames.
    app.update();
    app.update();

    // screenshot: pedido em fila fica pending sem render
    let shot = settle(
        &mut app,
        call_async(METHOD_SCREENSHOT, serde_json::json!({})),
    );
    let id = shot["id"].as_u64().expect("capture id");

    let status = settle(
        &mut app,
        call_async(METHOD_SCREENSHOT_STATUS, serde_json::json!({ "id": id })),
    );
    assert_eq!(status["status"], serde_json::json!("pending"));

    let error = settle_err(
        &mut app,
        call_async(METHOD_SCREENSHOT_STATUS, serde_json::json!({ "id": 99999 })),
    );
    assert!(error.contains("unknown capture id"), "erro: {error}");
}

#[test]
fn test_normalize_key_aliases() {
    use super::client::normalize_key;
    assert_eq!(normalize_key("w"), "KeyW");
    assert_eq!(normalize_key("7"), "Digit7");
    assert_eq!(normalize_key("space"), "Space");
    assert_eq!(normalize_key("esc"), "Escape");
    assert_eq!(normalize_key("up"), "ArrowUp");
    assert_eq!(normalize_key("ctrl"), "ControlLeft");
    assert_eq!(normalize_key("KeyW"), "KeyW");
    assert_eq!(normalize_key("F5"), "F5");
}

#[test]
fn test_keycode_for_char_mapping() {
    assert_eq!(keycode_for_char('a'), (KeyCode::KeyA, false));
    assert_eq!(keycode_for_char('A'), (KeyCode::KeyA, true));
    assert_eq!(keycode_for_char('5'), (KeyCode::Digit5, false));
    assert_eq!(keycode_for_char('!'), (KeyCode::Digit1, true));
    assert_eq!(keycode_for_char(' '), (KeyCode::Space, false));
}

/// Porta própria: não pode colidir com `TEST_PORT` do e2e do bridge nem com
/// outros testes Luau (correm em paralelo no mesmo binário).
const LUA_TEST_PORT: u16 = 35703;
const LUA2_TEST_PORT: u16 = 35704;
const LUA3_TEST_PORT: u16 = 35705;
const LUA4_TEST_PORT: u16 = 35706;

/// App headless com runtime Luau + bridge: base para os testes `viber.lua`.
fn lua_app(port: u16) -> App {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(crate::luau::LuauScriptPlugin::default())
        .add_plugins(BridgePlugin { port });
    // Assets para os testes de introspeção (sem AssetPlugin na app mínima).
    app.init_resource::<bevy::asset::Assets<bevy::mesh::Mesh>>();
    app.init_resource::<bevy::asset::Assets<bevy::pbr::StandardMaterial>>();
    app.world_mut().spawn((
        Name::new("player"),
        crate::player::Player::default(),
        Transform::from_xyz(0.0, 1.0, 0.0),
        crate::vitals::Health::default(),
        crate::vitals::Xp::default(),
    ));
    app.world_mut().spawn((
        Name::new("goblin"),
        Transform::from_xyz(5.0, 0.0, 0.0),
        crate::luau::LuaScriptRef {
            path: "ghost.lua".into(),
        },
    ));
    app.world_mut().spawn((
        Name::new("dummy"),
        Transform::from_xyz(0.0, 0.0, 50.0),
        crate::vitals::Health::default(),
    ));
    // Entidade "rich" com mesh + material + collider + corpo rígido: alvo
    // dos testes de introspeção (info/mesh/material/collider/components).
    let mut meshes = app
        .world_mut()
        .get_resource_mut::<bevy::asset::Assets<bevy::mesh::Mesh>>()
        .unwrap();
    let cube = meshes.add(bevy::mesh::Mesh::from(bevy::math::primitives::Cuboid::new(
        1.0, 1.0, 1.0,
    )));
    let mut materials = app
        .world_mut()
        .get_resource_mut::<bevy::asset::Assets<bevy::pbr::StandardMaterial>>()
        .unwrap();
    let material = materials.add(bevy::pbr::StandardMaterial {
        base_color: bevy::color::Color::srgb(0.8, 0.2, 0.1),
        ..Default::default()
    });
    drop(materials);
    app.world_mut().spawn((
        Name::new("rich"),
        Transform::from_xyz(1.0, 2.0, 3.0).with_scale(Vec3::splat(2.0)),
        bevy::render::mesh::Mesh3d(cube),
        bevy::pbr::MeshMaterial3d(material),
        bevy_rapier3d::prelude::Collider::cuboid(0.5, 0.5, 0.5),
        bevy_rapier3d::prelude::RigidBody::Fixed,
        bevy::light::PointLight {
            intensity: 1200.0,
            shadow_maps_enabled: true,
            ..Default::default()
        },
    ));
    app.update(); // Startup: liga o servidor HTTP
    app
}

#[test]
fn test_bridge_lua_end_to_end_headless() {
    let mut app = lua_app(LUA_TEST_PORT);

    // `return` devolve o valor; conversão mlua → JSON.
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "return 1 + 1" }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    assert_eq!(response["result"], serde_json::json!(2));

    // player() lê o snapshot; teleport aplica a op no mesmo frame.
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({
                "code": "viber.debug.teleport(10, 5, -3)\nreturn viber.debug.player().x"
            }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    assert_eq!(
        response["result"].as_f64(),
        Some(0.0),
        "snapshot é do início da chamada"
    );
    assert_eq!(response["applied"], serde_json::json!(1));

    let mut query = app
        .world_mut()
        .query_filtered::<(&Name, &Transform), bevy::ecs::query::With<crate::player::Player>>();
    let (_, transform) = query.single(app.world()).expect("player com Transform");
    assert_eq!(transform.translation, Vec3::new(10.0, 5.0, -3.0));

    // find por nome + set_pos; pos() lê o snapshot do INÍCIO da chamada
    // (antes da op aplicar) — o read-back vem no chunk seguinte.
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({
                "code": "viber.debug.set_pos('goblin', 1, 2, 3)\nreturn { viber.debug.pos('goblin') }"
            }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let pos = response["result"].as_array().expect("pos é xyz");
    assert_eq!(pos[0].as_f64(), Some(5.0), "posição pré-op");

    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "return { viber.debug.pos('goblin') }" }),
        ),
    );
    // Leitura vem do snapshot — a op do chunk anterior já aplicou.
    let pos = response["result"].as_array().expect("pos é xyz");
    assert_eq!(pos[1].as_f64(), Some(2.0), "y do set_pos anterior");

    // Erro Luau → ok:false com a mensagem; a engine continua viva.
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "error('boom')" }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(false));
    assert!(
        response["error"]
            .as_str()
            .is_some_and(|e| e.contains("boom")),
        "erro: {response}"
    );

    // disable() insere `Disabled` — a entidade sai das queries normais.
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "viber.debug.disable('goblin') return true" }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let mut disabled = app
        .world_mut()
        .query_filtered::<&Name, bevy::ecs::query::With<bevy::ecs::entity_disabling::Disabled>>();
    let found = disabled
        .iter(app.world())
        .any(|name| name.as_str() == "goblin");
    assert!(found, "goblin devia estar Disabled");

    // enable() devolve-o ao mundo (mesmo escondido das queries enquanto
    // estava desativado, o snapshot via-o via iter_entities).
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "viber.debug.enable('goblin') return true" }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let mut still_disabled = app
        .world_mut()
        .query_filtered::<&Name, bevy::ecs::query::With<bevy::ecs::entity_disabling::Disabled>>();
    assert!(
        !still_disabled
            .iter(app.world())
            .any(|name| name.as_str() == "goblin"),
        "goblin devia estar ativo"
    );

    // Globals persistem entre chamadas (REPL).
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "repl_hits = (repl_hits or 0) + 1 return true" }),
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let response = settle(
        &mut app,
        call_async_on(
            LUA_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": "return repl_hits" }),
        ),
    );
    assert_eq!(response["result"], serde_json::json!(1));

    // code ausente → erro BRP de params (fim do e2e: só há uma App nesta
    // porta — testes em paralelo não podem competir pela mesma).
    let error = settle_err(
        &mut app,
        call_async_on(LUA_TEST_PORT, METHOD_LUA, serde_json::json!({})),
    );
    assert!(error.contains("invalid params"), "erro: {error}");
}

/// Segundo lote de features: move_to/rotate/set_scale/kill/set_hp,
/// leituras distance/vault/quests/prof/fps, e ops com warning (câmara,
/// relógio — sem recursos nesta App mínima).
#[test]
fn test_bridge_lua_features_round2() {
    let mut app = lua_app(LUA2_TEST_PORT);
    let lua = |code: &'static str| {
        call_async_on(
            LUA2_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": code }),
        )
    };

    // move_to: qualquer entidade, XZ absolutos (sem terreno → Y fica).
    let response = settle(
        &mut app,
        lua("viber.debug.move_to('goblin', 20, 5) return true"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let mut query = app
        .world_mut()
        .query_filtered::<&Transform, bevy::ecs::query::Without<crate::player::Player>>();
    let goblin_pos: Vec3 = query
        .iter(app.world())
        .find(|t| t.translation.x == 20.0)
        .map(|t| t.translation)
        .expect("goblin movido");
    assert_eq!(goblin_pos.z, 5.0);

    // rotate +90°: yaw inicial identidade → π/2.
    let response = settle(
        &mut app,
        lua("viber.debug.rotate('goblin', 90) return true"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let mut query = app
        .world_mut()
        .query_filtered::<&Transform, bevy::ecs::query::Without<crate::player::Player>>();
    let yaw = query
        .iter(app.world())
        .find(|t| t.translation.x == 20.0)
        .map(|t| t.rotation.to_euler(bevy::math::EulerRot::YXZ).0)
        .expect("goblin");
    assert!(
        (yaw - std::f32::consts::FRAC_PI_2).abs() < 1e-4,
        "yaw devia ser 90°, foi {}",
        yaw.to_degrees()
    );

    // set_scale uniforme.
    let response = settle(
        &mut app,
        lua("viber.debug.set_scale('goblin', 2) return true"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let mut query = app
        .world_mut()
        .query_filtered::<&Transform, bevy::ecs::query::Without<crate::player::Player>>();
    let scale = query
        .iter(app.world())
        .find(|t| t.translation.x == 20.0)
        .map(|t| t.scale.x)
        .expect("goblin");
    assert_eq!(scale, 2.0);

    // set_hp absoluto no player + kill cru no dummy (Health a zero).
    let response = settle(
        &mut app,
        lua("viber.debug.set_hp(7) viber.debug.kill('dummy') return true"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    assert_eq!(response["applied"], serde_json::json!(2));
    let mut player_q = app
        .world_mut()
        .query_filtered::<&crate::vitals::Health, bevy::ecs::query::With<crate::player::Player>>();
    let player_hp = player_q
        .single(app.world())
        .expect("Health do player")
        .current;
    let mut dummy_q = app
        .world_mut()
        .query_filtered::<&crate::vitals::Health, bevy::ecs::query::Without<crate::player::Player>>(
        );
    let dummy_hp = dummy_q
        .single(app.world())
        .expect("Health do dummy")
        .current;
    assert_eq!(player_hp, 7.0, "set_hp");
    assert_eq!(dummy_hp, 0.0, "kill");

    // Ops sem recurso correspondente → warnings, não erro BRP.
    let response = settle(
        &mut app,
        lua(
            "viber.debug.set_camera{distance = 9} viber.debug.set_clock(1380) viber.debug.clear_markers() return true",
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let warnings = response["warnings"].as_array().expect("warnings é lista");
    assert_eq!(warnings.len(), 2, "câmara e relógio avisam: {warnings:?}");

    // Leituras: distance, vault (nil), quests (vazio), prof/fps.
    let response = settle(
        &mut app,
        lua(
            "return { d = viber.debug.distance('player', 'goblin'), tem_vault = viber.debug.vault() ~= nil, quests = #viber.debug.quests() }",
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let result = &response["result"];
    let expected = ((20.0f32 * 20.0 + 1.0 + 5.0 * 5.0).sqrt()) as f64;
    assert!(
        (result["d"].as_f64().unwrap() - expected).abs() < 1e-3,
        "distance"
    );
    assert_eq!(
        result["tem_vault"],
        serde_json::json!(false),
        "vault ausente"
    );
    assert_eq!(
        result["quests"],
        serde_json::json!(0),
        "sem QuestLog → vazio"
    );

    let response = settle(
        &mut app,
        lua("return { entities = viber.debug.prof().entities, fps = viber.debug.fps() }"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let result = &response["result"];
    assert!(
        result["entities"].as_u64().is_some(),
        "prof.entities presente"
    );
    assert!(result["fps"].is_null(), "fps sem DiagnosticsStore → nil");
}

/// Terceiro lote: introspeção — info/transform/mesh/material/collider/
/// components sobre a entidade "rich" (mesh+material+collider+corpo).
#[test]
fn test_bridge_lua_introspection() {
    let mut app = lua_app(LUA3_TEST_PORT);
    let lua = |code: &'static str| {
        call_async_on(
            LUA3_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": code }),
        )
    };

    // components() lista os componentes por nome.
    let response = settle(
        &mut app,
        lua("local c = viber.debug.components('rich') table.sort(c) return c"),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let components: Vec<&str> = response["result"]
        .as_array()
        .expect("lista de componentes")
        .iter()
        .map(|v| v.as_str().expect("string"))
        .collect();
    for expected in ["Transform", "Mesh3d", "Collider", "Name", "RigidBody"] {
        assert!(
            components
                .iter()
                .any(|c| c.ends_with(&format!("::{expected}")) || *c == expected),
            "'{expected}' em {components:?}"
        );
    }

    // mesh(): vértices + UVs do cubo (24 verts, uvs 0..1).
    let response = settle(
        &mut app,
        lua(
            "local m = viber.debug.mesh('rich') return { m.vertices, m.has_uvs, m.uv_min[1], m.uv_max[1] }",
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true));
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0], serde_json::json!(24), "cubo tem 24 vértices");
    assert_eq!(result[1], serde_json::json!(true));
    assert_eq!(result[2].as_f64(), Some(0.0), "uv_min");
    assert_eq!(result[3].as_f64(), Some(1.0), "uv_max");

    // material(): cor base e sem textura.
    let response = settle(
        &mut app,
        lua(
            "local m = viber.debug.material('rich') return { r = m.base_color[1], tex = m.base_color_texture }",
        ),
    );
    let result = &response["result"];
    assert!((result["r"].as_f64().unwrap() - 0.8).abs() < 1e-3, "r≈0.8");
    assert!(result["tex"].is_null(), "sem textura base_color");

    // collider(): cuboid com half-extents 0.5 + rigidbody Fixed.
    let response = settle(
        &mut app,
        lua("local c = viber.debug.collider('rich') return { c.shape, c.hx, c.hy, c.hz }"),
    );
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0], serde_json::json!("cuboid"));
    assert_eq!(result[1], serde_json::json!(0.5));

    // transform(): translation, scale e euler.
    let response = settle(
        &mut app,
        lua("local t = viber.debug.transform('rich') return { t.x, t.y, t.z, t.sx }"),
    );
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0].as_f64(), Some(1.0));
    assert_eq!(result[1].as_f64(), Some(2.0));
    assert_eq!(result[2].as_f64(), Some(3.0));
    assert_eq!(result[3].as_f64(), Some(2.0), "scale uniforme");

    // info(): tabela agregada com tudo.
    let response = settle(
        &mut app,
        lua(
            "local i = viber.debug.info('rich') return { i.name, i.rigidbody, i.collider ~= nil, i.mesh ~= nil, i.material ~= nil, #i.components > 0 }",
        ),
    );
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0], serde_json::json!("rich"));
    assert_eq!(result[1], serde_json::json!("Fixed"));
    assert_eq!(result[2], serde_json::json!(true));
    assert_eq!(result[3], serde_json::json!(true));
    assert_eq!(result[4], serde_json::json!(true));
    assert_eq!(result[5], serde_json::json!(true));

    // Entidade sem mesh → erro Luau claro (ok:false).
    let response = settle(&mut app, lua("return viber.debug.mesh('player')"));
    assert_eq!(response["ok"], serde_json::json!(false));
    assert!(
        response["error"].as_str().unwrap().contains("Mesh3d"),
        "erro: {response}"
    );
}

/// Quarto lote: bulk/profiling — stats, colliders, lights, around, physics.
#[test]
fn test_bridge_lua_bulk_profiling() {
    let mut app = lua_app(LUA4_TEST_PORT);
    let lua = |code: &'static str| {
        call_async_on(
            LUA4_TEST_PORT,
            METHOD_LUA,
            serde_json::json!({ "code": code }),
        )
    };

    // stats(): agregados do mundo inteiro.
    let response = settle(
        &mut app,
        lua(
            "local s = viber.debug.stats() return { s.entities, s.meshes, s.colliders, s.lights_point, s.lights_with_shadows, s.rigidbodies }",
        ),
    );
    assert_eq!(response["ok"], serde_json::json!(true), "resp: {response}");
    let result = response["result"].as_array().expect("lista");
    assert!(result[0].as_u64().unwrap() >= 4, "pelo menos 4 entidades");
    assert_eq!(result[1], serde_json::json!(1), "1 mesh (rich)");
    assert_eq!(result[2], serde_json::json!(1), "1 collider (rich)");
    assert_eq!(result[3], serde_json::json!(1), "1 PointLight");
    assert_eq!(result[4], serde_json::json!(1), "luz com sombras");
    assert_eq!(result[5], serde_json::json!(1), "1 RigidBody");

    // colliders(): o rich aparece com shape cuboid.
    let response = settle(
        &mut app,
        lua("local c = viber.debug.colliders() return { #c, c[1].shape, c[1].name }"),
    );
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0], serde_json::json!(1));
    assert_eq!(result[1], serde_json::json!("cuboid"));
    assert_eq!(result[2], serde_json::json!("rich"));

    // lights(): kind/shadows/intensity.
    let response = settle(
        &mut app,
        lua(
            "local l = viber.debug.lights() return { #l, l[1].kind, l[1].shadows, l[1].intensity }",
        ),
    );
    let result = response["result"].as_array().expect("lista");
    assert_eq!(result[0], serde_json::json!(1));
    assert_eq!(result[1], serde_json::json!("point"));
    assert_eq!(result[2], serde_json::json!(true));
    assert_eq!(result[3], serde_json::json!(1200));

    // around(): player e rich no raio; dummy (z=50) fora.
    let response = settle(
        &mut app,
        lua("local a = viber.debug.around(10) return { #a, a[1].name ~= nil, a[2] ~= nil }"),
    );
    assert_eq!(response["ok"], serde_json::json!(true), "resp: {response}");
    let result = response["result"].as_array().expect("lista");
    assert_eq!(
        result[0].as_u64().unwrap(),
        3,
        "player + goblin + rich num raio de 10"
    );
    // dummy está a ~50 m: raio pequeno exclui.
    let response = settle(&mut app, lua("local a = viber.debug.around(100) return #a"));
    assert!(
        response["result"].as_u64().unwrap() >= 4,
        "raio grande inclui o dummy"
    );

    // physics(): sem RapierContext nesta app → nil (não pode panicar).
    let response = settle(&mut app, lua("return viber.debug.physics() == nil"));
    assert_eq!(response["ok"], serde_json::json!(true));
    assert_eq!(response["result"], serde_json::json!(true));
}

/// Porta própria — os testes do bridge correm em paralelo no mesmo binário.
const IDENTITY_TEST_PORT: u16 = 35707;

/// O `viber.ping` identifica o mundo servido quando a engine tem
/// `BridgeIdentity` (inserida pelo `run`) — é o que o `viber debug --world`
/// valida contra o engine.json para apanhar registos stale.
#[test]
fn test_ping_reports_world_identity() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(bevy::input::InputPlugin)
        .add_plugins(BridgePlugin {
            port: IDENTITY_TEST_PORT,
        })
        .insert_resource(BridgeIdentity {
            world: "/repo/worlds/qa-pontes.xml".into(),
        });
    app.add_message::<CursorMoved>();
    app.update();

    let pong = settle(
        &mut app,
        call_async_on(IDENTITY_TEST_PORT, METHOD_PING, serde_json::json!({})),
    );
    assert_eq!(pong["pong"], serde_json::json!(true));
    assert_eq!(
        pong["world"],
        serde_json::json!("/repo/worlds/qa-pontes.xml")
    );
}

const BURST_TEST_PORT: u16 = 35708;

/// O `viber.burst` valida params cedo (frames fora da lista = erro BRP), e
/// um burst válido enfileira, spawna capturas (o sistema `Update`) e fica
/// `capturing` no status — headless (sem render) o guard de stall é quem
/// eventualmente o falha; aqui só se verifica o contrato do protocolo.
#[test]
fn test_bridge_burst_headless() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(bevy::input::InputPlugin)
        .add_plugins(BridgePlugin {
            port: BURST_TEST_PORT,
        });
    app.add_message::<CursorMoved>();
    app.update();

    // frames inválido → invalid params com a lista aceite na mensagem
    let error = settle_err(
        &mut app,
        call_async_on(
            BURST_TEST_PORT,
            METHOD_BURST,
            serde_json::json!({ "frames": 5 }),
        ),
    );
    assert!(error.contains("4, 9 ou 16"), "erro devia listar os aceites: {error}");

    // skip acima do teto → idem
    let error = settle_err(
        &mut app,
        call_async_on(
            BURST_TEST_PORT,
            METHOD_BURST,
            serde_json::json!({ "frames": 9, "skip": 10_000 }),
        ),
    );
    assert!(error.contains("skip"), "erro devia falar do skip: {error}");

    // burst válido → id + path; o status começa capturing com 0 capturados.
    // skip alto de propósito: garante que, não importa quantos updates o
    // pump do settle faça, só UMA captura chega a spawnar (determinismo).
    let started = settle(
        &mut app,
        call_async_on(
            BURST_TEST_PORT,
            METHOD_BURST,
            serde_json::json!({ "frames": 4, "skip": 500 }),
        ),
    );
    let id = started["id"].as_u64().expect("id do burst");
    assert_eq!(started["frames"], serde_json::json!(4));
    assert_eq!(started["skip"], serde_json::json!(500));
    assert!(started["path"].as_str().expect("path").contains("burst-"));

    // Um frame da app depois, a spawn já correu (Update) — status capturing.
    app.update();
    let status = settle(
        &mut app,
        call_async_on(
            BURST_TEST_PORT,
            METHOD_BURST_STATUS,
            serde_json::json!({ "id": id }),
        ),
    );
    assert_eq!(status["status"], serde_json::json!("capturing"));
    assert_eq!(status["captured"], serde_json::json!(0));
    assert_eq!(status["spawned"], serde_json::json!(1), "1.ª captura spawna no 1.º tick");
    assert!(
        status.get("png_base64").is_none(),
        "sem folha enquanto não capturado"
    );

    // id desconhecido → erro claro
    let error = settle_err(
        &mut app,
        call_async_on(
            BURST_TEST_PORT,
            METHOD_BURST_STATUS,
            serde_json::json!({ "id": 9999 }),
        ),
    );
    assert!(error.contains("unknown burst id"), "erro devia ser de id: {error}");
}

// ── M1: introspecção profunda (vitals de qualquer entidade, IA, nav, …) ──

const LUA5_TEST_PORT: u16 = 35709;

/// `viber.debug.*` do M1: health/ai por entidade, nav, quests fundas, seeds,
/// world_hash, waypoints, save_info, audio — nil-safe sem os plugins de jogo.
#[test]
fn test_bridge_lua_introspection_round3() {
    let mut app = lua_app(LUA5_TEST_PORT);
    {
        let world = app.world_mut();
        // Criatura da FSM com vitals, locomoção e perfil de nav.
        world.spawn((
            Name::new("mob"),
            Transform::from_xyz(-4.0, 0.5, 2.0),
            crate::vitals::Health {
                current: 30.0,
                max: 90.0,
            },
            crate::ai::EnemyCreature {
                speed: 3.5,
                ..Default::default()
            },
            crate::ai::AiLocomotion::default(),
            crate::nav::NavProfile::Wild,
        ));
        // Pilha de nav: config + tile (census fica vazio — sem landmass).
        world.insert_resource(crate::nav::NavConfig::default());
        world.insert_resource(crate::nav::NavTile::default());
        // Diário de quests (lidas do DISCO: dir das quests do exemplo).
        world.insert_resource(crate::quests::QuestLog::with_dir(
            &crate::quests::example_quests_dir(),
        ));
        // Skills sem nada aprendido.
        world.insert_resource(crate::skills::SkillTree::default());
    }
    app.update();

    // health() de QUALQUER entidade — não só do player.
    let r = lua_call(&mut app, LUA5_TEST_PORT, "local h = viber.debug.health('mob') return { h.current, h.max, h.dead }");
    assert_eq!(r["ok"], serde_json::json!(true));
    assert_eq!(r["result"][0].as_f64(), Some(30.0));
    assert_eq!(r["result"][1].as_f64(), Some(90.0));
    assert_eq!(r["result"][2], serde_json::json!(false));

    // Sem Health → nil (e não erro).
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return viber.debug.health('goblin') == nil");
    assert_eq!(r["result"], serde_json::json!(true));

    // ai() — FSM + locomoção + perfil.
    let r = lua_call(&mut app, LUA5_TEST_PORT, 
        "local a = viber.debug.ai('mob') return { a.state, a.speed, a.nav_profile, a.aggro_radius }",
    );
    assert_eq!(r["result"][0], serde_json::json!("wander"));
    assert_eq!(r["result"][1].as_f64(), Some(3.5));
    assert_eq!(r["result"][2], serde_json::json!("wild"));
    assert_eq!(r["result"][3].as_f64(), Some(18.0));

    // info() enriquecido: hp/max_hp + ai + script na mesma tabela.
    let r = lua_call(&mut app, LUA5_TEST_PORT, 
        "local i = viber.debug.info('mob') local g = viber.debug.info('goblin')
         return { i.hp, i.max_hp, i.ai.nav_profile, g.script }",
    );
    assert_eq!(r["result"][0].as_f64(), Some(30.0));
    assert_eq!(r["result"][3], serde_json::json!("ghost.lua"));

    // nav() — config inserida, sem tile ainda.
    let r = lua_call(&mut app, LUA5_TEST_PORT, 
        "local n = viber.debug.nav() return { n.enabled, n.tile_size, n.offroad_cost, n.tile_generations }",
    );
    assert_eq!(r["result"][0], serde_json::json!(true));
    assert_eq!(r["result"][1].as_f64(), Some(256.0));
    assert_eq!(r["result"][2].as_f64(), Some(2.5));
    assert_eq!(r["result"][3].as_i64(), Some(0));

    // Quest funda: defs embutidos + título + objetivo.
    let r = lua_call(&mut app, LUA5_TEST_PORT, 
        "local q = viber.debug.quest('forest_survey') return { q.title ~= nil, q.objective.kind, q.status }",
    );
    assert_eq!(r["result"][0], serde_json::json!(true));
    assert_eq!(r["result"][1], serde_json::json!("visit"));
    assert_eq!(r["result"][2], serde_json::json!("not_taken"));

    // quest_defs() cobre os JSONs embutidos (25 quests).
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return #viber.debug.quest_defs()");
    assert!(
        r["result"].as_i64().unwrap_or(0) >= 20,
        "defs embutidos: {}",
        r["result"]
    );

    // skills() com árvore vazia.
    let r = lua_call(&mut app, LUA5_TEST_PORT, "local s = viber.debug.skills() return { #s.learned, s.points }");
    assert_eq!(r["result"][0], serde_json::json!(0));
    assert_eq!(r["result"][1], serde_json::json!(0));

    // waypoints() — catálogo estático dos 12 marcos.
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return #viber.debug.waypoints().landmarks");
    assert_eq!(r["result"], serde_json::json!(12));

    // save_info() — path não vazio (exists depende da máquina).
    let r = lua_call(&mut app, LUA5_TEST_PORT, "local s = viber.debug.save_info() return #s.path > 0");
    assert_eq!(r["result"], serde_json::json!(true));

    // audio() — buses default sem kira; nil-safe.
    let r = lua_call(&mut app, LUA5_TEST_PORT, "local a = viber.debug.audio() return a ~= nil and a.buses.master");
    assert_eq!(r["result"].as_f64(), Some(1.0));

    // Seeds/terreno/atmosfera ausentes → nil, NUNCA erro.
    let r = lua_call(&mut app, LUA5_TEST_PORT, 
        "return { viber.debug.terrain(0, 0) == nil, viber.debug.atmosphere() == nil,
                  viber.debug.weather_full() == nil, viber.debug.border() == nil,
                  viber.debug.interior() == nil, viber.debug.biome_at(0, 0) == nil }",
    );
    for (i, v) in r["result"].as_array().unwrap().iter().enumerate() {
        assert_eq!(v, &serde_json::json!(true), "índice {i} devia ser nil-safe");
    }

    // ui_tree() vazio headless (array, não nil).
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return #viber.debug.ui_tree()");
    assert_eq!(r["result"], serde_json::json!(0));

    // world_hash: estável entre chamadas sem mutação, muda com o conteúdo.
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return viber.debug.world_hash()");
    let hash_a = r["result"].as_str().expect("hash hex").to_string();
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return viber.debug.world_hash()");
    assert_eq!(r["result"].as_str(), Some(hash_a.as_str()), "hash estável sem mutação");
    app.world_mut().spawn((Name::new("hash-bait"), Transform::IDENTITY));
    let r = lua_call(&mut app, LUA5_TEST_PORT, "return viber.debug.world_hash()");
    assert_ne!(
        r["result"].as_str(),
        Some(hash_a.as_str()),
        "entidade nova muda o hash"
    );

    // seeds() — sem terreno nem ciclo, tudo nil (e nunca erro).
    let r = lua_call(&mut app, LUA5_TEST_PORT, "local s = viber.debug.seeds() return s ~= nil and s.terrain_seed == nil");
    assert_eq!(r["result"], serde_json::json!(true));
}

// ── M2: controlo total (vitals/quests/vault/skills/IA/postfx/spawn…) ─────

const LUA6_TEST_PORT: u16 = 35710;
const RAYCAST_TEST_PORT: u16 = 35711;

#[test]
fn test_bridge_lua_control_round4() {
    let mut app = lua_app(LUA6_TEST_PORT);
    {
        let world = app.world_mut();
        world.spawn((
            Name::new("mob"),
            Transform::from_xyz(-4.0, 0.5, 2.0),
            crate::vitals::Health {
                current: 30.0,
                max: 90.0,
            },
            crate::ai::EnemyCreature {
                speed: 3.5,
                ..Default::default()
            },
        ));
        world.insert_resource(crate::quests::QuestLog::with_dir(
            &crate::quests::example_quests_dir(),
        ));
        world.insert_resource(crate::economy::Vault::default());
        world.insert_resource(crate::skills::SkillTree::default());
        world.insert_resource(crate::nav::NavConfig::default());
        world.insert_resource(crate::music::AudioMixerSettings::default());
        world.insert_resource(crate::music::CombatMusicState::default());
        world.insert_resource(bevy::ecs::message::Messages::<crate::ui::actions::UiAction>::default());
    }
    app.update();

    let lua = |code: &'static str, app: &mut App| {
        let code = code.to_string();
        let handle = std::thread::spawn(move || {
            BridgeClient::localhost(LUA6_TEST_PORT)
                .call(METHOD_LUA, serde_json::json!({ "code": code }))
                .map_err(|error| error.to_string())
        });
        for _ in 0..600 {
            app.update();
            if handle.is_finished() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        handle.join().unwrap().expect("bridge call responde")
    };

    // HP de QUALQUER entidade.
    let r = lua("viber.debug.set_entity_hp('mob', 5) return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app.world_mut().query::<(
        &Name,
        &crate::vitals::Health,
    )>();
    let found = query
        .iter(app.world())
        .find(|(name, _)| name.as_str() == "mob")
        .expect("mob");
    assert_eq!(found.1.current, 5.0);

    // set_max_hp baixa o teto e clampa o atual.
    let r = lua("viber.debug.set_max_hp('mob', 10) return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app.world_mut().query::<(&Name, &crate::vitals::Health)>();
    let (_, health) = query
        .iter(app.world())
        .find(|(name, _)| name.as_str() == "mob")
        .unwrap();
    assert_eq!((health.max, health.current), (10.0, 5.0));

    // Quests: force active → progress → done → reset. Escritas e leituras
    // em chamadas SEPARADAS (o snapshot é do início da chamada — semântica
    // documentada da REPL).
    let r = lua(
        "viber.debug.quest_force('forest_survey', 'active')
         viber.debug.quest_progress('forest_survey', 2)
         return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(2));
    let r = lua(
        "local q = viber.debug.quest('forest_survey')
         return { q.status, q.objective.progress_text, #q.visited }",
        &mut app,
    );
    assert_eq!(r["result"][0], serde_json::json!("active"));
    let expected = format!(
        "2/{}",
        r["result"]
            .pointer("/2")
            .and_then(Value::as_i64)
            .unwrap_or_default()
    );
    // progress_text = "2/<count do def>" — o count exato vem dos JSONs
    assert!(
        r["result"][1].as_str().unwrap_or("").starts_with("2/"),
        "progress_text: {}",
        r["result"][1]
    );
    let _ = expected;
    assert_eq!(r["result"][2], serde_json::json!(2));
    let r = lua(
        "viber.debug.quest_force('forest_survey', 'done') return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(1));
    let r = lua(
        "return viber.debug.quest('forest_survey').status",
        &mut app,
    );
    assert_eq!(r["result"], serde_json::json!("done"));
    let r = lua(
        "viber.debug.quest_force('forest_survey', 'reset') return true",
        &mut app,
    );
    let r = lua("return viber.debug.quest('forest_survey').status", &mut app);
    assert_eq!(r["result"], serde_json::json!("not_taken"));

    // Vault: valor absoluto + take (escritas e leitura em chamadas separadas).
    let r = lua(
        "viber.debug.vault_set('gold', 500)
         viber.debug.vault_set('potion', 3)
         viber.debug.take('potion', 1)
         return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(3));
    let r = lua(
        "local v = viber.debug.vault()
         return { v.gold, v.items.potion }",
        &mut app,
    );
    assert_eq!(r["result"][0], serde_json::json!(500));
    assert_eq!(r["result"][1], serde_json::json!(2));

    // Skills: pontos → aprender → herói ganha max_hp → reset devolve.
    let r = lua(
        "viber.debug.skill_points(5)
         viber.debug.skill_learn('vitality1')
         return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(2));
    let r = lua(
        "local s = viber.debug.skills()
         local p = viber.debug.player()
         return { #s.learned, s.points, p.max_hp }",
        &mut app,
    );
    assert_eq!(r["result"][0], serde_json::json!(1));
    assert_eq!(r["result"][1], serde_json::json!(4));
    assert_eq!(r["result"][2].as_f64(), Some(120.0));
    let r = lua("viber.debug.skill_reset() return true", &mut app);
    let r = lua(
        "local s = viber.debug.skills()
         return { #s.learned, s.points, viber.debug.player().max_hp }",
        &mut app,
    );
    assert_eq!(r["result"][0], serde_json::json!(0));
    assert_eq!(r["result"][1], serde_json::json!(5));
    assert_eq!(r["result"][2].as_f64(), Some(100.0));

    // IA: aggro persiste; state valida input.
    let r = lua("viber.debug.ai_aggro('mob', 42) return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let r = lua("return viber.debug.ai('mob').aggro_radius", &mut app);
    assert_eq!(r["result"].as_f64(), Some(42.0));
    let r = lua(
        "viber.debug.ai_state('mob', 'banana') return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(0));
    assert!(
        serde_json::to_string(&r["warnings"])
            .unwrap()
            .contains("inválido"),
        "warning de estado inválido"
    );

    // nav_set ao vivo.
    let r = lua(
        "viber.debug.nav_set{offroad_cost = 9.5, tile_size = 128} return true",
        &mut app,
    );
    let r = lua("return viber.debug.nav().offroad_cost", &mut app);
    assert_eq!(r["result"].as_f64(), Some(9.5));

    // postfx: gate FORA da resposta — observável do lado Rust.
    let r = lua("viber.debug.postfx{bloom = false} return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    assert!(crate::postfx::fx_off("BLOOM"), "bloom forçado OFF");
    let r = lua("viber.debug.postfx{bloom = true} return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    assert!(
        !crate::postfx::fx_off("BLOOM"),
        "bloom restaurado (sem env)"
    );

    // audio_set reflete no mixer (lido pelo snapshot de áudio).
    let r = lua("viber.debug.audio_set{music = 0.25} return true", &mut app);
    let r = lua("return viber.debug.audio().buses.music", &mut app);
    assert_eq!(r["result"].as_f64(), Some(0.25));

    // combat_music battle → layer ativa; off → apaga.
    let r = lua(
        "viber.debug.combat_music('boss') return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(1));
    {
        let world = app.world();
        let music = world.resource::<crate::music::CombatMusicState>();
        let mut probe = music.clone();
        assert_eq!(probe.active_layer(0.0), Some("boss"));
    }
    let r = lua("viber.debug.combat_music('off') return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));

    // save/load: UiAction escrevida (applied=1 chega — o handler da UI é que
    // consome; sem UIPlugin a mensagem fica na fila sem efeito).
    let r = lua("viber.debug.save() return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));

    // teleport_to: player vai ao goblin (sem terreno, Y = o do alvo).
    let r = lua("viber.debug.teleport_to('goblin') return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app
        .world_mut()
        .query_filtered::<&Transform, bevy::ecs::query::With<crate::player::Player>>();
    let transform = query.single(app.world()).unwrap();
    assert_eq!(transform.translation.x, 5.0);
    assert_eq!(transform.translation.z, 0.0);

    // spawn de primitiva FÍSICA: collider + corpo fixo.
    let r = lua(
        "viber.debug.spawn('box:2,2,2', 10, 0, 10, {color = '#ff0000'}) return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app.world_mut().query::<(
        &Name,
        Option<&bevy_rapier3d::prelude::Collider>,
        Option<&bevy_rapier3d::prelude::RigidBody>,
    )>();
    let spawned = query
        .iter(app.world())
        .find(|(name, _, _)| name.as_str().starts_with("debug:spawn:"))
        .expect("primitiva spawnada");
    assert!(spawned.1.is_some(), "tem collider");
    assert!(spawned.2.is_some(), "tem rigidbody");

    // spawn_light.
    let r = lua(
        "viber.debug.spawn_light(1, 3, 1, {intensity = 900, shadows = true}) return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app
        .world_mut()
        .query_filtered::<(&Name, &bevy::light::PointLight), ()>();
    let light = query
        .iter(app.world())
        .find(|(name, _)| name.as_str().starts_with("debug:light:"))
        .expect("luz spawnada");
    assert_eq!(light.1.intensity, 900.0);
    assert!(light.1.shadow_maps_enabled);

    // set_material ao vivo (rich tem StandardMaterial).
    let r = lua(
        "viber.debug.set_material('rich', {base_color = '#00ff00', metallic = 1}) return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(1));
    {
        let mut query = app.world_mut().query::<(
            Option<&Name>,
            &bevy::pbr::MeshMaterial3d<bevy::pbr::StandardMaterial>,
        )>();
        let world = app.world();
        // O rich pelo NOME — depois do spawn do box há ≥2 materiais.
        let handle = query
            .iter(world)
            .find(|(name, _)| {
                name.map(|n| n.as_str() == "rich").unwrap_or(false)
            })
            .expect("rich material")
            .1
            .0
            .clone();
        let assets = world.resource::<bevy::asset::Assets<bevy::pbr::StandardMaterial>>();
        let mat = assets.get(&handle).unwrap();
        let srgba = mat.base_color.to_srgba();
        assert!((srgba.green - 1.0).abs() < 1e-3, "base_color verde");
        assert_eq!(mat.metallic, 1.0);
    }

    // set_light ao vivo.
    let r = lua("viber.debug.set_light('rich', {intensity = 77}) return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app
        .world_mut()
        .query_filtered::<&bevy::light::PointLight, ()>();
    let rich_light = query
        .iter(app.world())
        .find(|l| (l.intensity - 77.0).abs() < 1e-3)
        .expect("luz do rich a 77");
    assert_eq!(rich_light.intensity, 77.0);

    // GLB sem AssetServer → warning, sem crash.
    let r = lua(
        "viber.debug.spawn('/assets/nao-existe.glb', 0, 0, 0, {snap = false}) return true",
        &mut app,
    );
    assert_eq!(r["applied"], serde_json::json!(0));
    assert!(
        serde_json::to_string(&r["warnings"]).unwrap().contains("AssetServer"),
        "warning de AssetServer ausente"
    );

    // clear_markers limpa TODO o namespace debug:*.
    let r = lua("viber.debug.clear_markers() return true", &mut app);
    assert_eq!(r["applied"], serde_json::json!(1));
    let mut query = app.world_mut().query::<&Name>();
    let leftovers = query
        .iter(app.world())
        .filter(|name| name.as_str().starts_with("debug:"))
        .count();
    assert_eq!(leftovers, 0, "nenhum debug:* sobrevive");
}

#[test]
fn test_bridge_raycast_headless() {
    let mut app = lua_app(RAYCAST_TEST_PORT);
    // PhysicsPlugin REAL: é ele que constrói o collider set do Rapier a
    // partir dos componentes `Collider` (um contexto manual ficaria vazio e
    // o raio nunca apanhava nada). O `resolve_pending_colliders` quer um
    // AssetServer — entra o AssetPlugin mínimo.
    app.add_plugins(bevy::asset::AssetPlugin::default());
    app.add_plugins(bevy::transform::TransformPlugin);
    // Stores que o `resolve_pending_colliders` toca por caminho de asset.
    app.init_asset::<bevy::gltf::Gltf>();
    app.init_asset::<bevy::gltf::GltfMesh>();
    app.init_asset::<bevy::gltf::GltfPrimitive>();
    app.init_asset::<bevy::gltf::GltfNode>();
    app.init_asset::<bevy::image::Image>();
    app.add_plugins(crate::physics::PhysicsPlugin { debug: false });
    {
        let world = app.world_mut();
        // O plugin insere o contexto em PreStartup, que já correu antes de
        // plugins pós-primeiro-update — spawnamos à mão o mesmo par; os
        // sistemas Sync do Rapier (Update) registam os colliders no set.
        world.spawn((
            bevy_rapier3d::prelude::RapierContextSimulation::default(),
            bevy_rapier3d::prelude::RapierConfiguration::new(1.0),
        ));
        // Cubo collider a 5 m de altura — raio de cima deve apanhar o TOPO.
        let mut meshes = world
            .resource_mut::<bevy::asset::Assets<bevy::mesh::Mesh>>();
        let cube = meshes.add(bevy::mesh::Mesh::from(bevy::math::primitives::Cuboid::new(
            1.0, 1.0, 1.0,
        )));
        drop(meshes);
        world.spawn((
            Name::new("alvo"),
            Transform::from_xyz(0.0, 5.0, 0.0),
            bevy::render::mesh::Mesh3d(cube),
            bevy_rapier3d::prelude::Collider::cuboid(0.5, 0.5, 0.5),
        ));
    }
    app.update();

    let handle = std::thread::spawn(|| {
        BridgeClient::localhost(RAYCAST_TEST_PORT)
            .call(
                METHOD_RAYCAST,
                serde_json::json!({ "x": 0.0, "y": 10.0, "z": 0.0, "dx": 0.0, "dy": -1.0, "dz": 0.0 }),
            )
            .map_err(|error| error.to_string())
    });
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    // NOTA: o caminho de sync completo do Rapier (collider COMPONENTE → set
    // interno) precisa de FixedUpdate + escada de sistemas que só o mundo
    // inteiro tem; o hit GEOMÉTRICO valida-se no fumo ao vivo (qa-bridge).
    // Headless garante: params/normalização, resposta estruturada e os dois
    // caminhos "sem contexto" (erro) e "contexto vazio" (hit=false).
    let r = handle.join().unwrap().expect("raycast responde");
    assert_eq!(r["hit"], serde_json::json!(false));
}

// ── M4: apidoc cobre EXATAMENTE as funções registadas (guard) ────────────

#[test]
fn test_apidoc_covers_registered_functions() {
    let mut app = lua_app(35712);
    app.update();
    // Chaves REAIS de viber.debug na VM.
    let handle = std::thread::spawn(|| {
        BridgeClient::localhost(35712)
            .call(
                METHOD_LUA,
                serde_json::json!({
                    "code": "local keys = {} for k in pairs(viber.debug) do table.insert(keys, k) end table.sort(keys) return keys"
                }),
            )
            .map_err(|error| error.to_string())
    });
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let response = handle.join().unwrap().expect("chamada responde");
    let registered: Vec<String> = response["result"]
        .as_array()
        .expect("lista de chaves")
        .iter()
        .map(|v| v.as_str().unwrap_or("?").to_string())
        .collect();
    let documented: Vec<&str> = crate::bridge::lua::DEBUG_API_DOCS
        .iter()
        .map(|(name, _, _)| *name)
        .collect();

    let undocumented: Vec<&String> = registered
        .iter()
        .filter(|name| !documented.contains(&name.as_str()))
        .collect();
    assert!(
        undocumented.is_empty(),
        "funções SEM doc no DEBUG_API_DOCS: {undocumented:?} — acrescenta a entrada \
         (a auto-descoberta do agente depende disto)"
    );

    let phantom: Vec<&&str> = documented
        .iter()
        .filter(|name| !registered.iter().any(|r| r == *name))
        .collect();
    assert!(
        phantom.is_empty(),
        "docs SEM função registada: {phantom:?} — remove a entrada órfã"
    );

    // apidoc() responde e traz os grupos.
    let handle = std::thread::spawn(|| {
        BridgeClient::localhost(35712)
            .call(
                METHOD_LUA,
                serde_json::json!({ "code": "local d = viber.debug.apidoc() return { has_debug = d.debug ~= nil, has_game = d.game ~= nil, doc_health = d.debug.health ~= nil }" }),
            )
            .map_err(|error| error.to_string())
    });
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let response = handle.join().unwrap().expect("apidoc responde");
    assert_eq!(response["result"]["has_debug"], serde_json::json!(true));
    assert_eq!(response["result"]["has_game"], serde_json::json!(true));
    assert_eq!(response["result"]["doc_health"], serde_json::json!(true));
}

// ── registry.schema / rpc.discover (builtin do bevy_remote) ──────────────

const SCHEMA_TEST_PORT: u16 = 35713;

/// O método builtin `registry.schema` responde com os campos dos tipos
/// refletidos — a base do `viber debug schema` (os fields vivem em
/// `/properties` como `$ref`s JSON-schema, não em `/fields`).
#[test]
fn test_bridge_registry_schema_headless() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins)
        .add_plugins(BridgePlugin {
            port: SCHEMA_TEST_PORT,
        })
        .register_type::<Transform>();
    app.update();

    let handle = std::thread::spawn(|| {
        BridgeClient::localhost(SCHEMA_TEST_PORT)
            .call(
                "registry.schema",
                serde_json::json!({ "with_crates": ["bevy_transform"] }),
            )
            .map_err(|error| error.to_string())
    });
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let response = handle.join().unwrap().expect("registry.schema responde");
    let types = response.as_object().expect("mapa tipo→schema");
    // Nem todo o tipo que "termina em Transform" é a struct com campos
    // (HashMap → ordem arbitrária); procura-se o que TEM o campo.
    let with_translation: Vec<&String> = types
        .iter()
        .filter(|(_, entry)| {
            entry
                .get("properties")
                .and_then(Value::as_object)
                .is_some_and(|properties| properties.contains_key("translation"))
        })
        .map(|(name, _)| name)
        .collect();
    assert!(
        with_translation.iter().any(|name| name.ends_with("Transform")),
        "algum tipo Transform com campo translation no schema; candidatos: {with_translation:?}"
    );

    // `rpc.discover` lista os métodos (builtin + viber.*).
    let handle = std::thread::spawn(|| {
        BridgeClient::localhost(SCHEMA_TEST_PORT)
            .call("rpc.discover", serde_json::json!({}))
            .map_err(|error| error.to_string())
    });
    for _ in 0..600 {
        app.update();
        if handle.is_finished() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let discover = handle.join().unwrap().expect("rpc.discover responde");
    let names: Vec<&str> = discover
        .get("methods")
        .and_then(Value::as_array)
        .map(|methods| {
            methods
                .iter()
                .filter_map(|m| m.get("name").and_then(Value::as_str))
                .collect()
        })
        .unwrap_or_default();
    assert!(
        names.contains(&METHOD_LUA),
        "rpc.discover inclui {METHOD_LUA}: {names:?}"
    );
}
