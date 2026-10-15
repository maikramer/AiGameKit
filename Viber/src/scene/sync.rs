//! Sync documento → mundo vivo: cada [`SceneEdited`] (de um agente pelo bridge, ou do
//! ViberEditor) é refletido nas entidades com [`SceneSource`].
//!
//! * mudança só de pose (`translation`/`euler`/`rotation`/`scale`/aliases) → patch do
//!   `Transform`, sem re-spawn;
//! * mudança só de props de script (`prop-*`) → patch do `ScriptProps` no sítio — o
//!   script continua a correr com o mesmo `viber.state()` (afinar o jogo vivo);
//! * outros atributos → re-spawn da subárvore do nó;
//! * inserção / remoção / move → spawn / despawn, e REALINHAMENTO das origens: um nó novo ou
//!   removido desloca os caminhos dos irmãos seguintes, e os `SceneSource` das entidades
//!   vivas têm de acompanhar (senão a próxima edição acertava na entidade errada).
//!
//! Só nascem a quente os tipos de [`spawns_incrementally`]; o resto (terreno, céu, UI,
//! spawners, recursos de mundo) fica em [`SceneSyncStatus::needs_reload`].

use std::sync::Arc;

use bevy::prelude::*;

use super::SceneSource;
use super::doc::{NodeRef, SceneDoc};
use super::live::SceneEdited;
use super::ops::Change;
use super::pose::read_pose;
use crate::recipes::spawn::{EditorGroups, spawn_specs, spawns_incrementally};
use crate::xml::{NodeOrigin, XmlNode};

/// Atributos cuja mudança é só pose (patch do `Transform`).
pub const POSE_KEYS: &[&str] = &[
    "translation",
    "pos",
    "euler",
    "rotation",
    "scale",
    "transform",
];

/// Mudanças que o mundo vivo não conseguiu refletir (o editor mostra "recarregar").
#[derive(Resource, Debug, Default, Clone)]
pub struct SceneSyncStatus {
    pub needs_reload: Vec<String>,
}

pub struct SceneSyncPlugin;

impl Plugin for SceneSyncPlugin {
    fn build(&self, app: &mut App) {
        // ANTES da física: o Rapier só aceita poses do utilizador que deteta na sua fase de
        // sync (`apply_rigid_body_user_changes`); depois dela, o writeback devolvia aos
        // corpos (fixos incluídos) a pose antiga.
        app.init_resource::<SceneSyncStatus>().add_systems(
            PostUpdate,
            sync_scene
                .before(bevy_rapier3d::prelude::PhysicsSet::SyncBackend)
                .before(bevy::transform::TransformSystems::Propagate),
        );
    }
}

/// O que fazer, por nó, depois de percorrer todas as mudanças.
#[derive(Debug, Default)]
struct Plan {
    spawn: Vec<NodeRef>,
    respawn: Vec<NodeRef>,
    pose: Vec<NodeRef>,
    props: Vec<NodeRef>,
    terrain: Vec<super::terrain::CellRect>,
    biomes: bool,
    paint: Vec<super::terrain::CellRect>,
}

