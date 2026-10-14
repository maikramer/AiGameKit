//! `viber.scene.*` — a API de edição de mundos (`crate::scene`) sobre o bridge, para agentes
//! que trabalham com a engine a correr. É a MESMA API da CLI `viber scene` e do ViberEditor;
//! aqui as edições passam por [`crate::scene::live`] (histórico + sync ao vivo).
//!
//! | método | params | resultado |
//! |---|---|---|
//! | `viber.scene.ls` | `{selector?, depth?}` | filhos com seletores |
//! | `viber.scene.get` | `{selector}` | o nó |
//! | `viber.scene.find` | `{tag?, attr?: "k=v", limit?}` | nós |
//! | `viber.scene.apply` | `{ops, label?, save?: true, coalesce?}` | transação (changes, inverse); `coalesce` (u64) funde lotes seguidos com a mesma chave num só undo |
//! | `viber.scene.undo` / `redo` | `{save?: true}` | transação ou `{"applied": 0}` |
//! | `viber.scene.save` | — | `{files}` |
//! | `viber.scene.catalog` | — | catálogo Add Node (tags + snippets) |
//! | `viber.scene.lint` | `{strict?}` | lint estático do mundo (sintaxe Luau, `viber.load`, quest-parse, nomes duplicados); `strict` com erros → BrpError |
//! | `viber.scene.drop` | `{glb, at: [x,y,z] mundo, parent?: "#", save?}` | transação (Entity + GltfScene) |
//! | `viber.scene.files` | `{depth?: 6}` | árvore da pasta do mundo |
//! | `viber.tool.list` | — | geradores `<Scatter>` |
//! | `viber.tool.canonicalize` | `{selector?, save?: true}` | poses na forma canónica |
//! | `viber.tool.settle` | `{selector, offset?: 0, save?: true}` | assenta no terreno atual |
//! | `viber.tool.scatter` | `{selector, save?: true}` | transação + resumo da ferramenta |
//! | `viber.scene.entities` | `{selector?}` (omisso: todas) | entidades vivas do nó (pose local + mundo) |
//! | `viber.scene.pick` | `{x, y}` (px da janela, câmara de editor) | `{hit, selector?, node?, point?}` |

use bevy::ecs::system::In;
use bevy::prelude::*;
use bevy::remote::BrpResult;
use serde::Deserialize;
use serde_json::{Value, json};

use super::invalid;
use crate::scene::doc::{NodeRef, SceneDoc};
use crate::scene::ops::{EditOp, History, Transaction};
use crate::scene::{cli::transaction_json, live};

pub const METHOD_LS: &str = "viber.scene.ls";
pub const METHOD_GET: &str = "viber.scene.get";
pub const METHOD_FIND: &str = "viber.scene.find";
pub const METHOD_APPLY: &str = "viber.scene.apply";
pub const METHOD_UNDO: &str = "viber.scene.undo";
pub const METHOD_REDO: &str = "viber.scene.redo";
pub const METHOD_SAVE: &str = "viber.scene.save";
pub const METHOD_PICK: &str = "viber.scene.pick";
pub const METHOD_ENTITIES: &str = "viber.scene.entities";
pub const METHOD_CATALOG: &str = "viber.scene.catalog";
pub const METHOD_LINT: &str = "viber.scene.lint";
pub const METHOD_DROP: &str = "viber.scene.drop";
pub const METHOD_FILES: &str = "viber.scene.files";
pub const METHOD_TOOL_LIST: &str = "viber.tool.list";
pub const METHOD_TOOL_SCATTER: &str = "viber.tool.scatter";
pub const METHOD_TOOL_SETTLE: &str = "viber.tool.settle";
pub const METHOD_TOOL_CANONICALIZE: &str = "viber.tool.canonicalize";

fn params<T: for<'de> Deserialize<'de>>(
    method: &str,
    value: Option<Value>,
) -> Result<T, bevy::remote::BrpError> {
    serde_json::from_value(value.unwrap_or_else(|| json!({})))
        .map_err(|error| invalid(format!("{method}: {error}")))
}

