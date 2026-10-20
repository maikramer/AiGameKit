//! Contactos Rapier → hooks Luau.
//!
//! `CollisionEvent` cobre sensores (o herói atravessa-os: o character
//! controller exclui sensores) e corpos dinâmicos. O herói contra um sólido
//! pára ~2 cm antes do overlap, por isso esse par entra também pelos hits do
//! `KinematicCharacterControllerOutput`. Um par fica activo enquanto QUALQUER
//! fonte o vir — os dois caminhos não disparam o enter duas vezes.

use std::collections::{HashMap, HashSet};

use bevy::prelude::*;
use bevy_rapier3d::prelude::{
    CollisionEvent, KinematicCharacterControllerOutput, Sensor,
};
use bevy_rapier3d::rapier::geometry::CollisionEventFlags;

use super::components::LuaScriptRef;
use super::host::{ContactHook, LuaScriptHost};

/// Par (dono do script, outra entidade) ainda em contacto.
#[derive(Resource, Default)]
pub struct ContactBook {
    pairs: HashMap<(Entity, Entity), LivePair>,
}

#[derive(Clone, Copy)]
struct LivePair {
    event: bool,
    controller: bool,
    trigger: bool,
}

impl LivePair {
    fn active(self) -> bool {
        self.event || self.controller
    }
}

#[derive(Clone, Copy)]
enum Source {
    Event,
    Controller,
}

struct OtherBody {
    id: i64,
    name: Option<String>,
    pos: Vec3,
}

/// Depois do step do Rapier: eventos deste frame + hits do controller.
pub fn dispatch_contacts(
    mut events: MessageReader<CollisionEvent>,
    time: Res<Time>,
    mut book: ResMut<ContactBook>,
    mut host: ResMut<LuaScriptHost>,
    scripts: Query<&LuaScriptRef>,
    names: Query<&Name>,
    globals: Query<&GlobalTransform>,
    parents: Query<&ChildOf>,
    sensors: Query<(), With<Sensor>>,
    controllers: Query<(Entity, &KinematicCharacterControllerOutput)>,
) {
    let dt = time.delta_secs();
    // Dono despawnado: o exit já não tem para quem correr.
    book.pairs
        .retain(|(owner, _), _| scripts.get(*owner).is_ok());

    let mut fires: Vec<(Entity, ContactHook, OtherBody)> = Vec::new();

    for event in events.read() {
        let (a, b, flags, started) = match *event {
            CollisionEvent::Started(a, b, flags) => (a, b, flags, true),
            CollisionEvent::Stopped(a, b, flags) => (a, b, flags, false),
        };
        let trigger = flags.contains(CollisionEventFlags::SENSOR);
        note(
            &mut book,
            &scripts,
            &names,
            &globals,
            &parents,
            a,
            b,
            trigger,
            started,
            Source::Event,
            &mut fires,
        );
    }

    let mut seen: HashSet<(Entity, Entity)> = HashSet::new();
    for (hero, output) in &controllers {
        for hit in &output.collisions {
            if is_sensor(hit.entity, &sensors, &parents) {
                continue;
            }
            let Some(owner) = script_owner(hit.entity, &scripts, &parents) else {
                continue;
            };
            if owner == hero {
                continue;
            }
            seen.insert((owner, hero));
            touch(
                &mut book,
                &scripts,
                &names,
                &globals,
                &parents,
                owner,
                hero,
                false,
                true,
                Source::Controller,
                &mut fires,
            );
        }
    }
    let release: Vec<(Entity, Entity)> = book
        .pairs
        .iter()
        .filter(|((owner, other), live)| live.controller && !seen.contains(&(*owner, *other)))
        .map(|(key, _)| *key)
        .collect();
    for (owner, other) in release {
        let trigger = book.pairs.get(&(owner, other)).is_some_and(|p| p.trigger);
        touch(
            &mut book,
            &scripts,
            &names,
            &globals,
            &parents,
            owner,
            other,
            trigger,
            false,
            Source::Controller,
            &mut fires,
        );
    }

    let stays: Vec<(Entity, OtherBody, bool)> = book
        .pairs
        .iter()
        .filter(|(_, live)| live.active())
        .map(|((owner, other), live)| {
            (
                *owner,
                describe(*other, &names, &globals, &parents),
                live.trigger,
            )
        })
        .collect();

    for (owner, hook, other) in fires {
        call(&mut host, &scripts, &globals, owner, hook, &other, dt);
    }
    for (owner, other, trigger) in stays {
        call(
            &mut host,
            &scripts,
            &globals,
            owner,
            ContactHook::stay(trigger),
            &other,
            dt,
        );
    }
}