pub fn sync_scene(world: &mut World) {
    let edits: Vec<SceneEdited> = world
        .resource_mut::<Messages<SceneEdited>>()
        .drain()
        .collect();
    if edits.is_empty() || !world.contains_resource::<SceneDoc>() {
        return;
    }
    let mut plan = Plan::default();
    for change in edits.iter().flat_map(|edit| &edit.changes) {
        match change {
            Change::Attrs { node, keys } => {
                let is_pose = |key: &String| POSE_KEYS.contains(&key.as_str());
                let is_prop = |key: &String| crate::luau::exports::prop_name(key).is_some();
                // Num `<Use>` os props da entidade são o merge protótipo+instância: o nó
                // sozinho não chega para os patchar — re-spawn.
                let use_node = world
                    .resource::<SceneDoc>()
                    .node(node)
                    .is_some_and(|xml| xml.tag.eq_ignore_ascii_case("use"));
                let patchable = keys
                    .iter()
                    .all(|key| is_pose(key) || (is_prop(key) && !use_node));
                let mut lists: Vec<&mut Vec<NodeRef>> = Vec::new();
                if !patchable {
                    lists.push(&mut plan.respawn);
                } else {
                    if keys.iter().any(is_pose) {
                        lists.push(&mut plan.pose);
                    }
                    if keys.iter().any(is_prop) {
                        lists.push(&mut plan.props);
                    }
                }
                for list in lists {
                    if !list.contains(node) {
                        list.push(node.clone());
                    }
                }
            }
            Change::Inserted { node } => {
                shift(world, &mut plan, node, 1);
                plan.spawn.push(node.clone());
            }
            Change::Terrain { rect } => plan.terrain.push(*rect),
            Change::Biomes => plan.biomes = true,
            Change::Paint { rect } => plan.paint.push(*rect),
            Change::Removed { node } => {
                despawn_subtree(world, node);
                for list in [
                    &mut plan.spawn,
                    &mut plan.respawn,
                    &mut plan.pose,
                    &mut plan.props,
                ] {
                    list.retain(|pending| !is_within(pending, node));
                }
                shift(world, &mut plan, node, -1);
            }
        }
    }
    let mut failures = Vec::new();
    for node in plan.respawn {
        if plan.spawn.contains(&node) {
            continue;
        }
        despawn_subtree(world, &node);
        if let Err(error) = spawn_node(world, &node) {
            failures.push(error);
        }
    }
    for node in plan.spawn {
        if let Err(error) = spawn_node(world, &node) {
            failures.push(error);
        }
    }
    for rect in &plan.terrain {
        push_terrain(world, *rect);
    }
    if plan.biomes {
        push_biomes(world);
    }
    for rect in &plan.paint {
        push_paint(world, *rect);
    }
    let mut posed = 0;
    for node in &plan.pose {
        posed += patch_pose(world, node);
    }
    for node in &plan.props {
        patch_props(world, node);
    }
    info!(
        target: "viber::scene",
        "scene sync: {} edit(s) — {posed} entit(ies) re-posed, {} failure(s)",
        edits.len(),
        failures.len()
    );
    if !failures.is_empty() {
        for failure in &failures {
            warn!("scene sync: {failure}");
        }
        world
            .resource_mut::<SceneSyncStatus>()
            .needs_reload
            .extend(failures);
    }
}

/// `node` é `ancestor` ou está dentro dele.
fn is_within(node: &NodeRef, ancestor: &NodeRef) -> bool {
    node.file == ancestor.file && node.path.starts_with(&ancestor.path)
}

/// Desloca em `delta` os caminhos dos irmãos a partir de `at` (inserção: `at` e seguintes;
/// remoção: só os seguintes) — nas entidades vivas e nos pendentes do plano.
fn shift(world: &mut World, plan: &mut Plan, at: &NodeRef, delta: isize) {
    let depth = at.path.len() - 1;
    let index = at.path[depth];
    let affected = |file_matches: bool, path: &[usize]| {
        file_matches
            && path.len() > depth
            && path[..depth] == at.path[..depth]
            && if delta > 0 {
                path[depth] >= index
            } else {
                path[depth] > index
            }
    };
    for list in [
        &mut plan.spawn,
        &mut plan.respawn,
        &mut plan.pose,
        &mut plan.props,
    ] {
        for pending in list.iter_mut() {
            if affected(pending.file == at.file, &pending.path) {
                pending.path[depth] = pending.path[depth].saturating_add_signed(delta);
            }
        }
    }
    let file: Arc<std::path::Path> =
        Arc::from(world.resource::<SceneDoc>().files[at.file].path.as_path());
    let mut query = world.query::<&mut SceneSource>();
    for mut source in query.iter_mut(world) {
        let path: Vec<usize> = source.0.path.iter().map(|&i| i as usize).collect();
        if affected(*source.0.file == *file, &path) {
            let mut origin = (*source.0).clone();
            origin.path[depth] = (origin.path[depth] as isize + delta) as u32;
            source.0 = Arc::new(origin);
        }
    }
}

