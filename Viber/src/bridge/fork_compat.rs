//! Compatibilidade de protocolo BRP com o cliente do **Bevy main** (o fork
//! local `../../bevy` usado pelo ViberEditor e pelo `bevy_inspector`).
//!
//! O `bevy_remote` do motor é o 0.20.0 do crates.io; o do fork mudou a
//! serialização de entidades no BRP: o 0.20.0 emite os *bits opacos* do
//! `Entity` (`serialize_u64(to_bits)`, layout `NonMaxU32` = `MAX - index`
//! nos bits baixos, geração nos altos), enquanto o cliente do fork espera a
//! forma legível `{index}v{generation}` (ou `PLACEHOLDER`). Sem esta camada,
//! o `bevy_inspector` perde a ligação a cada poll (`missing field …` /
//! `invalid type: integer …, expected an entity in the form …`) e a árvore
//! de entidades fica vazia.
//!
//! Os nomes dos métodos `world.*` são idênticos nas duas versões — só o
//! formato dos valores de entidade muda. Este módulo substitui os builtins
//! no `RemoteMethods` por shims que (1) traduzem entidades dos pedidos
//! `"3v1"` → bits antes de invocar o sistema original e (2) traduzem as
//! respostas bits → `"3v1"` no fim. REMOVER quando o Viber subir para um
//! Bevy que já traga o formato legível.

use bevy::ecs::system::{In, SystemId};
use bevy::prelude::*;
use bevy::remote::{BrpError, BrpResult, RemoteMethodSystemId, RemoteMethods};
use serde_json::Value;

/// Os ids dos sistemas builtin ORIGINAIS, guardados para os shims invocarem.
#[derive(Resource)]
pub struct ForkCompat {
    query: SystemId<In<Option<Value>>, BrpResult>,
    get_components: SystemId<In<Option<Value>>, BrpResult>,
    insert_components: SystemId<In<Option<Value>>, BrpResult>,
    remove_components: SystemId<In<Option<Value>>, BrpResult>,
    mutate_components: SystemId<In<Option<Value>>, BrpResult>,
    spawn_entity: SystemId<In<Option<Value>>, BrpResult>,
    despawn_entity: SystemId<In<Option<Value>>, BrpResult>,
    reparent_entities: SystemId<In<Option<Value>>, BrpResult>,
    list_components: SystemId<In<Option<Value>>, BrpResult>,
}

/// Substitui os builtins `world.*` pelos shims de tradução. Chamar DEPOIS de
/// `add_plugins(RemotePlugin…)` (o registo é um HashMap — `insert` substitui).
pub fn install(app: &mut App) {
    app.world_mut()
        .resource_scope(|world, mut methods: Mut<RemoteMethods>| {
            let compat = ForkCompat {
                query: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_QUERY_METHOD,
                    shim_query,
                ),
                get_components: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_GET_COMPONENTS_METHOD,
                    shim_get_components,
                ),
                insert_components: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_INSERT_COMPONENTS_METHOD,
                    shim_insert_components,
                ),
                remove_components: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_REMOVE_COMPONENTS_METHOD,
                    shim_remove_components,
                ),
                mutate_components: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_MUTATE_COMPONENTS_METHOD,
                    shim_mutate_components,
                ),
                spawn_entity: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_SPAWN_ENTITY_METHOD,
                    shim_spawn_entity,
                ),
                despawn_entity: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_DESPAWN_COMPONENTS_METHOD,
                    shim_despawn_entity,
                ),
                reparent_entities: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_REPARENT_ENTITIES_METHOD,
                    shim_reparent_entities,
                ),
                list_components: shim(
                    world,
                    &mut methods,
                    bevy::remote::builtin_methods::BRP_LIST_COMPONENTS_METHOD,
                    shim_list_components,
                ),
            };
            world.insert_resource(compat);
        });
}

/// Guarda o id original, regista o shim e substitui a entrada no registo.
fn shim(
    world: &mut World,
    methods: &mut RemoteMethods,
    name: &str,
    handler: fn(In<Option<Value>>, &mut World) -> BrpResult,
) -> SystemId<In<Option<Value>>, BrpResult> {
    let Some(RemoteMethodSystemId::Instant(original)) = methods.get(name).cloned() else {
        // Builtin ausente (versão do bevy_remote sem ele): nada a fazer — o
        // método fica como está. O id devolvido nunca corre (o shim só é
        // instalado quando há original), é só para o campo ser válido.
        return SystemId::from_entity(Entity::PLACEHOLDER);
    };
    let shim_id = world.register_system(handler);
    methods.insert(name, RemoteMethodSystemId::Instant(shim_id));
    original
}