fn doc(world: &World) -> Result<&SceneDoc, bevy::remote::BrpError> {
    world
        .get_resource::<SceneDoc>()
        .ok_or_else(|| invalid("viber.scene: a engine não tem documento de mundo carregado".into()))
}

pub fn ls_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        selector: Option<String>,
        depth: Option<usize>,
    }
    let p: Params = params(METHOD_LS, input.0)?;
    let doc = doc(world)?;
    let node = match p.selector {
        Some(selector) => doc
            .resolve(&selector)
            .map_err(|e| invalid(format!("{e:#}")))?,
        None => NodeRef::root(0),
    };
    Ok(crate::scene::list(doc, &node, p.depth.unwrap_or(1).max(1)))
}

pub fn get_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        selector: String,
    }
    let p: Params = params(METHOD_GET, input.0)?;
    let doc = doc(world)?;
    let node = doc
        .resolve(&p.selector)
        .map_err(|e| invalid(format!("{e:#}")))?;
    Ok(crate::scene::describe(doc, &node))
}

pub fn find_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        tag: Option<String>,
        attr: Option<String>,
        limit: Option<usize>,
    }
    let p: Params = params(METHOD_FIND, input.0)?;
    let attr = p
        .attr
        .as_deref()
        .map(|attr| attr.split_once('=').unwrap_or((attr, "")));
    Ok(crate::scene::find(
        doc(world)?,
        p.tag.as_deref(),
        attr,
        p.limit.unwrap_or(100),
    ))
}

#[derive(Deserialize)]
struct SaveFlag {
    #[serde(default = "yes")]
    save: bool,
}

fn yes() -> bool {
    true
}

pub fn apply_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        ops: Vec<EditOp>,
        label: Option<String>,
        #[serde(default = "yes")]
        save: bool,
        /// Chave de coalescência (traços de pincel): lotes seguidos com a mesma
        /// chave fundem-se numa única transação de undo.
        #[serde(default)]
        coalesce: Option<u64>,
    }
    let p: Params = params(METHOD_APPLY, input.0)?;
    let label = p.label.unwrap_or_else(|| "bridge".into());
    let tx = match p.coalesce {
        Some(key) => live::apply_coalesced(world, &label, &p.ops, key),
        None => live::apply(world, &label, &p.ops),
    }
    .map_err(|e| invalid(format!("{e:#}")))?;
    report(world, &tx, p.save)
}

pub fn undo_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    let p: SaveFlag = params(METHOD_UNDO, input.0)?;
    match live::undo(world).map_err(|e| invalid(format!("{e:#}")))? {
        Some(tx) => report(world, &tx, p.save),
        None => Ok(json!({ "applied": 0 })),
    }
}

pub fn redo_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    let p: SaveFlag = params(METHOD_REDO, input.0)?;
    match live::redo(world).map_err(|e| invalid(format!("{e:#}")))? {
        Some(tx) => report(world, &tx, p.save),
        None => Ok(json!({ "applied": 0 })),
    }
}

pub fn save_method(_input: In<Option<Value>>, world: &mut World) -> BrpResult {
    let files = live::save(world).map_err(|e| invalid(format!("{e:#}")))?;
    Ok(json!({ "files": files }))
}

pub fn catalog_method(_input: In<Option<Value>>, _world: &mut World) -> BrpResult {
    Ok(crate::scene::catalog::to_json())
}

/// Lint estático do mundo A CORRER (lê os ficheiros do `root_dir` do
/// documento — o mesmo relatório do `viber scene lint` offline). `strict`
/// devolve BrpError se houver findings de severidade error.
pub fn lint_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        #[serde(default)]
        strict: bool,
    }
    let p: Params = params(METHOD_LINT, input.0)?;
    // `files[0]` é o world root — existe no HEAD e no refactor em curso
    // (o `world_path()` novo é o mesmo caminho).
    let world_xml = doc(world)?
        .files
        .first()
        .map(|file| file.path.clone())
        .ok_or_else(|| invalid("documento sem ficheiros".into()))?;
    let report = crate::lint::lint_world(&world_xml).map_err(|e| invalid(format!("{e:#}")))?;
    let out = crate::lint::report_json(&report);
    if p.strict && report.errors().count() > 0 {
        return Err(invalid(format!(
            "lint: {} erro(s) — ver o campo errors do relatório",
            report.errors().count()
        )));
    }
    Ok(out)
}