/// Entidades cuja origem é `node` (não as dos descendentes).
fn entities_of(world: &mut World, node: &NodeRef) -> Vec<Entity> {
    let origin = world.resource::<SceneDoc>().origin_of(node);
    let mut query = world.query::<(Entity, &SceneSource)>();
    query
        .iter(world)
        .filter(|(_, source)| *source.0 == origin)
        .map(|(entity, _)| entity)
        .collect()
}

/// Despawna as entidades do nó e de todos os descendentes (o despawn é recursivo).
fn despawn_subtree(world: &mut World, node: &NodeRef) {
    let file = world.resource::<SceneDoc>().files[node.file].path.clone();
    let prefix: Vec<u32> = node.path.iter().map(|&i| i as u32).collect();
    let mut query = world.query::<(Entity, &SceneSource)>();
    let doomed: Vec<Entity> = query
        .iter(world)
        .filter(|(_, source)| *source.0.file == *file && source.0.path.starts_with(&prefix))
        .map(|(entity, _)| entity)
        .collect();
    for entity in doomed {
        if let Ok(entity) = world.get_entity_mut(entity) {
            entity.despawn();
        }
    }
}

/// Spawna o nó (do documento atual) debaixo da entidade do pai.
fn spawn_node(world: &mut World, node: &NodeRef) -> Result<(), String> {
    let doc = world.resource::<SceneDoc>();
    let selector = doc.selector(node);
    let Some(xml) = doc.node(node) else {
        return Ok(());
    };
    let mut xml = xml.clone();
    stamp_subtree(&mut xml, &doc.origin_of(node));
    // Uma cena instanciada (`<Include>` novo, `extract`): o conteúdo vem do ficheiro.
    let xml = expand_includes(doc, node, xml, 0);
    let parent_ref = node.parent().expect("um nó inserido nunca é raiz");
    let top_level = parent_ref.is_root();
    // Topo de um ficheiro INCLUÍDO: o pai natural é o Group do `<Include>` quando ele tem
    // entidade (include com transform) — nasce dentro do referencial do ficheiro.
    let includer = if top_level && node.file != 0 {
        (0..doc.files.len()).find_map(|file| {
            doc.files[file]
                .includes
                .iter()
                .find(|(_, target)| *target == node.file)
                .map(|(path, _)| NodeRef::new(file, path.clone()))
        })
    } else {
        None
    };
    let parsed = crate::recipes::parse_world(&[], std::slice::from_ref(&xml))
        .map_err(|e| format!("{selector}: {e:#}"))?;
    // Sem entidade (um `<Prototype>`, uma tag desconhecida): nada a spawnar.
    let Some(spec) = parsed.entities.first() else {
        return Ok(());
    };
    if !spawns_incrementally(spec) {
        return Err(format!(
            "{selector}: <{}> só muda ao recarregar o mundo",
            xml.tag
        ));
    }
    let parent = if let Some(include) = includer
        && let [one] = entities_of(world, &include).as_slice()
    {
        Some(*one)
    } else if top_level {
        world
            .get_resource::<EditorGroups>()
            .map(|groups| groups.for_tag(spec.tag.as_deref()))
    } else {
        let parents = entities_of(world, &parent_ref);
        match parents.as_slice() {
            [one] => Some(*one),
            [] => {
                return Err(format!(
                    "{selector}: o pai não tem entidade viva — recarrega o mundo"
                ));
            }
            _ => {
                return Err(format!(
                    "{selector}: o pai é instanciado várias vezes (<Use>) — recarrega o mundo"
                ));
            }
        }
    };
    spawn_specs(world, &parsed.entities, parent)
        .map(|_| ())
        .map_err(|e| format!("{selector}: {e}"))
}