fn note(
    book: &mut ContactBook,
    scripts: &Query<&LuaScriptRef>,
    names: &Query<&Name>,
    globals: &Query<&GlobalTransform>,
    parents: &Query<&ChildOf>,
    a: Entity,
    b: Entity,
    trigger: bool,
    started: bool,
    source: Source,
    fires: &mut Vec<(Entity, ContactHook, OtherBody)>,
) {
    let owner_a = script_owner(a, scripts, parents);
    let owner_b = script_owner(b, scripts, parents);
    if let Some(owner) = owner_a
        && owner != b
    {
        touch(
            book,
            scripts,
            names,
            globals,
            parents,
            owner,
            b,
            trigger,
            started,
            source,
            fires,
        );
    }
    if let Some(owner) = owner_b
        && owner != a
        && owner_a != Some(owner)
    {
        touch(
            book,
            scripts,
            names,
            globals,
            parents,
            owner,
            a,
            trigger,
            started,
            source,
            fires,
        );
    }
}

#[allow(clippy::too_many_arguments)]
fn touch(
    book: &mut ContactBook,
    scripts: &Query<&LuaScriptRef>,
    names: &Query<&Name>,
    globals: &Query<&GlobalTransform>,
    parents: &Query<&ChildOf>,
    owner: Entity,
    other: Entity,
    trigger: bool,
    source_on: bool,
    source: Source,
    fires: &mut Vec<(Entity, ContactHook, OtherBody)>,
) {
    if scripts.get(owner).is_err() {
        return;
    }
    let other = subject(other, names, parents);
    if other == owner {
        return;
    }
    let key = (owner, other);
    let prev = book.pairs.get(&key).copied();
    let mut live = prev.unwrap_or(LivePair {
        event: false,
        controller: false,
        trigger,
    });
    if matches!(source, Source::Event) {
        live.trigger = trigger;
        live.event = source_on;
    } else {
        live.controller = source_on;
    }
    let was = prev.is_some_and(LivePair::active);
    let now = live.active();
    if now {
        book.pairs.insert(key, live);
    } else {
        book.pairs.remove(&key);
    }
    if was == now {
        return;
    }
    let hook = if now {
        ContactHook::enter(live.trigger)
    } else {
        ContactHook::exit(prev.map(|pair| pair.trigger).unwrap_or(live.trigger))
    };
    fires.push((
        owner,
        hook,
        describe(other, names, globals, parents),
    ));
}

fn call(
    host: &mut LuaScriptHost,
    scripts: &Query<&LuaScriptRef>,
    globals: &Query<&GlobalTransform>,
    owner: Entity,
    hook: ContactHook,
    other: &OtherBody,
    dt: f32,
) {
    let Ok(script) = scripts.get(owner) else {
        return;
    };
    let origin = globals
        .get(owner)
        .map(|global| global.translation())
        .unwrap_or(Vec3::ZERO);
    if let Err(error) = host.activate_at(owner, &script.path, origin) {
        host.warn_once(&script.path, &error);
        return;
    }
    if let Err(error) = host.run_contact(
        owner,
        &script.path,
        origin,
        hook,
        other.id,
        other.name.as_deref(),
        other.pos,
        dt,
    ) {
        host.warn_once(&script.path, &error);
    }
}

fn script_owner(start: Entity, scripts: &Query<&LuaScriptRef>, parents: &Query<&ChildOf>) -> Option<Entity> {
    let mut current = start;
    loop {
        if scripts.get(current).is_ok() {
            return Some(current);
        }
        current = parents.get(current).ok()?.parent();
    }
}

fn is_sensor(start: Entity, sensors: &Query<(), With<Sensor>>, parents: &Query<&ChildOf>) -> bool {
    let mut current = start;
    loop {
        if sensors.get(current).is_ok() {
            return true;
        }
        match parents.get(current) {
            Ok(parent) => current = parent.parent(),
            Err(_) => return false,
        }
    }
}

/// O filho de offset chama-se `collider`; o hook vê o pai.
fn subject(entity: Entity, names: &Query<&Name>, parents: &Query<&ChildOf>) -> Entity {
    if names.get(entity).is_ok_and(|name| name.as_str() == "collider")
        && let Ok(parent) = parents.get(entity)
    {
        return parent.parent();
    }
    entity
}