// ── Shims (um fn por método — sistemas têm de ser fn items) ─────────────

fn shim_query(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(world, |w| w.resource::<ForkCompat>().query, params)
}

fn shim_get_components(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(world, |w| w.resource::<ForkCompat>().get_components, params)
}

fn shim_insert_components(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(
        world,
        |w| w.resource::<ForkCompat>().insert_components,
        params,
    )
}

fn shim_remove_components(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(
        world,
        |w| w.resource::<ForkCompat>().remove_components,
        params,
    )
}

fn shim_mutate_components(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(
        world,
        |w| w.resource::<ForkCompat>().mutate_components,
        params,
    )
}

fn shim_spawn_entity(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(world, |w| w.resource::<ForkCompat>().spawn_entity, params)
}

fn shim_despawn_entity(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(world, |w| w.resource::<ForkCompat>().despawn_entity, params)
}

fn shim_reparent_entities(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(
        world,
        |w| w.resource::<ForkCompat>().reparent_entities,
        params,
    )
}

fn shim_list_components(In(params): In<Option<Value>>, world: &mut World) -> BrpResult {
    run(
        world,
        |w| w.resource::<ForkCompat>().list_components,
        params,
    )
}

/// Traduz o pedido (entidades `"3v1"` → bits), invoca o builtin original e
/// traduz a resposta (bits → `"3v1"`).
fn run(
    world: &mut World,
    original: impl FnOnce(&World) -> SystemId<In<Option<Value>>, BrpResult>,
    params: Option<Value>,
) -> BrpResult {
    let id = original(world);
    let params = params.map(|mut value| {
        translate_request(&mut value);
        value
    });
    let result = world
        .run_system_with(id, params)
        .map_err(|error| BrpError::internal(error.to_string()))?;
    let mut value = result?;
    translate_response(&mut value);
    Ok(value)
}

// ── Tradução de entidades ────────────────────────────────────────────────

/// Bits opacos do `Entity` 0.20.0 → forma legível do fork.
///
/// Layout (empírico + `NonMaxU32::transmute`): bits baixos = `u32::MAX -
/// index` (representação interna do `NonMaxU32`), bits altos = geração.
/// `317v0` ↔ 4294966978u64, verificado contra o `viber.tree` (Display) e o
/// `world.query` (serde) da mesma entidade.
pub(crate) fn entity_bits_to_string(bits: u64) -> Option<String> {
    let raw_index = bits as u32;
    let generation = (bits >> 32) as u32;
    if raw_index == u32::MAX {
        // `EntityIndex::PLACEHOLDER` (NonMaxU32::MAX) — o fork também tem a
        // forma textual.
        return Some("PLACEHOLDER".into());
    }
    Some(format!("{}v{}", u32::MAX - raw_index, generation))
}

/// Forma legível do fork → bits opacos do `Entity` 0.20.0.
pub(crate) fn entity_string_to_bits(text: &str) -> Option<u64> {
    if text == "PLACEHOLDER" {
        return Some(u64::from(u32::MAX));
    }
    let (index, generation) = text.split_once('v')?;
    let index: u32 = index.parse().ok()?;
    let generation: u32 = generation.parse().ok()?;
    // `u32::MAX` não é representável como índice (nicho do NonMaxU32) — o
    // fork também rejeita "4294967295v0".
    if index == u32::MAX {
        return None;
    }
    Some(u64::from(u32::MAX - index) | (u64::from(generation) << 32))
}

/// Chaves de pedidos que carregam entidades (params dos builtins 0.20.0).
fn translate_request(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, item) in map.iter_mut() {
                match key.as_str() {
                    "entity" | "parent" => {
                        if let Value::String(text) = &*item
                            && let Some(bits) = entity_string_to_bits(text)
                        {
                            *item = Value::from(bits);
                        }
                    }
                    "entities" => {
                        if let Value::Array(items) = item {
                            for item in items.iter_mut() {
                                if let Value::String(text) = item
                                    && let Some(bits) = entity_string_to_bits(text)
                                {
                                    *item = Value::from(bits);
                                }
                            }
                        }
                    }
                    _ => translate_request(item),
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(translate_request),
        _ => {}
    }
}