/// Expande os `<Include>` de uma subárvore (já carimbada) com o conteúdo dos ficheiros
/// do documento: cada include vira um `<Group>` com a sua pose (e `name`), com a
/// proveniência do próprio include; os filhos levam a do ficheiro incluído — a mesma
/// forma que o `load_world` produz para um include com pose.
fn expand_includes(doc: &SceneDoc, at: &NodeRef, mut xml: XmlNode, depth: usize) -> XmlNode {
    if depth > crate::xml::include::MAX_INCLUDE_DEPTH {
        return xml;
    }
    if xml.tag.eq_ignore_ascii_case("include") {
        let Some(target) = doc.include_target(at) else {
            return xml;
        };
        let keys = crate::xml::include::INCLUDE_POSE_KEYS;
        xml.tag = "Group".into();
        xml.attrs
            .retain(|(key, _)| keys.contains(&key.as_str()) || key == "name");
        xml.children = doc.files[target]
            .doc
            .children
            .iter()
            .enumerate()
            .map(|(index, child)| {
                let child_ref = NodeRef::new(target, vec![index]);
                let mut child = child.clone();
                stamp_subtree(&mut child, &doc.origin_of(&child_ref));
                expand_includes(doc, &child_ref, child, depth + 1)
            })
            .collect();
        return xml;
    }
    let children = std::mem::take(&mut xml.children);
    xml.children = children
        .into_iter()
        .enumerate()
        .map(|(index, child)| expand_includes(doc, &at.child(index), child, depth))
        .collect();
    xml
}

/// Origens de uma subárvore copiada do documento, a partir da origem do seu raiz.
fn stamp_subtree(node: &mut XmlNode, origin: &NodeOrigin) {
    node.origin = Some(Arc::new(origin.clone()));
    for (index, child) in node.children.iter_mut().enumerate() {
        let mut child_origin = origin.clone();
        child_origin.path.push(index as u32);
        stamp_subtree(child, &child_origin);
    }
}

/// Alturas do documento → overlay do terreno vivo (re-mesh só das colunas tocadas). Sem
/// terreno vivo (CLI/headless sem bootstrap) não há nada a refletir.
fn push_terrain(world: &mut World, rect: super::terrain::CellRect) {
    let Some(heights) = world
        .get_resource::<SceneDoc>()
        .and_then(|doc| doc.terrain.as_ref())
        .map(|terrain| terrain.heights(rect))
    else {
        return;
    };
    let Some(mut runtime) = world.get_resource_mut::<crate::terrain::runtime::TerrainRuntime>()
    else {
        return;
    };
    let grid = runtime.grid.clone();
    let deltas = std::sync::Arc::make_mut(&mut runtime.deltas);
    if !deltas.set_cells(&grid, rect.i0, rect.j0, rect.cols, rect.rows, heights) {
        warn!("scene sync: retângulo de terreno inválido {rect:?}");
    }
}

/// Copia o campo de biomas do documento para o runtime e re-bakeia os splats
/// do mundo inteiro (a mesma cadeia do `viber.debug.ground`, mas com biomas).
fn push_biomes(world: &mut World) {
    let Some(biomes) = world
        .get_resource::<SceneDoc>()
        .and_then(|doc| doc.terrain.as_ref())
        .map(|terrain| terrain.meta.biomes.clone())
    else {
        return;
    };
    let tuning = world
        .get_resource::<crate::terrain::runtime::TerrainRuntime>()
        .map(|runtime| {
            // clone-on-write via interior: spec é campo pub, escrevemos com get_mut
            // depois da leitura do tuning
            runtime.splat_tuning
        });
    let Some(tuning) = tuning else {
        return;
    };
    world
        .resource_mut::<crate::terrain::runtime::TerrainRuntime>()
        .spec
        .biomes = biomes;
    let repainted = crate::terrain::runtime::rebake_chunk_splats(world, tuning);
    info!(
        target: "viber::scene",
        "scene sync: biomas — {repainted} chunk(s) re-baked"
    );
}