fn describe(
    entity: Entity,
    names: &Query<&Name>,
    globals: &Query<&GlobalTransform>,
    parents: &Query<&ChildOf>,
) -> OtherBody {
    let entity = subject(entity, names, parents);
    OtherBody {
        id: entity.to_bits() as i64,
        name: names
            .get(entity)
            .map(|name| name.as_str().to_string())
            .ok(),
        pos: globals
            .get(entity)
            .map(|global| global.translation())
            .unwrap_or(Vec3::ZERO),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::physics::stamp_collider_role;
    use bevy::time::{TimePlugin, TimeUpdateStrategy};
    use bevy_rapier3d::prelude::*;
    use std::time::Duration;

    fn physics_app(code: &str) -> App {
        let mut host = LuaScriptHost::new(std::path::PathBuf::from("scripts")).expect("host");
        host.load_script("zone.lua", code).expect("script");
        let mut app = App::new();
        app.add_plugins((
            TransformPlugin,
            TimePlugin,
            RapierPhysicsPlugin::<NoUserData>::default(),
        ));
        app.insert_resource(TimestepMode::Fixed {
            dt: 1.0 / 60.0,
            substeps: 1,
        });
        app.insert_resource(TimeUpdateStrategy::ManualDuration(Duration::from_secs_f32(
            1.0 / 60.0,
        )));
        app.insert_resource(host);
        app.init_resource::<ContactBook>();
        app.add_systems(PostUpdate, dispatch_contacts.after(PhysicsSet::Writeback));
        app
    }

    fn lua_count(app: &App, key: &str) -> i64 {
        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("zone.lua", key).expect("global") {
            mlua::Value::Integer(n) => n,
            mlua::Value::Number(n) => n as i64,
            mlua::Value::Nil => 0,
            other => panic!("{key} = {other:?}"),
        }
    }

    fn lua_string(app: &App, key: &str) -> String {
        let host = app.world().resource::<LuaScriptHost>();
        match host.script_global("zone.lua", key).expect("global") {
            mlua::Value::String(text) => text.to_str().map(|s| s.to_string()).unwrap_or_default(),
            mlua::Value::Nil => String::new(),
            other => panic!("{key} = {other:?}"),
        }
    }

    #[test]
    fn trigger_enter_and_exit_reach_the_script() {
        let mut app = physics_app(
            "entered = 0\n\
             exited = 0\n\
             last = ''\n\
             function on_trigger_enter(other)\n\
               entered = entered + 1\n\
               last = other.name or ''\n\
             end\n\
             function on_trigger_exit(other)\n\
               exited = exited + 1\n\
             end\n",
        );
        let zone = app
            .world_mut()
            .spawn((
                Transform::default(),
                RigidBody::Fixed,
                Collider::cuboid(1.0, 1.0, 1.0),
                LuaScriptRef {
                    path: "zone.lua".into(),
                },
                Name::new("zone"),
            ))
            .id();
        {
            let mut entity = app.world_mut().entity_mut(zone);
            stamp_collider_role(&mut entity, true, true);
        }
        assert!(app.world().get::<Sensor>(zone).is_some());
        let hero = app
            .world_mut()
            .spawn((
                Transform::from_xyz(6.0, 0.0, 0.0),
                RigidBody::KinematicPositionBased,
                Collider::ball(0.4),
                Name::new("hero"),
            ))
            .id();
        for _ in 0..5 {
            app.update();
        }
        assert_eq!(lua_count(&app, "entered"), 0, "ainda longe");
        app.world_mut()
            .get_mut::<Transform>(hero)
            .unwrap()
            .translation = Vec3::ZERO;
        for _ in 0..12 {
            app.update();
        }
        assert_eq!(lua_count(&app, "entered"), 1, "um enter");
        assert_eq!(lua_string(&app, "last"), "hero");
        app.world_mut()
            .get_mut::<Transform>(hero)
            .unwrap()
            .translation = Vec3::new(6.0, 0.0, 0.0);
        for _ in 0..12 {
            app.update();
        }
        assert_eq!(lua_count(&app, "exited"), 1, "um exit");
    }

    #[test]
    fn character_controller_hit_is_one_collision_enter() {
        let mut app = physics_app(
            "entered = 0\n\
             function on_collision_enter(other)\n\
               entered = entered + 1\n\
               last = other.name or ''\n\
             end\n",
        );
        let wall = app
            .world_mut()
            .spawn((
                Transform::default(),
                RigidBody::Fixed,
                Collider::cuboid(0.5, 1.0, 1.0),
                LuaScriptRef {
                    path: "zone.lua".into(),
                },
            ))
            .id();
        {
            let mut entity = app.world_mut().entity_mut(wall);
            stamp_collider_role(&mut entity, false, true);
        }
        assert!(app.world().get::<Sensor>(wall).is_none());
        app.world_mut().spawn((
            Transform::from_xyz(2.0, 0.0, 0.0),
            RigidBody::KinematicPositionBased,
            Collider::ball(0.35),
            KinematicCharacterController {
                translation: Some(Vec3::new(-0.5, 0.0, 0.0)),
                ..default()
            },
            Name::new("hero"),
        ));
        app.add_systems(Update, shove_hero);
        for _ in 0..20 {
            app.update();
        }
        assert_eq!(
            lua_count(&app, "entered"),
            1,
            "controller e evento não duplicam o enter"
        );
        assert_eq!(lua_string(&app, "last"), "hero");
    }

    fn shove_hero(mut controllers: Query<&mut KinematicCharacterController>) {
        for mut controller in &mut controllers {
            controller.translation = Some(Vec3::new(-0.5, 0.0, 0.0));
        }
    }
}