pub fn drop_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        glb: String,
        at: [f32; 3],
        #[serde(default = "root")]
        parent: String,
        #[serde(default = "yes")]
        save: bool,
    }
    fn root() -> String {
        "#".into()
    }
    let p: Params = params(METHOD_DROP, input.0)?;
    let op = crate::scene::catalog::glb_drop(doc(world)?, &p.parent, &p.glb, p.at)
        .map_err(|e| invalid(format!("{e:#}")))?;
    let tx = live::apply(world, &format!("drop {}", p.glb), &[op])
        .map_err(|e| invalid(format!("{e:#}")))?;
    report(world, &tx, p.save)
}

pub fn files_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        depth: Option<usize>,
    }
    let p: Params = params(METHOD_FILES, input.0)?;
    let dir = doc(world)?.root_dir.clone();
    Ok(crate::scene::files::to_json(&crate::scene::files::scan(
        &dir,
        p.depth.unwrap_or(6),
    )))
}

pub fn tool_list_method(_input: In<Option<Value>>, world: &mut World) -> BrpResult {
    Ok(crate::scene::tools::list_scatters(doc(world)?))
}

pub fn tool_scatter_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        selector: String,
        #[serde(default = "yes")]
        save: bool,
    }
    let p: Params = params(METHOD_TOOL_SCATTER, input.0)?;
    let output =
        crate::scene::tools::scatter(world, &p.selector).map_err(|e| invalid(format!("{e:#}")))?;
    let tx =
        live::apply(world, &output.label, &output.ops).map_err(|e| invalid(format!("{e:#}")))?;
    let mut out = report(world, &tx, p.save)?;
    out["tool"] = output.summary();
    Ok(out)
}

pub fn tool_canonicalize_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        selector: Option<String>,
        #[serde(default = "yes")]
        save: bool,
    }
    let p: Params = params(METHOD_TOOL_CANONICALIZE, input.0)?;
    let output = crate::scene::tools::canonicalize(doc(world)?, p.selector.as_deref())
        .map_err(|e| invalid(format!("{e:#}")))?;
    let tx =
        live::apply(world, &output.label, &output.ops).map_err(|e| invalid(format!("{e:#}")))?;
    let mut out = report(world, &tx, p.save)?;
    out["tool"] = output.summary();
    out["inverse"] = json!(format!("{} ops (omitidas)", tx.inverse.len()));
    Ok(out)
}

pub fn tool_settle_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        selector: String,
        #[serde(default)]
        offset: f32,
        #[serde(default = "yes")]
        save: bool,
    }
    let p: Params = params(METHOD_TOOL_SETTLE, input.0)?;
    let output = {
        let mut doc = world
            .get_resource_mut::<SceneDoc>()
            .ok_or_else(|| invalid("viber.tool.settle: sem documento".into()))?;
        crate::scene::tools::settle(&mut doc, &p.selector, p.offset)
            .map_err(|e| invalid(format!("{e:#}")))?
    };
    let tx =
        live::apply(world, &output.label, &output.ops).map_err(|e| invalid(format!("{e:#}")))?;
    let mut out = report(world, &tx, p.save)?;
    out["tool"] = output.summary();
    Ok(out)
}

pub fn entities_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        /// Seletor do nó origem; omisso = todas as entidades vivas do documento.
        #[serde(default)]
        selector: Option<String>,
    }
    let p: Params = params(METHOD_ENTITIES, input.0)?;
    let origin = match p.selector.as_deref() {
        Some(selector) => {
            let doc = doc(world)?;
            let node = doc
                .resolve(selector)
                .map_err(|e| invalid(format!("{e:#}")))?;
            Some(doc.origin_of(&node))
        }
        None => None,
    };
    let mut query = world.query::<(
        Entity,
        &crate::scene::SceneSource,
        &Transform,
        Option<&GlobalTransform>,
    )>();
    let entities: Vec<Value> = query
        .iter(world)
        .filter(|(_, source, _, _)| origin.as_ref().is_none_or(|origin| *source.0 == *origin))
        .map(|(entity, _, transform, global)| {
            let world_position = global.map(|g| g.translation().to_array());
            json!({
                "entity": entity.to_bits(),
                "translation": transform.translation.to_array(),
                "rotation": transform.rotation.to_array(),
                "scale": transform.scale.to_array(),
                "world_translation": world_position,
            })
        })
        .collect();
    Ok(json!({ "selector": p.selector, "entities": entities }))
}