/// Chaves de respostas que carregam entidades: o `entity` das rows do
/// `world.query`/`world.spawn_entity`, e o valor de componentes
/// entity-typed (`ChildOf`) — o `bevy_inspector` desserializa-os como
/// `"2v0"` (ver teste `parse_rows` do fork).
fn translate_response(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, item) in map.iter_mut() {
                let key = key.as_str();
                let entity_typed_component =
                    key.ends_with("::ChildOf") || key.ends_with("::Parent");
                match key {
                    "entity" | "parent" => rewrite_int_entity(item),
                    "entities" => {
                        if let Value::Array(items) = item {
                            items.iter_mut().for_each(rewrite_int_entity);
                        }
                    }
                    _ if entity_typed_component => rewrite_int_entity(item),
                    _ => translate_response(item),
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(translate_response),
        _ => {}
    }
}

fn rewrite_int_entity(item: &mut Value) {
    if let Value::Number(number) = item
        && let Some(bits) = number.as_u64()
        && let Some(text) = entity_bits_to_string(bits)
    {
        *item = Value::String(text);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// O par calibrado ao vivo (tree "317v0" ↔ query 4294966978u64).
    #[test]
    fn test_bits_round_trip_live_pair() {
        assert_eq!(
            entity_bits_to_string(4294966978u64).as_deref(),
            Some("317v0")
        );
        assert_eq!(entity_string_to_bits("317v0"), Some(4294966978u64));
    }

    #[test]
    fn test_bits_round_trip_with_generation() {
        // Geração nos bits altos: "36242v1".
        let bits = u32::MAX as u64 - 36242 + (1u64 << 32);
        assert_eq!(entity_string_to_bits("36242v1"), Some(bits));
        assert_eq!(entity_bits_to_string(bits).as_deref(), Some("36242v1"));
    }

    #[test]
    fn test_placeholder_forms() {
        assert_eq!(
            entity_string_to_bits("PLACEHOLDER"),
            Some(u64::from(u32::MAX))
        );
        assert_eq!(
            entity_bits_to_string(u64::from(u32::MAX)).as_deref(),
            Some("PLACEHOLDER")
        );
    }

    #[test]
    fn test_rejects_non_entities() {
        assert_eq!(entity_string_to_bits("42"), None);
        assert_eq!(entity_string_to_bits("v3"), None);
        assert_eq!(entity_string_to_bits("4294967295v0"), None);
    }

    /// Um request de insert_components do editor: `entity` vira bits.
    #[test]
    fn test_translate_request_insert() {
        let mut value = json!({
            "entity": "317v0",
            "components": { "bevy_transform::components::transform::Transform":
                { "translation": [1.0, 2.0, 3.0] } }
        });
        translate_request(&mut value);
        assert_eq!(value["entity"], json!(4294966978u64));
        // O resto segue intacto.
        assert_eq!(
            value["components"]["bevy_transform::components::transform::Transform"]["translation"],
            json!([1.0, 2.0, 3.0])
        );
    }

    /// Um reparent: `entities` (array) + `parent`.
    #[test]
    fn test_translate_request_reparent() {
        let mut value = json!({ "entities": ["317v0", "36242v1"], "parent": "0v0" });
        translate_request(&mut value);
        assert_eq!(value["entities"], json!([4294966978u64, 8589898349u64]));
        assert_eq!(value["parent"], json!(4294967295u64));
    }

    /// Rows do world.query: `entity` + ChildOf viram "NvG".
    #[test]
    fn test_translate_response_query_rows() {
        let mut value = json!([
            { "entity": 4294966978u64,
              "components": {
                "bevy_ecs::name::Name": "Armature",
                "bevy_ecs::hierarchy::ChildOf": 4294966978u64 } }
        ]);
        translate_response(&mut value);
        assert_eq!(value[0]["entity"], json!("317v0"));
        assert_eq!(
            value[0]["components"]["bevy_ecs::hierarchy::ChildOf"],
            json!("317v0")
        );
        assert_eq!(
            value[0]["components"]["bevy_ecs::name::Name"],
            json!("Armature")
        );
    }

    /// Inteiros comuns (ex.: um campo u64 de um componente qualquer) não
    /// são tocados fora das chaves de entidade.
    #[test]
    fn test_translate_response_leaves_plain_numbers() {
        let mut value = json!({ "hit": true, "toi": 42, "frame": 4294966978u64 });
        translate_response(&mut value);
        assert_eq!(value["toi"], json!(42));
        assert_eq!(value["frame"], json!(4294966978u64));
    }
}