/// Copia a camada de pintura do documento para o runtime e repinta os chunks
/// tocados (in-place, mesmos handles) — o efeito vivo da op `terrain_paint`.
fn push_paint(world: &mut World, rect: super::terrain::CellRect) {
    let window = crate::terrain::paint::CellWindow {
        i0: rect.i0,
        j0: rect.j0,
        cols: rect.cols,
        rows: rect.rows,
    };
    // Só a janela tocada: a grelha inteira são dezenas de MB num mapa grande.
    let Some((bytes, side, corners)) = world
        .get_resource::<SceneDoc>()
        .and_then(|doc| doc.terrain.as_ref())
        .map(|terrain| {
            let paint = &terrain.paint;
            let lo = paint.cell_center(rect.i0, rect.j0);
            let hi = paint.cell_center(rect.i0 + rect.cols - 1, rect.j0 + rect.rows - 1);
            (paint.snapshot(window), paint.side(), (lo, hi))
        })
    else {
        return;
    };
    let Some(mut runtime) = world.get_resource_mut::<crate::terrain::runtime::TerrainRuntime>()
    else {
        return;
    };
    if runtime.paint.side() != side {
        // Grelhas de tamanhos diferentes (o runtime arrancou sem camada): adota a do
        // documento uma vez; as pinceladas seguintes copiam só a janela.
        let Some(doc_paint) = world
            .get_resource::<SceneDoc>()
            .and_then(|doc| doc.terrain.as_ref())
            .map(|terrain| terrain.paint.clone())
        else {
            return;
        };
        world
            .resource_mut::<crate::terrain::runtime::TerrainRuntime>()
            .paint = std::sync::Arc::new(doc_paint);
    } else if let Err(error) = std::sync::Arc::make_mut(&mut runtime.paint).restore(window, &bytes)
    {
        warn!("scene sync: pintura {rect:?}: {error:#}");
        return;
    }
    let repainted = crate::terrain::runtime::repaint_chunks(world, corners.0, corners.1);
    debug!(target: "viber::scene", "scene sync: paint {rect:?} — {repainted} chunk(s) repainted");
}

/// Aplica a pose do XML às entidades do nó; devolve quantas mudaram.
fn patch_pose(world: &mut World, node: &NodeRef) -> usize {
    let Some(xml) = world.resource::<SceneDoc>().node(node).cloned() else {
        return 0;
    };
    let Ok(pose) = read_pose(&xml) else {
        return 0;
    };
    let mut count = 0;
    for entity in entities_of(world, node) {
        let Some(mut transform) = world.get_mut::<Transform>(entity) else {
            continue;
        };
        transform.translation = pose.translation;
        transform.rotation = pose.rotation;
        transform.scale = pose.scale;
        let local = *transform;
        teleport_body(world, entity, local);
        count += 1;
    }
    count
}

/// Aplica os `prop-*` do XML ao [`crate::luau::ScriptProps`] das entidades com script do
/// nó — sem re-spawn, o script mantém o estado; o próximo `viber.prop` lê o valor novo.
fn patch_props(world: &mut World, node: &NodeRef) -> usize {
    let Some(xml) = world.resource::<SceneDoc>().node(node).cloned() else {
        return 0;
    };
    let props: Vec<(String, String)> = xml
        .attrs
        .iter()
        .filter_map(|(key, value)| {
            crate::luau::exports::prop_name(key).map(|name| (name.to_owned(), value.clone()))
        })
        .collect();
    let mut count = 0;
    for entity in entities_of(world, node) {
        if world.get::<crate::luau::LuaScriptRef>(entity).is_none() {
            continue;
        }
        let mut entity = world.entity_mut(entity);
        if props.is_empty() {
            entity.remove::<crate::luau::ScriptProps>();
        } else {
            entity.insert(crate::luau::ScriptProps(Arc::new(props.clone())));
        }
        count += 1;
    }
    count
}

