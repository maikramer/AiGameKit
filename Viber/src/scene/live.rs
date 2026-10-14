//! Edição do mundo VIVO: o [`SceneDoc`] e o [`History`] como resources da App, e as funções
//! únicas por onde passam TODAS as edições ao vivo — as do bridge (`viber.scene.*`, agentes)
//! e as do ViberEditor (gizmos, Inspector, atalhos). Cada edição aplicada publica um
//! [`SceneEdited`]; o sync documento→ECS reage a essa mensagem, venha ela de onde vier.

use anyhow::{Result, anyhow};
use bevy::prelude::*;

use super::doc::SceneDoc;
use super::ops::{Change, EditOp, History, Transaction};

/// O documento mudou (aplicar, desfazer ou refazer). `changes` pela ordem em que ocorreram.
#[derive(Message, Debug, Clone)]
pub struct SceneEdited {
    pub label: String,
    pub changes: Vec<Change>,
}

/// Regista a mensagem e o histórico. O documento é inserido por quem conhece o mundo
/// (`viber::app::build_app`).
pub struct ScenePlugin;

impl Plugin for ScenePlugin {
    fn build(&self, app: &mut App) {
        app.add_message::<SceneEdited>().init_resource::<History>();
    }
}

/// Aplica um lote ao documento vivo (com histórico) e publica a mudança.
pub fn apply(world: &mut World, label: &str, ops: &[EditOp]) -> Result<Transaction> {
    with_doc(world, |doc, history| history.apply(doc, label, ops)).map(|tx| publish(world, tx))
}

/// Como [`apply`], fundindo com a última transação se tiver a mesma `key` (traços de pincel).
pub fn apply_coalesced(
    world: &mut World,
    label: &str,
    ops: &[EditOp],
    key: u64,
) -> Result<Transaction> {
    with_doc(world, |doc, history| {
        history.apply_coalesced(doc, label, ops, key)
    })
    .map(|tx| publish(world, tx))
}

/// Desfaz a última edição; `None` se não havia nada.
pub fn undo(world: &mut World) -> Result<Option<Transaction>> {
    with_doc(world, |doc, history| history.undo(doc)).map(|tx| tx.map(|tx| publish(world, tx)))
}

/// Refaz a última edição desfeita.
pub fn redo(world: &mut World) -> Result<Option<Transaction>> {
    with_doc(world, |doc, history| history.redo(doc)).map(|tx| tx.map(|tx| publish(world, tx)))
}

/// Grava os ficheiros alterados; devolve os caminhos (relativos ao mundo) escritos.
pub fn save(world: &mut World) -> Result<Vec<String>> {
    let mut doc = world
        .get_resource_mut::<SceneDoc>()
        .ok_or_else(|| anyhow!("scene: sem documento carregado"))?;
    let dirty: Vec<String> = doc.dirty_paths();
    doc.save()?;
    Ok(dirty)
}

fn with_doc<T>(
    world: &mut World,
    f: impl FnOnce(&mut SceneDoc, &mut History) -> Result<T>,
) -> Result<T> {
    let mut history = world.remove_resource::<History>().unwrap_or_default();
    let result = match world.get_resource_mut::<SceneDoc>() {
        Some(mut doc) => f(&mut doc, &mut history),
        None => Err(anyhow!("scene: sem documento carregado")),
    };
    world.insert_resource(history);
    result
}

fn publish(world: &mut World, tx: Transaction) -> Transaction {
    if !tx.changes.is_empty() {
        world.write_message(SceneEdited {
            label: tx.label.clone(),
            changes: tx.changes.clone(),
        });
    }
    tx
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::doc::tests::fixture;

    fn app(dir: &std::path::Path) -> App {
        let mut app = App::new();
        app.add_plugins(ScenePlugin);
        app.insert_resource(SceneDoc::load(&fixture(dir)).unwrap());
        app
    }

    fn edited(app: &mut App) -> Vec<SceneEdited> {
        app.world_mut()
            .resource_mut::<Messages<SceneEdited>>()
            .drain()
            .collect()
    }

    #[test]
    fn apply_undo_redo_publish_changes() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let op = EditOp::Remove {
            node: "name=hero".into(),
        };
        apply(app.world_mut(), "rm", &[op]).unwrap();
        let messages = edited(&mut app);
        assert_eq!(messages.len(), 1);
        assert!(matches!(messages[0].changes[0], Change::Removed { .. }));

        assert!(undo(app.world_mut()).unwrap().is_some());
        assert!(matches!(
            edited(&mut app)[0].changes[0],
            Change::Inserted { .. }
        ));
        assert!(redo(app.world_mut()).unwrap().is_some());
        assert!(undo(app.world_mut()).unwrap().is_some());
        assert!(
            undo(app.world_mut()).unwrap().is_none(),
            "histórico esgotado"
        );
    }

    #[test]
    fn coalesced_applies_with_the_same_key_are_one_undo() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let stroke = |translation: &str| EditOp::Set {
            node: "name=well".into(),
            attrs: [("translation".to_string(), Some(translation.to_string()))]
                .into_iter()
                .collect(),
        };
        apply_coalesced(app.world_mut(), "brush", &[stroke("5 0 2")], 1).unwrap();
        apply_coalesced(app.world_mut(), "brush", &[stroke("6 0 2")], 1).unwrap();
        // Chave diferente: transação própria.
        apply_coalesced(app.world_mut(), "outra", &[stroke("7 0 2")], 2).unwrap();
        let translation = |app: &App| {
            let doc = app.world().resource::<SceneDoc>();
            doc.node(&doc.resolve("name=well").unwrap())
                .unwrap()
                .attr("translation")
                .unwrap()
                .to_string()
        };
        // 3 applies, 2 undos: o primeiro desfaz a chave 2, o segundo AS DUAS da chave 1.
        undo(app.world_mut()).unwrap();
        assert_eq!(translation(&app), "6 0 2");
        undo(app.world_mut()).unwrap();
        assert_eq!(
            translation(&app),
            "4 0 2",
            "as duas pinceladas fundidas desfazem juntas"
        );
        assert!(
            undo(app.world_mut()).unwrap().is_none(),
            "histórico esgotado em 2 undos"
        );
    }

    #[test]
    fn failed_apply_publishes_nothing_and_keeps_history() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let bad = EditOp::Remove {
            node: "name=ghost".into(),
        };
        assert!(apply(app.world_mut(), "bad", &[bad]).is_err());
        assert!(edited(&mut app).is_empty());
        assert!(!app.world().resource::<History>().can_undo());
    }

    #[test]
    fn save_reports_written_files() {
        let tmp = tempfile::tempdir().unwrap();
        let mut app = app(tmp.path());
        let op = EditOp::Insert {
            parent: "world/village.xml#".into(),
            index: None,
            xml: "<Entity name=\"cart\" />".into(),
        };
        apply(app.world_mut(), "add", &[op]).unwrap();
        assert_eq!(save(app.world_mut()).unwrap(), ["world/village.xml"]);
        assert!(save(app.world_mut()).unwrap().is_empty());
    }
}
