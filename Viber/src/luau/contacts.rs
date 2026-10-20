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
    CollisionEvent, CollisionEventFlags, KinematicCharacterControllerOutput, Sensor,
};

use super::host::{ContactHook, LuaScriptHost};
use super::runtime::LuaScriptRef;

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