/// Um rigid body (mesmo `fixed`) é dono da pose: o writeback do Rapier reescreve o
/// `Transform` a partir do corpo, e a deteção de mudanças dele não vê corpos FILHOS (a
/// propagação interna do bevy_rapier não marca as árvores sujas do Bevy 0.20). Por isso o
/// corpo é teleportado para a nova pose mundo (`mundo do pai × local`).
pub fn teleport_body(world: &mut World, entity: Entity, local: Transform) {
    use bevy_rapier3d::prelude::{
        RapierContextEntityLink, RapierRigidBodyHandle, RapierRigidBodySet,
    };
    let (Some(handle), Some(link)) = (
        world.get::<RapierRigidBodyHandle>(entity).map(|h| h.0),
        world.get::<RapierContextEntityLink>(entity).map(|l| l.0),
    ) else {
        return;
    };
    let parent_world = world
        .get::<ChildOf>(entity)
        .and_then(|child_of| world.get::<GlobalTransform>(child_of.parent()))
        .copied()
        .unwrap_or_default();
    let global = parent_world.mul_transform(local).compute_transform();
    if let Some(mut bodies) = world.get_mut::<RapierRigidBodySet>(link)
        && let Some(body) = bodies.bodies.get_mut(handle)
    {
        body.set_position(
            bevy_rapier3d::rapier::math::Pose::from_parts(global.translation, global.rotation),
            true,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::doc::tests::fixture;
    use crate::scene::live::{self, ScenePlugin};
    use crate::scene::ops::EditOp;
    use std::collections::BTreeMap;

    /// A App headless do bake (o MESMO arranque do jogo) com o documento e o sync.
    fn app(dir: &std::path::Path) -> App {
        boot(dir, &fixture(dir))
    }

    /// Boots the app against an already-written world (tests that tweak the fixture first).
    fn boot(dir: &std::path::Path, world: &std::path::Path) -> App {
        std::fs::write(
            dir.join(crate::config::CONFIG_FILE),
            crate::scaffold::CONFIG_YAML_TEMPLATE,
        )
        .unwrap();
        let config = crate::config::load(dir).unwrap();
        let loaded = crate::xml::include::load_world(&world).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        let mut app = crate::bake::headless_app(&world, &config, parsed).unwrap();
        app.add_plugins((ScenePlugin, SceneSyncPlugin));
        app.insert_resource(SceneDoc::load(&world).unwrap());
        app.add_systems(Startup, crate::recipes::spawn::startup);
        app.update();
        app
    }

    fn named(app: &mut App, name: &str) -> Vec<Entity> {
        let mut query = app.world_mut().query::<(Entity, &Name)>();
        query
            .iter(app.world())
            .filter(|(_, n)| n.as_str() == name)
            .map(|(e, _)| e)
            .collect()
    }

    fn source_path(app: &mut App, name: &str) -> Vec<u32> {
        let entity = named(app, name)[0];
        app.world()
            .get::<SceneSource>(entity)
            .expect("proveniência")
            .0
            .path
            .clone()
    }

    fn edit(app: &mut App, ops: &[EditOp]) {
        live::apply(app.world_mut(), "test", ops).unwrap();
        app.update();
    }

    fn set(node: &str, key: &str, value: &str) -> EditOp {
        EditOp::Set {
            node: node.into(),
            attrs: BTreeMap::from([(key.to_owned(), Some(value.to_owned()))]),
        }
    }

    #[test]
    fn spawned_entities_carry_their_source() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        assert_eq!(source_path(&mut app, "hero"), [0]);
        assert_eq!(source_path(&mut app, "barrel"), [1, 1]);
        let well = named(&mut app, "well")[0];
        let source = app.world().get::<SceneSource>(well).unwrap().0.clone();
        assert!(source.file.ends_with("world/village.xml"));
    }

    #[test]
    fn extract_and_instance_keep_the_content_alive() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(
            &mut app,
            &[EditOp::Extract {
                node: "name=hero".into(),
                to: Some("scenes/hero.xml".into()),
            }],
        );
        let hero = named(&mut app, "hero");
        assert_eq!(
            hero.len(),
            1,
            "o ramo extraído continua vivo (via o include)"
        );
        app.update();
        let global = app.world().get::<GlobalTransform>(hero[0]).unwrap();
        assert_eq!(global.translation(), Vec3::new(0.0, 1.0, 0.0));
        let source = app.world().get::<SceneSource>(hero[0]).unwrap().0.clone();
        assert!(source.file.ends_with("scenes/hero.xml"));
        // Uma segunda instância da mesma cena (ainda por gravar) noutro sítio.
        let op = crate::scene::catalog::scene_instance(
            app.world().resource::<SceneDoc>(),
            "#",
            "scenes/hero.xml",
            Some([10.0, 0.0, 0.0]),
        )
        .unwrap();
        edit(&mut app, &[op]);
        app.update();
        let mut xs: Vec<f32> = named(&mut app, "hero")
            .into_iter()
            .map(|e| {
                app.world()
                    .get::<GlobalTransform>(e)
                    .unwrap()
                    .translation()
                    .x
            })
            .collect();
        xs.sort_by(f32::total_cmp);
        assert_eq!(xs, [0.0, 10.0]);
    }

    #[test]
    fn pose_edit_patches_transform_without_respawn() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let before = named(&mut app, "well")[0];
        edit(&mut app, &[set("name=well", "translation", "9 1 -3")]);
        let after = named(&mut app, "well");
        assert_eq!(after, [before], "a mesma entidade, sem re-spawn");
        assert_eq!(
            app.world().get::<Transform>(before).unwrap().translation,
            Vec3::new(9.0, 1.0, -3.0)
        );
    }

    #[test]
    fn prop_edit_patches_script_props_without_respawn() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(&mut app, &[set("name=well", "script", "well.lua")]);
        let scripted = named(&mut app, "well")[0];
        assert!(
            app.world()
                .get::<crate::luau::LuaScriptRef>(scripted)
                .is_some()
        );
        edit(&mut app, &[set("name=well", "prop-speed", "2.5")]);
        assert_eq!(named(&mut app, "well"), [scripted], "sem re-spawn");
        let props = app
            .world()
            .get::<crate::luau::ScriptProps>(scripted)
            .expect("props patchados");
        assert_eq!(*props.0, vec![("speed".to_owned(), "2.5".to_owned())]);
        // Pose + prop na mesma edição: continua sem re-spawn.
        edit(
            &mut app,
            &[EditOp::Set {
                node: "name=well".into(),
                attrs: BTreeMap::from([
                    ("translation".to_owned(), Some("1 2 3".to_owned())),
                    ("prop-speed".to_owned(), None),
                ]),
            }],
        );
        assert_eq!(named(&mut app, "well"), [scripted]);
        // O undo de um `set` é um `replace_attrs` com a lista inteira: só as chaves que
        // mudaram contam — continua a ser patch, não re-spawn.
        let mut attrs: Vec<(String, String)> = app
            .world()
            .resource::<SceneDoc>()
            .node(
                &app.world()
                    .resource::<SceneDoc>()
                    .resolve("name=well")
                    .unwrap(),
            )
            .unwrap()
            .attrs
            .clone();
        attrs.push(("prop-speed".to_owned(), "4".to_owned()));
        edit(
            &mut app,
            &[EditOp::ReplaceAttrs {
                node: "name=well".into(),
                attrs,
            }],
        );
        assert_eq!(
            named(&mut app, "well"),
            [scripted],
            "replace_attrs sem re-spawn"
        );
        edit(
            &mut app,
            &[EditOp::Set {
                node: "name=well".into(),
                attrs: BTreeMap::from([("prop-speed".to_owned(), None)]),
            }],
        );
        assert_eq!(named(&mut app, "well"), [scripted]);
        assert!(
            app.world()
                .get::<crate::luau::ScriptProps>(scripted)
                .is_none(),
            "remover o último prop tira o componente"
        );
    }

    #[test]
    fn include_pose_edit_moves_included_content() {
        // <Include translation=…> vira um Group na expansão; editar a pose do include
        // patcha essa MESMA entidade (sem respawn) e os filhos do ficheiro seguem.
        let tmp = tempfile::tempdir().unwrap();
        let world = fixture(tmp.path());
        let xml = std::fs::read_to_string(&world).unwrap().replace(
            "<Include src=\"/world/village.xml\" />",
            "<Include src=\"/world/village.xml\" translation=\"10 0 0\" />",
        );
        std::fs::write(&world, xml).unwrap();
        let mut app = boot(tmp.path(), &world);

        // O wrapper tem a proveniência do Include (world.xml, índice 2).
        let mut query = app.world_mut().query::<(Entity, &SceneSource)>();
        let group = query
            .iter(app.world())
            .find(|(_, source)| source.0.file.ends_with("world.xml") && source.0.path == [2])
            .expect("wrapper do include")
            .0;
        drop(query);
        assert_eq!(
            app.world().get::<Transform>(group).unwrap().translation,
            Vec3::new(10.0, 0.0, 0.0),
            "a pose inicial do include está no wrapper"
        );
        let well = named(&mut app, "well")[0];
        assert_eq!(
            app.world()
                .get::<GlobalTransform>(well)
                .unwrap()
                .translation(),
            Vec3::new(14.0, 0.0, 2.0),
            "well = pose do include + pose local (4 0 2)"
        );

        edit(&mut app, &[set("#2", "translation", "30 0 0")]);
        assert_eq!(
            named(&mut app, "well"),
            [well],
            "a mesma entidade, sem respawn"
        );
        assert_eq!(
            app.world().get::<Transform>(group).unwrap().translation,
            Vec3::new(30.0, 0.0, 0.0)
        );
        assert_eq!(
            app.world()
                .get::<GlobalTransform>(well)
                .unwrap()
                .translation(),
            Vec3::new(34.0, 0.0, 2.0),
            "o conteúdo do ficheiro seguiu o include"
        );
    }

    #[test]
    fn insert_spawns_and_realigns_later_siblings() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(
            &mut app,
            &[EditOp::Insert {
                parent: "name=props".into(),
                index: Some(0),
                xml: r#"<Entity name="cart" translation="1 0 0"><Cuboid size="1" /></Entity>"#
                    .into(),
            }],
        );
        let cart = named(&mut app, "cart");
        assert_eq!(cart.len(), 1, "nasceu a quente");
        let props = named(&mut app, "props")[0];
        assert_eq!(app.world().get::<ChildOf>(cart[0]).unwrap().parent(), props);
        assert_eq!(source_path(&mut app, "cart"), [1, 0]);
        // crate/barrel desceram um índice — as origens vivas acompanham.
        assert_eq!(source_path(&mut app, "crate"), [1, 1]);
        assert_eq!(source_path(&mut app, "barrel"), [1, 2]);
        // E a próxima edição acerta no nó certo.
        edit(&mut app, &[set("name=barrel", "translation", "0 0 5")]);
        let barrel = named(&mut app, "barrel")[0];
        assert_eq!(
            app.world().get::<Transform>(barrel).unwrap().translation.z,
            5.0
        );
    }

    #[test]
    fn remove_despawns_subtree_and_undo_brings_it_back() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(
            &mut app,
            &[EditOp::Remove {
                node: "name=props".into(),
            }],
        );
        assert!(named(&mut app, "props").is_empty() && named(&mut app, "barrel").is_empty());
        // hero (#0) não se mexe; nada depois de props no mesmo ficheiro com entidade.
        assert_eq!(source_path(&mut app, "hero"), [0]);

        live::undo(app.world_mut()).unwrap();
        app.update();
        assert_eq!(named(&mut app, "props").len(), 1);
        assert_eq!(named(&mut app, "barrel").len(), 1);
        assert_eq!(source_path(&mut app, "barrel"), [1, 1]);
    }

    #[test]
    fn move_reparents_live_entity() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(
            &mut app,
            &[EditOp::Move {
                node: "name=hero".into(),
                parent: "name=props".into(),
                index: None,
            }],
        );
        let hero = named(&mut app, "hero");
        assert_eq!(hero.len(), 1);
        let props = named(&mut app, "props")[0];
        assert_eq!(app.world().get::<ChildOf>(hero[0]).unwrap().parent(), props);
        assert_eq!(source_path(&mut app, "props"), [0]);
        assert_eq!(source_path(&mut app, "hero"), [0, 2]);
    }

    #[test]
    fn non_entity_tags_ask_for_reload() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        edit(
            &mut app,
            &[EditOp::Insert {
                parent: "#".into(),
                index: None,
                xml: r#"<Lake at="0 0" radius="10" />"#.into(),
            }],
        );
        let status = app.world().resource::<SceneSyncStatus>();
        assert_eq!(status.needs_reload.len(), 1, "{:?}", status.needs_reload);
        assert!(status.needs_reload[0].contains("recarregar"));
    }
}
