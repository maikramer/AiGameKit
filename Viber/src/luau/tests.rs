//! Testes headless do runtime Luau (app mínima: Time + host + os sistemas).

use super::*;
use crate::player::Player;
use crate::vitals::Health;
use mlua::Table;
use std::path::PathBuf;
use std::time::Duration;

    /// Advances the app's `Time` resource by `secs` (no TimePlugin in tests).
    fn advance_time(app: &mut bevy::app::App, secs: f32) {
        let mut time = app.world_mut().remove_resource::<Time>().unwrap();
        time.advance_by(Duration::from_secs_f32(secs));
        app.world_mut().insert_resource(time);
    }

    /// Minimal headless app: Time + host resource + the three runtime systems.
    fn test_app(host: LuaScriptHost) -> bevy::app::App {
        let mut app = bevy::app::App::new();
        app.init_resource::<Time>();
        app.init_resource::<ButtonInput<KeyCode>>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::ambient::SfxEvent>();
        app.add_message::<crate::feedback::PlayerHurt>();
        app.insert_resource(host);
        app.add_systems(Update, (luau_on_add, luau_update, luau_on_remove).chain());
        app
    }

    fn host_with(code: &str, path: &str) -> LuaScriptHost {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script(path, code).expect("load script");
        host
    }

    #[test]
    fn test_script_top_level_runs_and_logs() {
        let mut host = host_with("viber.log('hello from luau')", "log.lua");
        let mut world = World::new();
        let entity = world.spawn_empty().id();
        host.activate(entity, "log.lua").expect("activate");
        assert!(
            host.logs().iter().any(|l| l == "hello from luau"),
            "expected logged line, got {:?}",
            host.logs()
        );
    }

    #[test]
    fn test_syntax_error_is_reported_not_fatal() {
        let mut host = host_with("function on_update(dt) end", "ok.lua");
        let err = host
            .load_script("broken.lua", "this is not luau )))")
            .expect_err("syntax error should fail loading");
        assert!(!err.to_string().is_empty());
        // O host continua utilizável depois do erro de compilação.
        host.load_script("after.lua", "viber.log('still alive')")
            .expect("reload ok");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "after.lua")
            .expect("activate after");
        assert!(host.logs().iter().any(|l| l == "still alive"));
    }

    #[test]
    fn test_runtime_error_warns_once_and_engine_survives() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("bad.lua", "function on_update(dt) error('boom') end")
            .expect("load bad");
        host.load_script(
            "good.lua",
            "calls = 0\nfunction on_update(dt) calls = calls + 1 end",
        )
        .expect("load good");
        let mut world = World::new();
        let bad = world.spawn_empty().id();
        let good = world.spawn_empty().id();
        host.activate(bad, "bad.lua").expect("activate bad");
        host.activate(good, "good.lua").expect("activate good");

        for _ in 0..3 {
            let bad_err = host
                .run_update(bad, "bad.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect_err("bad script errors every frame");
            assert!(bad_err.to_string().contains("boom"));
            host.warn_once("bad.lua", &bad_err);
            host.run_update(good, "good.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect("good script unaffected");
        }

        // Warn 1x: só a primeira chamada devolve true.
        let mut host2 = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host2
            .load_script("x.lua", "function on_update(dt) error('e') end")
            .unwrap();
        host2.activate(bad, "x.lua").unwrap();
        assert!(host2.warn_once("x.lua", &"first"));
        assert!(!host2.warn_once("x.lua", &"second"));

        // Script bom correu as 3 vezes apesar do mau.
        match host.script_global("good.lua", "calls").expect("global") {
            mlua::Value::Integer(n) => assert_eq!(n, 3),
            other => panic!("expected integer calls, got {other:?}"),
        }
    }

    #[test]
    fn test_on_update_call_counter() {
        let mut host = host_with(
            "count = 0\nfunction on_update(dt) count = count + 1 end",
            "counter.lua",
        );
        let mut world = World::new();
        let entity = world.spawn_empty().id();
        host.activate(entity, "counter.lua").expect("activate");
        for i in 1..=4 {
            host.run_update(entity, "counter.lua", 0.016, Vec3::ZERO, None, 0.0)
                .expect("run_update");
            match host.script_global("counter.lua", "count").expect("count") {
                mlua::Value::Integer(n) => assert_eq!(n, i),
                other => panic!("expected integer count, got {other:?}"),
            }
        }
    }

    #[test]
    fn test_position_read_write_via_app() {
        let host = host_with(
            "function on_update(dt)\n  local px, py, pz = viber.position()\n  viber.set_position(px + 5, py, pz)\nend",
            "move.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Transform::from_xyz(1.0, 2.0, 3.0),
            LuaScriptRef {
                path: "move.lua".to_string(),
            },
        ));
        app.update();

        let mut q = app.world_mut().query::<&Transform>();
        let tf = q.single(app.world()).expect("transform");
        assert!(
            (tf.translation.x - 6.0).abs() < 1e-4,
            "x should be 6, got {}",
            tf.translation.x
        );
        assert!((tf.translation.y - 2.0).abs() < 1e-4);
        assert!((tf.translation.z - 3.0).abs() < 1e-4);
    }

    #[test]
    fn test_distance_to_player() {
        let host = host_with(
            "dist = nil\nfunction on_update(dt) dist = viber.distance_to_player() end",
            "dist.lua",
        );
        let mut app = test_app(host);
        // Player a 10 m na origem do script.
        app.world_mut()
            .spawn((Player::default(), GlobalTransform::from_xyz(10.0, 0.0, 0.0)));
        app.world_mut().spawn((
            Transform::from_xyz(0.0, 0.0, 0.0),
            LuaScriptRef {
                path: "dist.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        // mlua converte 10.0 (integral) em Value::Integer; aceitar ambos.
        let dist = match host.script_global("dist.lua", "dist").expect("dist") {
            mlua::Value::Number(d) => d,
            mlua::Value::Integer(n) => n as f64,
            other => panic!("expected number dist, got {other:?}"),
        };
        assert!((dist - 10.0).abs() < 1e-4, "dist {dist}");
    }

    #[test]
    fn test_time_api_reports_elapsed() {
        let host = host_with(
            "t0 = nil\nfunction on_update(dt) t0 = viber.time() end",
            "clock.lua",
        );
        let mut app = test_app(host);
        advance_time(&mut app, 2.5);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "clock.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("clock.lua", "t0").expect("t0") {
            mlua::Value::Number(t) => {
                assert!((t - 2.5).abs() < 0.1, "elapsed should be ~2.5, got {t}")
            }
            other => panic!("expected number t0, got {other:?}"),
        }
    }

    #[test]
    fn test_on_update_receives_dt() {
        let host = host_with("got = nil\nfunction on_update(dt) got = dt end", "dt.lua");
        let mut app = test_app(host);
        advance_time(&mut app, 0.25);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "dt.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("dt.lua", "got").expect("got") {
            mlua::Value::Number(d) => assert!((d - 0.25).abs() < 1e-3, "dt {d}"),
            other => panic!("expected number dt, got {other:?}"),
        }
    }

    #[test]
    fn test_script_environments_are_isolated() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("a.lua", "secret = 42\nfunction on_update(dt) end")
            .expect("load a");
        host.load_script(
            "b.lua",
            "assert(secret == nil, 'b must not see a.globals')\nfunction on_update(dt) end",
        )
        .expect("load b");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "a.lua")
            .expect("activate a");
        host.activate(world.spawn_empty().id(), "b.lua")
            .expect("activate b");
        match host.script_global("a.lua", "secret").expect("secret") {
            mlua::Value::Integer(n) => assert_eq!(n, 42),
            other => panic!("expected integer secret, got {other:?}"),
        }
    }

    #[test]
    fn test_on_remove_stops_script_calls() {
        let host = host_with(
            "count = 0\nfunction on_update(dt) count = count + 1 end",
            "count_remove.lua",
        );
        let mut app = test_app(host);
        let entity = app
            .world_mut()
            .spawn((
                Transform::default(),
                LuaScriptRef {
                    path: "count_remove.lua".to_string(),
                },
            ))
            .id();
        app.update();
        app.update();

        app.world_mut().despawn(entity);
        app.update(); // frame pós-despawn: on_remove corre, on_update não deve chamar
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host
            .script_global("count_remove.lua", "count")
            .expect("count")
        {
            mlua::Value::Integer(n) => assert_eq!(n, 2, "script must stop after despawn"),
            other => panic!("expected integer count, got {other:?}"),
        }
    }

    #[test]
    fn test_load_script_from_dir_reads_disk() {
        let tmp = tempfile::tempdir().expect("tmpdir");
        let scripts = tmp.path().join("scripts");
        std::fs::create_dir_all(&scripts).expect("mkdir");
        std::fs::write(
            scripts.join("disk.lua"),
            "loaded_from = 'disk'\nfunction on_update(dt) end",
        )
        .expect("write script");

        let mut host = LuaScriptHost::new(scripts).expect("host");
        host.ensure_loaded("disk.lua").expect("ensure_loaded");
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "disk.lua")
            .expect("activate");
        match host
            .script_global("disk.lua", "loaded_from")
            .expect("global")
        {
            mlua::Value::String(s) => assert_eq!(s.to_str().expect("utf8"), "disk"),
            other => panic!("expected string, got {other:?}"),
        }
        // Idempotente: segunda chamada não recarrega nem falha.
        host.ensure_loaded("disk.lua").expect("ensure_loaded again");
    }

    #[test]
    fn test_plugin_registers_host_and_systems() {
        let mut app = bevy::app::App::new();
        app.add_plugins(LuauScriptPlugin::default());
        assert!(app.world().get_resource::<LuaScriptHost>().is_some());

        // Script pré-carregado via o resource do plugin (host de teste).
        let mut host = app.world_mut().resource_mut::<LuaScriptHost>();
        host.load_script("plugin.lua", "viber.log('via plugin')")
            .expect("load");

        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "plugin.lua".to_string(),
            },
        ));
        app.update();
        assert!(
            app.world()
                .resource::<LuaScriptHost>()
                .logs()
                .iter()
                .any(|l| l == "via plugin"),
            "plugin path should run scripts"
        );
    }

    #[test]
    fn test_missing_script_on_disk_warns_but_does_not_panic() {
        let mut app = bevy::app::App::new();
        app.init_resource::<Time>();
        app.init_resource::<ButtonInput<KeyCode>>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::ambient::SfxEvent>();
        app.add_message::<crate::feedback::PlayerHurt>();
        app.insert_resource(
            LuaScriptHost::new(PathBuf::from("/nonexistent/scripts")).expect("host"),
        );
        app.add_systems(Update, (luau_on_add, luau_update, luau_on_remove).chain());
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "ghost.lua".to_string(),
            },
        ));
        app.update(); // on_add falha → warn_once; luau_update ignora path ausente
        app.update();
        assert!(app.world().get_resource::<LuaScriptHost>().is_some());
    }

    #[test]
    fn test_registry_lists_paths_sorted() {
        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("z.lua", "function on_update(dt) end")
            .unwrap();
        host.load_script("a.lua", "function on_update(dt) end")
            .unwrap();
        assert_eq!(host.registry.paths(), vec!["a.lua", "z.lua"]);
        assert!(host.registry.contains("z.lua"));
        assert!(!host.registry.contains("nope.lua"));
    }

    #[test]
    fn test_bundled_example_script_compiles_runs_and_moves() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("assets")
            .join("scripts")
            .join("example.lua");
        let code = std::fs::read_to_string(&path)
            .expect("assets/scripts/example.lua must ship with the crate");

        let mut host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.load_script("example.lua", &code)
            .expect("compile example");

        let mut app = test_app(host);
        let entity = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 0.0),
                LuaScriptRef {
                    path: "example.lua".to_string(),
                },
            ))
            .id();
        advance_time(&mut app, 0.5);
        app.update();

        // O script empurra py = sin(elapsed * 2): com elapsed 0.5 s, py != 0.
        let mut q = app.world_mut().query::<&Transform>();
        let tf = q.get(app.world(), entity).expect("transform");
        assert!(
            tf.translation.y.abs() > 1e-3,
            "example should oscillate y, got {}",
            tf.translation.y
        );
        // Top-level log + tick @1 Hz ainda não (elapsed < 1).
        let logs = app.world().resource::<LuaScriptHost>().logs();
        assert!(
            logs.iter().any(|l| l.contains("carregado")),
            "example top-level log missing: {logs:?}"
        );
    }

    /// CharacterAnimator mínimo com os clips dados (1 s cada, nós 1..n).
    fn test_animator(names: &[&str]) -> crate::animation::CharacterAnimator {
        crate::animation::CharacterAnimator {
            clip_names: names.iter().map(|s| s.to_string()).collect(),
            nodes: (0..names.len())
                .map(|i| bevy::animation::graph::AnimationNodeIndex::new(i + 1))
                .collect(),
            durations: vec![1.0; names.len()],
            player: Entity::PLACEHOLDER,
            state: None,
            current: None,
            action_time: 0.0,
            locked: false,
            air_time: 0.0,
            speed: 0.0,
            last_pos: None,
        }
    }

    #[test]
    fn test_match_gesture_clip_fuzzy_and_alternatives() {
        // Pack npc_* real: prefixos de ferramenta, `_` e caixa variam.
        let a = test_animator(&["Animator3D_Talk", "Animator3D_Yes", "npc_Fold_Arms", "idle"]);
        assert_eq!(match_gesture_clip(&a, "yes"), Some(1));
        assert_eq!(match_gesture_clip(&a, "YES"), Some(1), "case-insensitive");
        assert_eq!(match_gesture_clip(&a, "talk"), Some(0));
        // "foldarms" encontra "npc_Fold_Arms" (normalização de ambos os lados).
        assert_eq!(match_gesture_clip(&a, "foldarms"), Some(2));
        assert_eq!(match_gesture_clip(&a, "fold_arms"), Some(2));
        // Clip inexistente = None; a alternativa seguinte salva.
        assert_eq!(match_gesture_clip(&a, "salute"), None);
        assert_eq!(match_gesture_clip(&a, "salute,yes"), Some(1));
        assert_eq!(match_gesture_clip(&a, "salute|talk"), Some(0));
        // Pedido vazio (ou só separadores) nunca dá match.
        assert_eq!(match_gesture_clip(&a, ""), None);
        assert_eq!(match_gesture_clip(&a, ","), None);
    }

    #[test]
    fn test_sfx_clip_from_str_maps_known_names() {
        assert_eq!(sfx_clip_from_str("hit"), Some(crate::ambient::SfxClip::Hit));
        assert_eq!(
            sfx_clip_from_str("UI"),
            Some(crate::ambient::SfxClip::Ui),
            "case-insensitive"
        );
        assert_eq!(
            sfx_clip_from_str("harvest"),
            Some(crate::ambient::SfxClip::Harvest)
        );
        assert_eq!(
            sfx_clip_from_str("whoosh"),
            Some(crate::ambient::SfxClip::Whoosh)
        );
        assert_eq!(sfx_clip_from_str("boom"), None);
    }

    #[test]
    fn test_gesture_and_sound_commands_apply() {
        let host = host_with(
            "function on_update(dt)\n  viber.gesture('wave')\n  viber.sound('ui')\nend",
            "fx.lua",
        );
        let mut app = test_app(host);
        // AnimationPlayer da cena glTF (entidade descendente, como no runtime).
        let player = app
            .world_mut()
            .spawn((
                bevy::animation::AnimationPlayer::default(),
                bevy::animation::transition::AnimationTransitions::new(),
            ))
            .id();
        let mut animator = test_animator(&["idle", "Animator3D_Wave"]);
        animator.player = player;
        let npc = app
            .world_mut()
            .spawn((
                Transform::default(),
                animator,
                LuaScriptRef {
                    path: "fx.lua".to_string(),
                },
            ))
            .id();
        app.update();

        // Gesto aplicado: one-shot fica com o rig (action_time > 0).
        let animator = app
            .world()
            .get::<crate::animation::CharacterAnimator>(npc)
            .expect("animator");
        assert!(animator.action_time > 0.0, "o gesto devia estar a tocar");
        let wave = animator.node_matching(|n| n == "wave").expect("wave node");
        assert_eq!(animator.current, Some(wave));
        // Som aplicado: um SfxEvent Ui no buffer de mensagens.
        let mut events = app
            .world_mut()
            .resource_mut::<bevy::ecs::message::Messages<crate::ambient::SfxEvent>>();
        let clips: Vec<_> = events.drain().map(|e| e.clip).collect();
        assert_eq!(clips, vec![crate::ambient::SfxClip::Ui]);
    }

    #[test]
    fn test_gesture_without_animator_warns_once_and_survives() {
        let host = host_with(
            "count = 0\nfunction on_update(dt)\n  count = count + 1\n  viber.gesture('wave')\n  viber.sound('nope')\nend",
            "ghost-fx.lua",
        );
        let mut app = test_app(host);
        // Sem CharacterAnimator e com clip de som inválido: warn 1×, engine segue.
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "ghost-fx.lua".to_string(),
            },
        ));
        app.update();
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("ghost-fx.lua", "count").expect("count") {
            mlua::Value::Integer(n) => assert_eq!(n, 2, "script continua a correr"),
            other => panic!("expected integer count, got {other:?}"),
        }
    }

    #[test]
    fn test_player_hp_read() {
        let host = host_with(
            "got = nil\nfunction on_update(dt) got = { viber.player_hp() } end",
            "hp.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Player::default(),
            Transform::default(),
            crate::vitals::Health::default(),
            crate::vitals::Xp::default(),
        ));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "hp.lua".to_string(),
            },
        ));
        app.update();

        let host = app.world().resource::<LuaScriptHost>();
        let got = host.script_global("hp.lua", "got").expect("got");
        let mlua::Value::Table(t) = got else {
            panic!("expected table got, {got:?}")
        };
        let ok: bool = t.raw_get(1).expect("ok");
        let cur: f32 = t.raw_get(2).expect("cur");
        let max: f32 = t.raw_get(3).expect("max");
        assert!(ok, "player com vitals tem de reportar hp");
        assert!(
            (cur - max).abs() < 1e-3 && cur > 0.0,
            "default Health nasce cheio, tive cur={cur} max={max}"
        );
    }

    #[test]
    fn test_events_fan_out_to_subscribers() {
        let host = host_with(
            "received = nil\n\
             last_count = -1\n\
             function on_update(dt)\n\
               local ev = viber.events()\n\
               last_count = #ev\n\
               if #ev > 0 then received = ev end\n\
             end",
            "events.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<ScriptEventQueue>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "events.lua".to_string(),
            },
        ));
        // Frame 1: a 1.ª chamada a viber.events() subescreve o path.
        app.update();
        // Frame 2: um produtor empurra; o fan-out corre ANTES do on_update.
        app.world_mut()
            .resource_mut::<ScriptEventQueue>()
            .push(ScriptGameEvent::Kill {
                name: "wolf".to_string(),
                entity: 42,
            });
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        let received: Table = match host.script_global("events.lua", "received").unwrap() {
            mlua::Value::Table(t) => t,
            other => panic!("expected received table, got {other:?}"),
        };
        let first: Table = received.raw_get(1).unwrap();
        let kind: String = first.raw_get("type").unwrap();
        assert_eq!(kind, "kill");
        let name: String = first.raw_get("name").unwrap();
        assert_eq!(name, "wolf");
        let entity: i64 = first.raw_get("entity").unwrap();
        assert_eq!(entity, 42);
        // A fila drenou: um 3.º frame sem produtor entrega zero eventos.
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("events.lua", "last_count").unwrap() {
            mlua::Value::Integer(0) => {}
            other => panic!("a fila devia ter drenado, got {other:?}"),
        }
    }

    #[test]
    fn test_non_finite_and_unknown_key_args_are_rejected() {
        let host = host_with("function on_update(dt) end", "guards.lua");
        for snippet in [
            "return viber.damage_player(0/0)",
            "return viber.heal_player(math.huge)",
            "return viber.teleport_player(0/0, 0, 0)",
            "return viber.wander_target(0/0)",
            "return viber.set_interaction('Usar', 'zzz')",
            "return viber.set_interaction('Usar', 'e', 0/0)",
            "return viber.input.pressed('zzz')",
            "return viber.spawn_prototype('zzz', 0/0, 0)",
            "return viber.spawn_prototype('zzz', 0, 0, { y = 0/0 })",
            "return viber.spawn_prototype('zzz', 0, 0)",
            "return viber.own_system('zzz')",
            "return viber.say('olá', -1)",
            "return viber.radial_damage(0, 0, 0/0, 1)",
            "return viber.radial_damage(0, 0, 1, -1)",
            "return viber.burst('zzz', 0, 0, 0)",
            "return viber.ring(0, 0, 0)",
            "return viber.shake(-1)",
            "return viber.nearby(0)",
            "return viber.play_clip('x', { speed = 0 })",
            "return viber.fire_projectile('dardo', 0/0, 0, 0)",
            "return viber.fire_projectile('dardo', 1, 2)",
        ] {
            assert!(
                host.lua.load(snippet).exec().is_err(),
                "devia rejeitar: {snippet}"
            );
        }
        // Valores finitos continuam a passar (fora de on_update os setters
        // de combate/teleporte enfileiram à mesma — não precisam de entidade).
        assert!(
            host.lua
                .load("return viber.damage_player(5)")
                .exec()
                .is_ok()
        );
        assert!(host.lua.load("return viber.heal_player(5)").exec().is_ok());
        assert!(
            host.lua
                .load("return viber.teleport_player(1, 2, 3)")
                .exec()
                .is_ok()
        );
        // Sem herói no contexto não há alvo implícito: `false`, sem erro.
        let implicit: bool = host
            .lua
            .load("return viber.fire_projectile('dardo')")
            .eval()
            .unwrap();
        assert!(!implicit);
        let explicit: bool = host
            .lua
            .load("return viber.fire_projectile('dardo', 1, 2, 3)")
            .eval()
            .unwrap();
        assert!(explicit);
    }

    #[test]
    fn test_key_code_parser_covers_full_keyboard() {
        // Legado intacto.
        for legacy in ["e", "j", "f", "q", "r", "space"] {
            assert!(key_code_from_str(legacy).is_some(), "legado: {legacy}");
        }
        // Teclado completo: letras, dígitos, F-keys, setas, modificadoras,
        // símbolos literais e normalização com `_`/caixa.
        assert_eq!(key_code_from_str("W"), Some(KeyCode::KeyW));
        assert_eq!(key_code_from_str("arrow_up"), Some(KeyCode::ArrowUp));
        assert_eq!(key_code_from_str("esc"), Some(KeyCode::Escape));
        assert_eq!(key_code_from_str("f5"), Some(KeyCode::F5));
        assert_eq!(key_code_from_str("F24"), Some(KeyCode::F24));
        assert_eq!(key_code_from_str("7"), Some(KeyCode::Digit7));
        assert_eq!(key_code_from_str("numpad3"), Some(KeyCode::Numpad3));
        assert_eq!(key_code_from_str("lshift"), Some(KeyCode::ShiftLeft));
        assert_eq!(key_code_from_str("="), Some(KeyCode::Equal));
        assert_eq!(key_code_from_str("f"), Some(KeyCode::KeyF));
        // Fóra do teclado = None.
        assert_eq!(key_code_from_str("zzz"), None);
        assert_eq!(key_code_from_str(""), None);
        assert_eq!(input_code_from_str("mouse1"), Some(InputCode::Mouse(bevy::input::mouse::MouseButton::Left)));
        assert_eq!(input_code_from_str("rmb"), Some(InputCode::Mouse(bevy::input::mouse::MouseButton::Right)));
        // "left" é a SETA (teclas ganham), não o botão do rato.
        assert_eq!(input_code_from_str("left"), Some(InputCode::Key(KeyCode::ArrowLeft)));
        assert_eq!(input_code_from_str("mouse99"), None);
    }

    #[test]
    fn test_viber_input_reads_key_states() {
        // pressed/down/released com a W pressionada: pressed=true só no frame
        // do press; down=true enquanto held; released=true no frame do release.
        let host = host_with(
            "saw_pressed = false\n\
             saw_down = false\n\
             saw_released = false\n\
             function on_update(dt)\n\
               if viber.input.pressed('w') then saw_pressed = true end\n\
               if viber.input.down('w') then saw_down = true end\n\
               if viber.input.released('w') then saw_released = true end\n\
             end",
            "input.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Transform::from_xyz(0.0, 0.0, 0.0),
            LuaScriptRef {
                path: "input.lua".to_string(),
            },
        ));
        let mut keys = app
            .world_mut()
            .get_resource_mut::<ButtonInput<KeyCode>>()
            .expect("keys");
        keys.press(KeyCode::KeyW);
        app.update();
        assert!(app
            .world()
            .get_resource::<LuaScriptHost>()
            .unwrap()
            .script_global("input.lua", "saw_pressed")
            .is_ok_and(|v| v == mlua::Value::Boolean(true)));
        let mut keys = app
            .world_mut()
            .get_resource_mut::<ButtonInput<KeyCode>>()
            .expect("keys");
        keys.release(KeyCode::KeyW);
        app.update();
        let host = app.world().get_resource::<LuaScriptHost>().unwrap();
        assert_eq!(
            host.script_global("input.lua", "saw_down").unwrap(),
            mlua::Value::Boolean(true),
            "down durante o hold"
        );
        assert_eq!(
            host.script_global("input.lua", "saw_released").unwrap(),
            mlua::Value::Boolean(true),
            "released no frame do release"
        );
    }

    #[test]
    fn test_simple_rpg_npc_scripts_compile() {
        // Smoke dos scripts NPC do exemplo (fonte da verdade dos gestos/som):
        // compile-only — apanha erros de sintaxe sem correr o top-level.
        let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("examples")
            .join("simple-rpg")
            .join("scripts");
        let mut host = LuaScriptHost::new(base.clone()).expect("host");
        for path in [
            "townsfolk.lua",
            "merchant.lua",
            "healer.lua",
            "watch-guard.lua",
            "lib/fsm.lua",
            "game/shop.lua",
            "game/abilities.lua",
            "game/bomb-lua.lua",
            "game/hotbar.lua",
            "npc/forest-wolves.lua",
            "enemies/wolf.lua",
            "enemies/goblin.lua",
            "enemies/bandit.lua",
            "enemies/bogling.lua",
            "enemies/shade.lua",
            "enemies/slime.lua",
            "enemies/scorpion.lua",
            "bosses/boss.lua",
            "bosses/bog-warden.lua",
            "bosses/sand-worm.lua",
            "bosses/witch.lua",
        ] {
            host.load_script_from_dir(path)
                .unwrap_or_else(|e| panic!("{path} deve compilar: {e}"));
        }
    }

    #[test]
    fn test_timers_fire_and_cancel() {
        let host = host_with(
            "fired = 0\n\
             handle = nil\n\
             function on_update(dt)\n\
               if handle == nil then\n\
                 handle = viber.after(0.5, function() fired = fired + 1 end)\n\
                 viber.every(0.25, function() fired = fired + 10 end)\n\
               end\n\
             end",
            "timers.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "timers.lua".to_string(),
            },
        ));
        // Frame 1: regista os timers (tick corre antes dos on_update — nada
        // vence ainda).
        app.update();
        // +0.3 s: `every` (0.25) vence, `after` (0.5) ainda não.
        advance_time(&mut app, 0.3);
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        assert_eq!(
            host.script_global("timers.lua", "fired").unwrap(),
            mlua::Value::Integer(10),
            "every disparou, after não"
        );
        // +0.3 s (total 0.6): after dispara 1× e every volta a disparar.
        advance_time(&mut app, 0.3);
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        assert_eq!(
            host.script_global("timers.lua", "fired").unwrap(),
            mlua::Value::Integer(21)
        );
    }

    #[test]
    fn test_timer_cancel_stops_every() {
        let host = host_with(
            "fired = 0\n\
             armed = false\n\
             handle = nil\n\
             function on_update(dt)\n\
               if handle == nil and not armed then\n\
                 handle = viber.every(0.2, function() fired = fired + 1 end)\n\
                 armed = true\n\
               elseif fired > 0 and handle ~= nil then\n\
                 viber.timer_cancel(handle)\n\
                 handle = nil\n\
               end\n\
             end",
            "cancela.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "cancela.lua".to_string(),
            },
        ));
        advance_time(&mut app, 0.9);
        app.update(); // arma o `every` (vence a 1.1 s)
        advance_time(&mut app, 5.0);
        app.update(); // dispara 1× (re-agenda a 1.3) e o script CANCELA
        let host = app.world().resource::<LuaScriptHost>();
        let fired_after_cancel = host.script_global("cancela.lua", "fired").unwrap();
        assert_eq!(fired_after_cancel, mlua::Value::Integer(1), "disparou 1× antes do cancel");
        // Avança MUITO tempo: cancelado, não volta a disparar.
        advance_time(&mut app, 5.0);
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        assert_eq!(
            host.script_global("cancela.lua", "fired").unwrap(),
            fired_after_cancel,
            "timer cancelado não volta a disparar"
        );
    }

    #[test]
    fn test_entity_vitals_and_find() {
        let host = host_with(
            "hits = 0\n\
             hp = nil\n\
             function on_update(dt)\n\
               local id = viber.find('alvo')\n\
               if id and hits == 0 then\n\
                 viber.entity_set_max_hp(40, id)\n\
                 viber.entity_damage(25, id)\n\
                 hits = 1\n\
               elseif id and hits == 1 then\n\
                 local ok, cur, mx = viber.entity_hp(id)\n\
                 hp = (ok and cur) or -1\n\
               end\n\
             end",
            "vitais.lua",
        );
        let mut app = test_app(host);
        // Alvo NOMEADO sem Health — a API cria o Health no primeiro comando.
        app.world_mut().spawn((Name::new("alvo"), Transform::default()));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "vitais.lua".to_string(),
            },
        ));
        // Frame 1: find + comandos (aplicados pós-frame no MESMO frame).
        app.update();
        {
            let mut q = app.world_mut().query::<&Health>();
            let h = q.single(app.world()).expect("Health criado pelo set_max_hp");
            assert_eq!((h.current, h.max), (15.0, 40.0));
        }
        // Frame 2: leitura do snapshot (entity_hp) confirma 15.
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        // mlua/Luau normaliza 15.0 para Integer — aceita ambos.
        match host.script_global("vitais.lua", "hp").unwrap() {
            mlua::Value::Integer(n) => assert_eq!(n, 15),
            mlua::Value::Number(n) => assert!((n - 15.0).abs() < 1e-4, "hp={n}"),
            other => panic!("esperava HP=15, got {other:?}"),
        }
    }

    #[test]
    fn test_game_state_and_load_module() {
        // Módulo em disco carregado por DOIS scripts: mesma tabela (cache) —
        // e o estado de jogo world-scoped liga os dois.
        let dir = std::env::temp_dir().join(format!("viber-luau-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("lib_ok.lua"), "return {}").unwrap();
        let mut host = LuaScriptHost::new(dir.clone()).expect("host");
        host.load_script(
            "a.lua",
            "m = viber.load('lib_ok.lua')\n\
             m.a = 1\n\
             viber.game().arrancou = true\n\
             function on_update(dt) end",
        )
        .unwrap();
        host.load_script(
            "b.lua",
            "m2 = viber.load('lib_ok.lua')\n\
             function on_update(dt)\n\
               if m2.a == 1 and viber.game().arrancou then viber.game().ok = true end\n\
             end",
        )
        .unwrap();
        let mut app = test_app(host);
        for path in ["a.lua", "b.lua"] {
            app.world_mut().spawn((
                Transform::default(),
                LuaScriptRef {
                    path: path.to_string(),
                },
            ));
        }
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        let game: Table = host.lua.named_registry_value("viber_game").unwrap();
        assert!(
            matches!(game.raw_get::<bool>("ok"), Ok(true)),
            "módulo partilhado + game() world-scoped"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Spec mínima de caixa para os testes de spawn de prototypes.
    fn simple_box_spec() -> crate::recipes::EntitySpec {
        crate::recipes::EntitySpec {
            name: Some("crystal".to_string()),
            tag: None,
            script: None,
            destructible: None,
            transform: crate::recipes::TransformSpec::default(),
            physics: crate::physics::PhysicsSpec::default(),
            kind: crate::recipes::EntityKind::Primitive {
                shape: crate::recipes::Shape::Cuboid {
                    half_size: [0.5, 0.5, 0.5],
                },
                material: crate::recipes::MaterialSpec::default(),
            },
            children: Vec::new(),
        }
    }

    #[test]
    fn test_spawn_prototype_queues_known_request() {
        let host = host_with(
            "function on_update(dt)\n\
               viber.spawn_prototype('crystal', 5, -3)\n\
               viber.spawn_prototype('crystal', 1, 2, { y = 7.5 })\n\
             end",
            "spawnq.lua",
        );
        let mut app = test_app(host);
        let mut lib = crate::recipes::spawn::PrototypeLibrary::default();
        lib.0.insert("crystal".to_string(), simple_box_spec());
        app.insert_resource(lib);
        app.init_resource::<crate::recipes::spawn::PendingScriptSpawns>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "spawnq.lua".to_string(),
            },
        ));
        app.update();
        let queue = app
            .world()
            .resource::<crate::recipes::spawn::PendingScriptSpawns>();
        assert_eq!(queue.0.len(), 2, "os dois pedidos entram na fila");
        assert_eq!(queue.0[0].name, "crystal");
        assert!(queue.0[0].seat, "sem y = assenta no terreno");
        assert_eq!(queue.0[0].pos, Vec3::new(5.0, 0.0, -3.0));
        assert!(!queue.0[1].seat, "y explícito = cota fixa");
        assert_eq!(queue.0[1].pos, Vec3::new(1.0, 7.5, 2.0));
        assert!(queue.0[0].on_spawned.is_none());
    }

    /// `viber.terrain.lower/raise/crater` enfileiram `ScriptCommand::TerrainEdit`
    /// e o arm empurra-os para a `TerrainEditQueue` (sem a queue registada, o
    /// pedido é ignorado com aviso — nunca aceite em silêncio).
    #[test]
    fn test_viber_terrain_edits_reach_the_queue() {
        let host = host_with(
            "local g = viber.game()\n\
             function on_update(dt)\n\
               if g.sent == nil then\n\
                 g.sent = true\n\
                 viber.terrain.lower(3, -4, 8, 3)\n\
                 viber.terrain.raise(10, 0, 5, 2)\n\
                 viber.terrain.crater(-2, 2, 12, 5)\n\
                 viber.terrain.flatten(0, 0, 6, 7.5)\n\
               end\n\
             end",
            "terrain_edits.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<crate::terrain::delta::TerrainEditQueue>();
        let entity = app
            .world_mut()
            .spawn((
                bevy::prelude::Transform::default(),
                LuaScriptRef {
                    path: "terrain_edits.lua".to_string(),
                },
                ScriptActivation { radius: 1000.0 },
            ))
            .id();
        app.world_mut()
            .resource_mut::<LuaScriptHost>()
            .activate(entity, "terrain_edits.lua")
            .expect("activate");
        app.update();
        let q = app
            .world()
            .resource::<crate::terrain::delta::TerrainEditQueue>();
        assert_eq!(q.pending.len(), 4, "os 4 pedidos entraram na fila");
        use crate::terrain::delta::TerrainEdit;
        let edits: Vec<TerrainEdit> = q.pending.iter().copied().collect();
        assert!(matches!(
            edits[0],
            TerrainEdit::Lower { radius: 8.0, depth: 3.0, .. }
        ));
        assert!(matches!(
            edits[1],
            TerrainEdit::Raise { radius: 5.0, height: 2.0, .. }
        ));
        assert!(matches!(
            edits[2],
            TerrainEdit::Crater { radius: 12.0, depth: 5.0, .. }
        ));
        assert!(matches!(
            edits[3],
            TerrainEdit::Flatten {
                radius: 6.0,
                height: Some(7.5),
                ..
            }
        ));
    }

    #[test]
    fn test_apply_script_spawns_creates_and_calls_back() {
        let host = host_with(
            "local g = viber.game()\n\
             function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               viber.spawn_prototype('crystal', 5, -3, {\n\
                 on_spawned = function(bits) g.spawned = bits end,\n\
               })\n\
             end",
            "spawn.lua",
        );
        let mut app = test_app(host);
        app.add_plugins(bevy::asset::AssetPlugin::default());
        app.init_asset::<Mesh>();
        app.init_asset::<StandardMaterial>();
        app.init_asset::<crate::sky::SkyMaterial>();
        app.init_resource::<crate::textures::WorldTiledTextures>();
        app.init_resource::<crate::recipes::spawn::PendingScriptSpawns>();
        let mut lib = crate::recipes::spawn::PrototypeLibrary::default();
        lib.0.insert("crystal".to_string(), simple_box_spec());
        app.insert_resource(lib);
        app.add_systems(
            bevy::app::PostUpdate,
            crate::recipes::spawn::apply_script_spawns,
        );
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "spawn.lua".to_string(),
            },
        ));
        app.update();
        // A entidade nasceu na posição pedida (sem terreno = cota 0) e a
        // callback correu com os bits dela.
        let mut q = app.world_mut().query::<(Entity, &Transform, &Name)>();
        let spawned: Vec<(Entity, Vec3)> = q
            .iter(app.world())
            .filter(|(_, _, name)| name.as_str() == "crystal")
            .map(|(e, t, _)| (e, t.translation))
            .collect();
        assert_eq!(spawned.len(), 1, "um cristal criado pelo sistema exclusivo");
        assert_eq!(spawned[0].1, Vec3::new(5.0, 0.0, -3.0));
        let host = app.world().resource::<LuaScriptHost>();
        let game: Table = host.lua.named_registry_value("viber_game").unwrap();
        let bits: i64 = game.raw_get("spawned").expect("callback on_spawned correu");
        assert_eq!(bits as u64, spawned[0].0.to_bits());
    }

    #[test]
    fn test_own_action_claims_ui_action() {
        let host = host_with(
            "viber.own_action('buy')\nviber.own_action('sell')\nfunction on_update(dt) end",
            "own.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<crate::ui::actions::UiActionOwners>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "own.lua".to_string(),
            },
        ));
        app.update();
        let owners = app.world().resource::<crate::ui::actions::UiActionOwners>();
        assert!(owners.0.contains("buy"), "buy reclamada: {:?}", owners.0);
        assert!(owners.0.contains("sell"), "sell reclamada");
    }

    #[test]
    fn test_vault_take_command_consumes_only_with_stock() {
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               viber.vault_take('gold', 4)\n\
               viber.vault_take('gold', 100)\n\
             end",
            "take.lua",
        );
        let mut app = test_app(host);
        let mut vault = crate::economy::Vault::default();
        vault.gold = 10;
        app.insert_resource(vault);
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "take.lua".to_string(),
            },
        ));
        app.update();
        assert_eq!(
            app.world().resource::<crate::economy::Vault>().gold,
            6,
            "4 consumidos; o pedido sem stock é no-op"
        );
    }

    #[test]
    fn test_event_queue_cap_drops_new_events() {
        let host = host_with(
            "subscribed = false\n\
             function on_update(dt)\n\
               if not subscribed then subscribed = true; viber.events() end\n\
             end",
            "cap.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<ScriptEventQueue>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "cap.lua".to_string(),
            },
        ));
        app.update(); // 1.º frame: subscreve (drena vazio)
        for i in 0..100 {
            app.world_mut()
                .resource_mut::<ScriptEventQueue>()
                .push(ScriptGameEvent::Kill {
                    name: format!("w{i}"),
                    entity: i as i64,
                });
        }
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        let queues: Table = host.lua.named_registry_value("viber_events").unwrap();
        let queue: Table = queues.raw_get("cap.lua").unwrap();
        assert_eq!(
            queue.raw_len(),
            EVENT_QUEUE_CAP,
            "transbordo descarta os eventos novos no cap"
        );
    }

    #[test]
    fn test_game_kv_roundtrip_ignores_complex_values() {
        let host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        host.lua
            .load(
                "viber.game().n = 7\n\
                 viber.game().s = 'x'\n\
                 viber.game().b = true\n\
                 viber.game().tbl = { 1, 2 }\n\
                 viber.game().fn = function() end",
            )
            .exec()
            .expect("chunk kv");
        let json = crate::luau::game::game_to_json(&host.lua);
        assert_eq!(json.get("n"), Some(&serde_json::json!(7)));
        assert_eq!(json.get("s"), Some(&serde_json::json!("x")));
        assert_eq!(json.get("b"), Some(&serde_json::json!(true)));
        assert!(
            !json.contains_key("tbl") && !json.contains_key("fn"),
            "tabelas/funções não são persistíveis: {json:?}"
        );
        // Repõe noutra VM: os primitivos voltam.
        let fresh = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        crate::luau::game::json_to_game(&fresh.lua, &json);
        let game: Table = fresh.lua.named_registry_value("viber_game").unwrap();
        assert_eq!(game.raw_get::<f64>("n").unwrap(), 7.0);
        assert_eq!(game.raw_get::<String>("s").unwrap(), "x");
        assert!(game.raw_get::<bool>("b").unwrap());
    }

    #[test]
    fn test_entity_damage_lethal_emits_kill_event() {
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               local id = viber.find('alvo')\n\
               viber.entity_set_max_hp(10, id)\n\
               viber.entity_damage(20, id)\n\
             end",
            "kill.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<ScriptEventQueue>();
        app.world_mut()
            .spawn((Name::new("alvo"), Transform::default()));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "kill.lua".to_string(),
            },
        ));
        app.update();
        let events = app.world().resource::<ScriptEventQueue>();
        assert!(
            events
                .0
                .iter()
                .any(|e| matches!(e, ScriptGameEvent::Kill { .. })),
            "HP a zero por entity_damage emite Kill: {:?}",
            events.0
        );
    }

    #[test]
    fn test_lua_demo_scripts_compile() {
        // Smoke dos scripts do mundo-demo (`gameplay: none`): compile-only —
        // apanha erros de sintaxe sem correr o top-level.
        let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("worlds")
            .join("lua-demo")
            .join("scripts");
        let mut host = LuaScriptHost::new(base.clone()).expect("host");
        for path in ["game.lua", "crystal.lua", "ui/hud.lua"] {
            host.load_script_from_dir(path)
                .unwrap_or_else(|e| panic!("{path} deve compilar: {e}"));
        }
    }

    /// Guard docs↔API: toda a função registada em `viber` tem de aparecer em
    /// `docs/LUA_API.md` (como `viber.<nome>`). Ao lado do guard do
    /// `viber.debug` (bridge) — a auto-descoberta do agente depende dos dois.
    /// Só cobre o NÚCLEO instalado no `LuaScriptHost::new`: `viber.ui` e
    /// `viber.profiler` instalam-se em runtime e têm as suas secções.
    #[test]
    fn test_docs_cover_registered_core_api() {
        let host = LuaScriptHost::new(PathBuf::from("scripts")).expect("host");
        let doc = std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("docs/LUA_API.md"),
        )
        .expect("docs/LUA_API.md legível");
        let viber: Table = host.lua.globals().get("viber").expect("tabela viber");
        let mut missing: Vec<String> = Vec::new();
        let mut checked = 0usize;
        for (name, value) in viber.pairs::<String, mlua::Value>().flatten() {
            if matches!(value, mlua::Value::Function(_)) {
                checked += 1;
                if !doc.contains(&format!("viber.{name}")) {
                    missing.push(format!("viber.{name}"));
                }
            }
        }
        missing.sort();
        // Enumeração vazia/qebrada não pode passar em silêncio: o núcleo
        // tem dezenas de funções (o valor exato não interessa).
        assert!(checked >= 40, "guarda vacuidade: só {checked} funções enumeradas");
        assert!(
            missing.is_empty(),
            "funções registadas SEM doc no LUA_API.md: {missing:?}"
        );
    }

    #[test]
    fn test_script_save_request_flags() {
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               viber.save()\n\
               viber.load_save()\n\
             end",
            "save.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<crate::save::SaveRequest>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "save.lua".to_string(),
            },
        ));
        app.update();
        let request = app.world().resource::<crate::save::SaveRequest>();
        assert!(request.save, "viber.save() marca o pedido de gravação");
        assert!(request.load, "viber.load_save() marca o pedido de carregamento");
    }

    #[test]
    fn test_own_system_claims_native_system() {
        let host = host_with(
            "viber.own_system('dialogue')\nfunction on_update(dt) end",
            "ownsys.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<crate::luau::ScriptSystemOwners>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "ownsys.lua".to_string(),
            },
        ));
        app.update();
        let owners = app.world().resource::<crate::luau::ScriptSystemOwners>();
        assert!(owners.owns("dialogue"), "diálogo reclamado: {:?}", owners.0);
        assert!(!owners.owns("harvest"));
        // Um segundo script reclama harvest+hotbar (independentes).
        let mut lib = app.world_mut().resource_mut::<crate::luau::ScriptSystemOwners>();
        lib.0.insert("harvest".into());
        lib.0.insert("hotbar".into());
        assert!(lib.owns("harvest") && lib.owns("hotbar"));
    }

    #[test]
    fn test_say_writes_native_balloon() {
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               viber.say('“Lobos ao norte.”')\n\
             end",
            "say.lua",
        );
        let mut app = test_app(host);
        // Balão nativo: visibilidade + HudBalloon + primeiro filho Text.
        let parent = app
            .world_mut()
            .spawn((Visibility::Hidden, crate::hud::HudBalloon { timer: 0.0 }))
            .with_children(|p| {
                p.spawn(Text::new(""));
            })
            .id();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "say.lua".to_string(),
            },
        ));
        app.update();
        let (vis, balloon) = {
            let entity = app.world().entity(parent);
            (
                *entity.get::<Visibility>().unwrap(),
                entity.get::<crate::hud::HudBalloon>().unwrap().timer,
            )
        };
        assert_eq!(vis, Visibility::Visible, "o balão aparece");
        assert!(
            (balloon - crate::hud::BALLOON_DURATION).abs() < 1e-4,
            "duração por omissão: {balloon}"
        );
        let mut q = app.world_mut().query::<&Text>();
        let texts: Vec<String> = q.iter(app.world()).map(|t| t.0.clone()).collect();
        assert!(
            texts.iter().any(|t| t.contains("Lobos ao norte")),
            "texto no balão: {texts:?}"
        );
    }

    #[test]
    fn test_quest_defs_snapshot() {
        let host = host_with(
            "title = nil\ncount = 0\nkind = nil\n\
             function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               local d = viber.quest_def('forest_wolves')\n\
               if d then title = d.title kind = d.kind end\n\
               count = #viber.quest_defs()\n\
             end",
            "qdef.lua",
        );
        let mut app = test_app(host);
        app.insert_resource(crate::quests::QuestLog::with_dir(
            &crate::quests::example_quests_dir(),
        ));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "qdef.lua".to_string(),
            },
        ));
        // A 1.ª passagem semeia o snapshot; o script lê no MESMO frame
        // (a semeadura corre antes dos on_update).
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("qdef.lua", "title").unwrap() {
            mlua::Value::String(t) => assert!(t.to_string_lossy().contains("Hald"), "{t:?}"),
            other => panic!("título da quest em falta: {other:?}"),
        }
        match host.script_global("qdef.lua", "kind").unwrap() {
            mlua::Value::String(k) => assert_eq!(k.to_string_lossy(), "kill"),
            other => panic!("objetivo em falta: {other:?}"),
        }
        match host.script_global("qdef.lua", "count").unwrap() {
            mlua::Value::Integer(n) => assert!(n >= 25, "todas as quests do disco: {n}"),
            other => panic!("contagem: {other:?}"),
        }
    }

    #[test]
    fn test_radial_damage_damages_and_kills() {
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               viber.radial_damage(0, 0, 5, 100, { knockback = 7 })\n\
             end",
            "radial.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<ScriptEventQueue>();
        // Dois alvos vivos em Health: um no centro (morre), outro fora do
        // raio (intocado).
        let alvo = app
            .world_mut()
            .spawn((
                Name::new("alvo"),
                Transform::from_xyz(1.0, 0.0, 0.0),
                // GlobalTransform explícito: a app mínima não tem o
                // TransformPlugin a propagar (as queries leem o global).
                GlobalTransform::from_xyz(1.0, 0.0, 0.0),
                Health {
                    current: 30.0,
                    max: 30.0,
                },
            ))
            .id();
        let longe = app
            .world_mut()
            .spawn((
                Name::new("longe"),
                Transform::from_xyz(20.0, 0.0, 0.0),
                GlobalTransform::from_xyz(20.0, 0.0, 0.0),
                Health {
                    current: 30.0,
                    max: 30.0,
                },
            ))
            .id();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "radial.lua".to_string(),
            },
        ));
        app.update();
        assert!(
            app.world().entity(alvo).get::<crate::combat::Corpse>().is_some(),
            "o alvo no centro morre com paridade nativa (Corpse)"
        );
        assert!(
            app.world().get::<Health>(longe).is_some_and(|h| h.current == 30.0),
            "fora do raio fica intacto"
        );
        let events = app.world().resource::<ScriptEventQueue>();
        assert!(
            events.0.iter().any(|e| matches!(e, ScriptGameEvent::Kill { .. })),
            "a morte emite Kill: {:?}",
            events.0
        );
    }

    #[test]
    fn test_entity_position_and_nearby_snapshot() {
        let host = host_with(
            "pos = nil\nnear = 0\nnome = nil\n\
             function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               local id = viber.find('alvo')\n\
               local ok, x, y, z = viber.entity_position(id)\n\
               if ok then pos = math.floor(x) end\n\
               local list = viber.nearby(10)\n\
               near = #list\n\
               if list[1] then nome = list[1].name end\n\
             end",
            "pos.lua",
        );
        let mut app = test_app(host);
        app.world_mut().spawn((
            Name::new("alvo"),
            Transform::from_xyz(4.0, 0.0, 0.0),
            GlobalTransform::from_xyz(4.0, 0.0, 0.0),
        ));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "pos.lua".to_string(),
            },
        ));
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("pos.lua", "pos").unwrap() {
            mlua::Value::Integer(n) => assert_eq!(n, 4, "x do alvo lido do snapshot"),
            other => panic!("posição em falta: {other:?}"),
        }
        match host.script_global("pos.lua", "near").unwrap() {
            mlua::Value::Integer(n) => assert_eq!(n, 1, "só o alvo está a <10 m"),
            other => panic!("nearby em falta: {other:?}"),
        }
        match host.script_global("pos.lua", "nome").unwrap() {
            mlua::Value::String(n) => assert_eq!(n.to_string_lossy(), "alvo"),
            other => panic!("nome em falta: {other:?}"),
        }
    }

    #[test]
    fn test_play_clip_on_foreign_entity_warns_not_panics() {
        // Entidade alvo SEM animator: `play_clip` com `id` degrada para warn
        // 1× (nunca panica nem derruba o frame).
        let host = host_with(
            "function on_update(dt)\n\
               local st = viber.state()\n\
               if st.done then return end\n\
               st.done = true\n\
               local id = viber.find('toco')\n\
               viber.play_clip('sworda,swordb', { id = id, speed = 1.4 })\n\
             end",
            "clip.lua",
        );
        let mut app = test_app(host);
        app.world_mut()
            .spawn((Name::new("toco"), Transform::default()));
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "clip.lua".to_string(),
            },
        ));
        app.update();
        app.update();
        assert!(
            app.world().resource::<LuaScriptHost>().registry.contains("clip.lua"),
            "o script continua carregado depois do warn"
        );
    }

    #[test]
    fn test_invalid_entity_id_is_a_script_error_not_a_panic() {
        // 32 bits baixos a zero = índice inválido: `Entity::from_bits` panicava.
        let mut host = host_with(
            "ok_despawn = pcall(viber.entity_despawn, 0)\n\
             ok_hp = pcall(viber.entity_set_max_hp, 10, 4294967296)\n\
             ok_clip = pcall(viber.play_clip, 'wave', { id = 0 })",
            "ids.lua",
        );
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "ids.lua").expect("activate");
        for key in ["ok_despawn", "ok_hp", "ok_clip"] {
            assert_eq!(
                host.script_global("ids.lua", key).unwrap(),
                mlua::Value::Boolean(false),
                "{key}: id inválido tem de falhar como erro Lua"
            );
        }
    }

    #[test]
    fn test_infinite_loop_is_interrupted_by_the_cpu_budget() {
        let mut host = host_with("function on_update(dt) while true do end end", "loop.lua");
        let mut world = World::new();
        let entity = world.spawn_empty().id();
        host.activate(entity, "loop.lua").expect("activate");
        let t0 = std::time::Instant::now();
        let err = host
            .run_update(entity, "loop.lua", 0.016, Vec3::ZERO, None, 0.0)
            .expect_err("o loop infinito tem de ser interrompido");
        assert!(err.to_string().contains("orçamento"), "{err}");
        assert!(t0.elapsed() < Duration::from_secs(5));
        // Fora de uma chamada guardada não há prazo pendurado.
        host.load_script("after.lua", "x = 0 for i = 1, 100000 do x = x + i end")
            .unwrap();
        host.activate(entity, "after.lua").expect("sem prazo stale");
    }

    #[test]
    fn test_sandbox_blocks_require_and_shared_stdlib_writes() {
        let mut host = host_with(
            "has_require = require ~= nil\n\
             has_package = package ~= nil\n\
             ok_clobber = pcall(function() string.upper = nil end)\n\
             ok_load = pcall(viber.load, '../../etc/passwd')\n\
             ok_abs = pcall(viber.load, '/etc/passwd')",
            "sandbox.lua",
        );
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "sandbox.lua").expect("activate");
        for key in ["has_require", "has_package", "ok_clobber", "ok_load", "ok_abs"] {
            assert_eq!(
                host.script_global("sandbox.lua", key).unwrap(),
                mlua::Value::Boolean(false),
                "{key}"
            );
        }
    }

    #[test]
    fn test_load_module_without_return_runs_once() {
        let dir = std::env::temp_dir().join(format!("viber-luau-once-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("side.lua"),
            "viber.game().runs = (viber.game().runs or 0) + 1",
        )
        .unwrap();
        let mut host = LuaScriptHost::new(dir.clone()).expect("host");
        host.load_script(
            "user.lua",
            "a = viber.load('side.lua')\nb = viber.load('side.lua')",
        )
        .unwrap();
        let mut world = World::new();
        host.activate(world.spawn_empty().id(), "user.lua").expect("activate");
        let game: Table = host.lua.named_registry_value("viber_game").unwrap();
        assert_eq!(game.raw_get::<i64>("runs").unwrap(), 1);
        assert_eq!(
            host.script_global("user.lua", "b").unwrap(),
            mlua::Value::Boolean(true)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_timers_die_with_their_owner() {
        let host = host_with(
            "function on_update(dt)\n\
               if not armed then armed = true viber.every(0.1, function() end) end\n\
             end",
            "orfao.lua",
        );
        let mut app = test_app(host);
        let owner = app
            .world_mut()
            .spawn((
                Transform::default(),
                LuaScriptRef {
                    path: "orfao.lua".to_string(),
                },
            ))
            .id();
        app.update();
        let pending = |app: &bevy::app::App| {
            let host = app.world().resource::<LuaScriptHost>();
            crate::luau::timers::timer_ids_where(&host.lua, |_, _| true).len()
        };
        assert_eq!(pending(&app), 1);
        app.world_mut().despawn(owner);
        app.update();
        assert_eq!(pending(&app), 0, "timer órfão sai com o dono");
    }

    #[test]
    fn test_timer_callback_sees_its_owner_ctx() {
        let host = host_with(
            "function on_update(dt)\n\
               if not armed then\n\
                 armed = true\n\
                 armed_x = viber.position()\n\
                 viber.after(0, function()\n\
                   local x = viber.position()\n\
                   seen = seen or {}\n\
                   seen[#seen + 1] = x\n\
                 end)\n\
               end\n\
             end",
            "dono.lua",
        );
        let mut app = test_app(host);
        for x in [3.0, -7.0] {
            app.world_mut().spawn((
                Transform::from_xyz(x, 0.0, 0.0),
                GlobalTransform::from(Transform::from_xyz(x, 0.0, 0.0)),
                LuaScriptRef {
                    path: "dono.lua".to_string(),
                },
            ));
        }
        app.update();
        advance_time(&mut app, 0.1);
        app.update();
        let host = app.world().resource::<LuaScriptHost>();
        let mlua::Value::Table(seen) = host.script_global("dono.lua", "seen").unwrap() else {
            panic!("timer não correu");
        };
        // Globals partilhados pelo path: só o 1.º on_update arma; o timer
        // tem de ver a posição DESSE dono, não a da última entidade.
        let xs: Vec<f64> = seen.sequence_values::<f64>().flatten().collect();
        let armed_x: f64 = match host.script_global("dono.lua", "armed_x").unwrap() {
            mlua::Value::Integer(n) => n as f64,
            mlua::Value::Number(n) => n,
            other => panic!("armed_x: {other:?}"),
        };
        assert_eq!(xs, vec![armed_x], "origin do dono, não a da última entidade");
    }

    #[test]
    fn test_script_kill_is_emitted_once_with_the_script_kind() {
        let host = host_with(
            "function on_update(dt)\n\
               if not hit then\n\
                 hit = true\n\
                 viber.entity_set_max_hp(10)\n\
                 viber.entity_damage(15)\n\
                 viber.entity_damage(15)\n\
               end\n\
             end",
            "creatures/goblin.lua",
        );
        let mut app = test_app(host);
        app.init_resource::<ScriptEventQueue>();
        app.world_mut().spawn((
            Transform::default(),
            LuaScriptRef {
                path: "creatures/goblin.lua".to_string(),
            },
        ));
        app.update();
        let kills: Vec<String> = app
            .world()
            .resource::<ScriptEventQueue>()
            .0
            .iter()
            .filter_map(|e| match e {
                ScriptGameEvent::Kill { name, .. } => Some(name.clone()),
                _ => None,
            })
            .collect();
        assert_eq!(kills, vec!["goblin".to_string()]);
    }