pub fn pick_method(input: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        max_distance: Option<f32>,
    }
    let p: Params = params(METHOD_PICK, input.0)?;
    let ray = {
        let mut cameras = world.query_filtered::<(&Camera, &GlobalTransform), With<crate::editor_camera::EditorCamera>>();
        let Ok((camera, global)) = cameras.single(world) else {
            return Err(invalid(format!(
                "{METHOD_PICK}: sem câmara de editor (`viber run --camera free`)"
            )));
        };
        camera.viewport_to_world(global, Vec2::new(p.x, p.y))
    };
    let Ok(ray) = ray else {
        return Ok(json!({ "hit": false }));
    };
    let Some(picked) = crate::scene::pick::pick(
        world,
        ray.origin,
        *ray.direction,
        p.max_distance.unwrap_or(2000.0),
    ) else {
        return Ok(json!({ "hit": false }));
    };
    let doc = doc(world)?;
    Ok(json!({
        "hit": true,
        "selector": doc.selector(&picked.node),
        "node": crate::scene::describe(doc, &picked.node),
        "point": [picked.point.x, picked.point.y, picked.point.z],
        "distance": picked.distance,
    }))
}

fn report(world: &mut World, tx: &Transaction, save: bool) -> BrpResult {
    let files = if save {
        live::save(world).map_err(|e| invalid(format!("{e:#}")))?
    } else {
        let doc = doc(world)?;
        doc.dirty_paths()
    };
    // O sync do documento→ECS só correria no próximo PostUpdate; a resposta
    // traz-lo AGORA para o campo `needs_reload` — é o que diz ao agente (ou ao
    // CLI --live) o que exige Ctrl+R, em vez de ele descobrir pelo log. O
    // recurso acumula (a toolbar do editor lê-o a cada frame): reportamos só o
    // DELTA desta transação.
    let before = world
        .get_resource::<crate::scene::sync::SceneSyncStatus>()
        .map_or(0, |status| status.needs_reload.len());
    crate::scene::sync::sync_scene(world);
    let needs_reload: Vec<String> = world
        .get_resource::<crate::scene::sync::SceneSyncStatus>()
        .map(|status| status.needs_reload[before.min(status.needs_reload.len())..].to_vec())
        .unwrap_or_default();
    let mut out = transaction_json(doc(world)?, tx, &files, save);
    out["needs_reload"] = json!(needs_reload);
    let history = world.resource::<History>();
    out["can_undo"] = json!(history.can_undo());
    out["can_redo"] = json!(history.can_redo());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::doc::tests::fixture;

    fn app(dir: &std::path::Path) -> App {
        let mut app = App::new();
        app.add_plugins(live::ScenePlugin);
        app.add_plugins(crate::scene::sync::SceneSyncPlugin);
        app.insert_resource(SceneDoc::load(&fixture(dir)).unwrap());
        app
    }

    fn call(
        app: &mut App,
        method: fn(In<Option<Value>>, &mut World) -> BrpResult,
        params: Value,
    ) -> BrpResult {
        method(In(Some(params)), app.world_mut())
    }

    #[test]
    fn agent_edit_roundtrip_over_the_bridge() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let top = call(&mut app, ls_method, json!({})).unwrap();
        assert_eq!(top.as_array().unwrap().len(), 3);

        let out = call(
            &mut app,
            apply_method,
            json!({"ops": [{"op": "set", "node": "name=well", "attrs": {"translation": "7 0 2"}}], "label": "agent"}),
        )
        .unwrap();
        assert_eq!(out["applied"], 1);
        assert_eq!(out["files"], json!(["world/village.xml"]));
        assert_eq!(out["saved"], true);
        assert_eq!(out["can_undo"], true);
        let on_disk = std::fs::read_to_string(tmp.path().join("world/village.xml")).unwrap();
        assert!(on_disk.contains("translation=\"7 0 2\""), "{on_disk}");

        let well = call(&mut app, get_method, json!({"selector": "name=well"})).unwrap();
        assert_eq!(well["attrs"]["translation"], "7 0 2");

        let undone = call(&mut app, undo_method, json!({})).unwrap();
        assert_eq!(undone["can_redo"], true);
        let on_disk = std::fs::read_to_string(tmp.path().join("world/village.xml")).unwrap();
        assert!(
            on_disk.contains("translation=\"4 0 2\""),
            "undo gravado: {on_disk}"
        );
        assert_eq!(
            call(&mut app, undo_method, json!({})).unwrap()["applied"],
            0
        );
    }

    #[test]
    fn apply_with_coalesce_merges_strokes_into_one_undo() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        for translation in ["5 0 2", "6 0 2"] {
            let out = call(
                &mut app,
                apply_method,
                json!({
                    "ops": [{"op": "set", "node": "name=well", "attrs": {"translation": translation}}],
                    "label": "agent",
                    "coalesce": 42
                }),
            )
            .unwrap();
            assert_eq!(out["applied"], 1);
        }
        // As duas aplicações fundiram-se: um undo devolve o original.
        let undone = call(&mut app, undo_method, json!({})).unwrap();
        assert_eq!(
            undone["applied"], 2,
            "a transação fundida desfaz as duas ops"
        );
        let on_disk = std::fs::read_to_string(tmp.path().join("world/village.xml")).unwrap();
        assert!(on_disk.contains("translation=\"4 0 2\""), "{on_disk}");
        assert_eq!(
            call(&mut app, undo_method, json!({})).unwrap()["applied"],
            0
        );
    }

    /// A resposta do apply/undo/redo informa o AGENTE do que ficou
    /// `needs_reload` (o sync corre agora, não no próximo PostUpdate) — sem o
    /// campo, ele só descobriria pelo log da engine.
    #[test]
    fn apply_response_reports_needs_reload_delta() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        // Edição pose-only: nada exige reload.
        let pose = call(
            &mut app,
            apply_method,
            json!({"ops": [{"op": "set", "node": "name=well", "attrs": {"translation": "9 0 9"}}]}),
        )
        .unwrap();
        assert_eq!(pose["needs_reload"], json!([]));
        // Inserir um <Lake> (carve de feature) não nasce a quente: a resposta
        // tem de dizer logo que exige recarregar.
        let lake = call(
            &mut app,
            apply_method,
            json!({"ops": [{"op": "insert", "parent": "#", "xml": "<Lake at=\"0 0\" radius=\"10\" />"}]}),
        )
        .unwrap();
        assert_eq!(lake["applied"], 1);
        let reload = lake["needs_reload"].as_array().unwrap();
        assert_eq!(reload.len(), 1, "{lake}");
        assert!(reload[0].as_str().unwrap().contains("recarregar"));
        // O delta é POR TRANSAÇÃO: uma edição pose-only a seguir zera o campo
        // (o recurso acumula para a toolbar do editor; a resposta não).
        let again = call(
            &mut app,
            apply_method,
            json!({"ops": [{"op": "set", "node": "name=well", "attrs": {"translation": "4 0 2"}}]}),
        )
        .unwrap();
        assert_eq!(again["needs_reload"], json!([]));
    }

    #[test]
    fn bad_requests_are_brp_errors() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        assert!(call(&mut app, get_method, json!({"selector": "name=ghost"})).is_err());
        assert!(call(&mut app, apply_method, json!({"ops": [{"op": "nope"}]})).is_err());
        let found = call(
            &mut app,
            find_method,
            json!({"tag": "Entity", "attr": "name=crate"}),
        )
        .unwrap();
        assert_eq!(found.as_array().unwrap().len(), 2);
    }
}
