//! Combate melee headless: press [J] → soft-lock → chain (×1/×1.15/×1.7) →
//! execute → corpse + XP. Reproduz o fluxo completo do melee com uma App
//! mínima (sem janela nem render) — regressão do "não consigo atacar os
//! monstros": o herói virado para +Z TEM de acertar um inimigo a +Z (o
//! modelo olha +Z; o melee antigo media o cone contra −Z).

use std::time::Duration;

use bevy::app::TaskPoolPlugin;
use bevy::ecs::message::Messages;
use bevy::input::keyboard::{Key, KeyCode as _KeyCode, KeyboardInput, NativeKey};
use bevy::input::mouse::MouseButtonInput;
use bevy::input::{ButtonState, InputPlugin};
use bevy::prelude::*;
use bevy::time::TimePlugin;

use viber::combat;
use viber::luau::LuaScriptRef;
use viber::menus::MenusOpen;
use viber::player::{Player, facing_rotation};
use viber::skills::PlayerStatsResource;
use viber::vitals::{Health, Xp};

/// App mínima com o CombatPlugin: input de teclado manual (sem winit),
/// relógio real (TimePlugin) e transform propagation.
fn combat_app() -> App {
    let mut app = App::new();
    app.add_plugins((TaskPoolPlugin::default(), TimePlugin, InputPlugin));
    // Assets para o cycle_weapon / fireball / bursts (sem render).
    app.add_plugins(bevy::asset::AssetPlugin::default());
    {
        use bevy::asset::AssetApp;
        app.init_asset::<Mesh>();
        app.init_asset::<StandardMaterial>();
    }
    app.init_resource::<MenusOpen>();
    app.init_resource::<PlayerStatsResource>();
    // Kick de câmara do passe de juice — o swing_track e o fireball_step
    // escrevem-no em cada golpe (e o kick de exposição lê o PostFxState).
    app.init_resource::<viber::camera::CameraKick>();
    app.init_resource::<viber::postfx::PostFxState>();
    // BGM de combate: o aggro/kill do melee promove a layer `battle` — no
    // jogo o recurso nasce no AiPlugin, ausente nesta App mínima.
    app.init_resource::<viber::music::CombatMusicState>();
    // Sem críticos: chance negativa → roll nunca passa (dano determinístico).
    app.world_mut()
        .resource_mut::<PlayerStatsResource>()
        .0
        .crit_bonus = -1.0;
    app.add_plugins(bevy::transform::TransformPlugin);
    app.add_plugins(combat::CombatPlugin);
    app
}

/// Herói na origem olhando +Z + lobo a 3 m em +Z virado de costas (sem
/// backstab). Devolve as entidades.
fn spawn_hero_and_wolf(app: &mut App) -> (Entity, Entity) {
    let hero = app
        .world_mut()
        .spawn((
            Player::default(),
            Transform::from_rotation(facing_rotation(Vec3::Z)),
            GlobalTransform::IDENTITY,
            Name::new("player"),
        ))
        .id();
    let wolf = app
        .world_mut()
        .spawn((
            LuaScriptRef {
                path: "enemies/wolf.lua".into(),
            },
            // De costas para o herói: forward −Z, sem backstab.
            Transform::from_xyz(0.0, 0.0, 3.0).with_rotation(facing_rotation(Vec3::NEG_Z)),
            GlobalTransform::from_xyz(0.0, 0.0, 3.0),
            Name::new("wolf"),
        ))
        .id();
    (hero, wolf)
}

/// Um frame de press [J] via EVENTO de teclado (o InputPlugin limpa o
/// just_pressed no PreUpdate — um press manual nunca chegava ao Update).
fn press_j(app: &mut App) {
    app.world_mut()
        .resource_mut::<Messages<KeyboardInput>>()
        .write(KeyboardInput {
            key_code: _KeyCode::KeyJ,
            logical_key: Key::Unidentified(NativeKey::Unidentified),
            state: ButtonState::Pressed,
            text: None,
            window: Entity::PLACEHOLDER,
            repeat: false,
        });
    app.update();
    app.world_mut()
        .resource_mut::<Messages<KeyboardInput>>()
        .write(KeyboardInput {
            key_code: _KeyCode::KeyJ,
            logical_key: Key::Unidentified(NativeKey::Unidentified),
            state: ButtonState::Released,
            text: None,
            window: Entity::PLACEHOLDER,
            repeat: false,
        });
    app.update();
}

/// Deixa o relógio avançar `secs` reais com updates (o impacto do swing
/// aterra a ~0.22 s do press sem clip de animação conhecido).
fn advance(app: &mut App, secs: f32) {
    let mut elapsed = 0.0_f32;
    while elapsed < secs {
        std::thread::sleep(Duration::from_millis(30));
        app.update();
        elapsed += 0.03;
    }
}

fn wolf_health(app: &mut App, wolf: Entity) -> Option<f32> {
    app.world().get::<Health>(wolf).map(|h| h.current)
}

#[test]
fn test_melee_chain_finisher_execute_and_kill() {
    let mut app = combat_app();
    let (hero, wolf) = spawn_hero_and_wolf(&mut app);
    // vitals do herói/inimigo inseridos no primeiro update.
    app.update();
    assert_eq!(wolf_health(&mut app, wolf), Some(100.0), "lobo a 100 HP");

    // ── swing 1: chain passo 0 (×1), combo 0 hits → 25 de dano ──
    press_j(&mut app);
    advance(&mut app, 0.7);
    let hp = wolf_health(&mut app, wolf).expect("Health inserido pelo ensure_creature_vitals");
    assert!((hp - 75.0).abs() < 1e-2, "1.º golpe: -25 (got {hp})");

    // ── swing 2 (dentro da chain window): passo 1 ×1.15 × combo 1.02 ──
    std::thread::sleep(Duration::from_millis(400));
    press_j(&mut app);
    advance(&mut app, 0.7);
    let hp = wolf_health(&mut app, wolf).unwrap();
    let expected = 100.0 - 25.0 - 25.0 * 1.15 * viber::combat::combo_bonus(1);
    assert!(
        (hp - expected).abs() < 1e-2,
        "2.º golpe: -{expected} (got {hp})"
    );

    // ── swing 3: finisher ×1.7 × combo 1.04; ainda sem execute ──
    std::thread::sleep(Duration::from_millis(400));
    press_j(&mut app);
    advance(&mut app, 0.7);
    let hp = wolf_health(&mut app, wolf).unwrap();
    let expected = expected - 25.0 * 1.7 * viber::combat::combo_bonus(2);
    assert!(
        (hp - expected).abs() < 1e-2,
        "3.º golpe (finisher): {expected} (got {hp})"
    );
    assert!(hp > 0.0, "ainda vivo antes do execute");

    // ── swing 4: abaixo de 15 % → execute (morte na hora) ──
    std::thread::sleep(Duration::from_millis(400));
    press_j(&mut app);
    advance(&mut app, 0.7);
    assert_eq!(
        wolf_health(&mut app, wolf),
        Some(0.0),
        "execute: HP a 0 no 4.º golpe"
    );
    assert!(
        app.world().get::<combat::Corpse>(wolf).is_some(),
        "lobo vira cadáver (anima death + despawn)"
    );
    let xp = app.world().get::<Xp>(hero).expect("XP do herói");
    assert_eq!(xp.current, combat::KILL_XP, "+XP por abate");
}

#[test]
fn test_melee_softlock_hits_enemy_behind_the_model() {
    // O press não precisa de mirar: o soft-lock apanha o inimigo num círculo
    // COMPLETO (LOCK_RANGE) e o windup vira o herói para ele.
    let mut app = combat_app();
    let (hero, wolf) = spawn_hero_and_wolf(&mut app);
    // Lobo ATRÁS do herói (a −Z do forward +Z): o melee antigo nunca acertava.
    app.world_mut()
        .get_mut::<Transform>(wolf)
        .unwrap()
        .translation = Vec3::new(0.0, 0.0, -3.0);
    // O GlobalTransform re-sincroniza no próximo frame (TransformPlugin).
    app.update();
    app.update();
    press_j(&mut app);
    advance(&mut app, 0.7);
    let hp = wolf_health(&mut app, wolf).unwrap();
    assert!(hp < 100.0, "soft-lock acerta inimigo atrás (got {hp})");
    // E o herói virou-se para ele durante o windup.
    let rotation = app.world().get::<Transform>(hero).unwrap().rotation;
    let faced = rotation * Vec3::Z;
    assert!(faced.z < -0.5, "herói virou-se para o alvo: {faced:?}");
    let _ = hero;
}

#[test]
fn test_fireball_flies_forward_plus_z() {
    // O modelo olha +Z: a bola de fogo (botão direito) sai PARA A FRENTE —
    // antes voava para trás (GlobalTransform::forward() = −Z).
    let mut app = combat_app();
    let (_, wolf) = spawn_hero_and_wolf(&mut app);
    app.update();
    app.world_mut()
        .resource_mut::<Messages<MouseButtonInput>>()
        .write(MouseButtonInput {
            button: bevy::input::mouse::MouseButton::Right,
            state: ButtonState::Pressed,
            window: Entity::PLACEHOLDER,
        });
    app.update();
    app.world_mut()
        .resource_mut::<Messages<MouseButtonInput>>()
        .write(MouseButtonInput {
            button: bevy::input::mouse::MouseButton::Right,
            state: ButtonState::Released,
            window: Entity::PLACEHOLDER,
        });
    app.update();
    let mut balls = app
        .world_mut()
        .query_filtered::<&Transform, With<combat::Fireball>>();
    let fireball = balls
        .single(app.world())
        .expect("fireball spawna no clique direito")
        .translation;
    assert!(
        fireball.z > 0.5,
        "bola de fogo à FRENTE do herói (+Z), got {fireball:?}"
    );
    let vel = app
        .world_mut()
        .query_filtered::<&combat::Fireball, ()>()
        .single(app.world())
        .expect("fireball component")
        .vel;
    assert!(vel.z > 0.0, "velocidade para +Z, got {vel:?}");
    let _ = wolf;
}
