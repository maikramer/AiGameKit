//! Edição de mundos — a API ÚNICA partilhada por agentes de IA e pelo ViberEditor.
//!
//! * [`doc::SceneDoc`] — o mundo e os seus includes, cada ficheiro com o seu documento;
//!   nós endereçados por seletores (`world/village.xml#3/0`, `name=well`).
//! * [`ops`] — operações JSON com inversa (undo), lotes atómicos e [`ops::History`].
//! * Vistas de consulta ([`list`], [`describe`], [`find`]) em JSON estável.
//!
//! Superfícies (todas sobre estas funções, nunca com lógica própria):
//! `viber scene …` (CLI, offline, grava no disco), `viber.scene.*` (bridge, mundo vivo) e o
//! ViberEditor (in-process). Uma operação feita por um agente e a mesma feita à mão no editor
//! produzem o mesmo XML.

pub mod catalog;
pub mod cli;
pub mod doc;
pub mod files;
pub mod live;
pub mod ops;
pub mod pick;
pub mod pose;
pub mod sync;
pub mod terrain;
pub mod tools;

#[cfg(test)]
mod tests;

use std::sync::Arc;

use bevy::prelude::Component;
use serde_json::{Value, json};

use crate::xml::NodeOrigin;
use doc::{NodeRef, SceneDoc, node_label};

/// O nó XML que declarou uma entidade viva (posto pelo spawn a partir de
/// `EntitySpec::source`). [`SceneDoc::node_ref_for`] converte-o num [`NodeRef`].
#[derive(Component, Debug, Clone, PartialEq)]
pub struct SceneSource(pub Arc<NodeOrigin>);

/// Os filhos de `node` (até `depth` níveis), cada um com seletor, tag, rótulo e atributos.
/// Um `<Include>` lista os nós do ficheiro incluído.
pub fn list(doc: &SceneDoc, node: &NodeRef, depth: usize) -> Value {
    let container = match doc.include_target(node) {
        Some(file) => NodeRef::root(file),
        None => node.clone(),
    };
    let Some(children) = doc.children(&container) else {
        return json!([]);
    };
    children
        .iter()
        .enumerate()
        .map(|(index, child)| {
            let child_ref = container.child(index);
            let mut entry = node_json(doc, &child_ref);
            let has_children =
                !child.children.is_empty() || doc.include_target(&child_ref).is_some();
            entry["has_children"] = json!(has_children);
            if depth > 1 && has_children {
                entry["children"] = list(doc, &child_ref, depth - 1);
            }
            entry
        })
        .collect()
}

/// Um nó: seletor, ficheiro, tag, rótulo, atributos (por ordem), texto e nº de filhos.
pub fn describe(doc: &SceneDoc, node: &NodeRef) -> Value {
    let mut entry = node_json(doc, node);
    if let Some(target) = doc.include_target(node) {
        entry["include"] = json!(doc.relative_path(target));
    }
    entry
}

/// Nós que casam com `tag` (sem distinguir maiúsculas) e/ou `attr=value` (`value` vazio:
/// basta o atributo existir).
pub fn find(doc: &SceneDoc, tag: Option<&str>, attr: Option<(&str, &str)>, limit: usize) -> Value {
    let mut out = Vec::new();
    doc.walk(|node_ref, node| {
        if out.len() >= limit {
            return;
        }
        let tag_ok = tag.is_none_or(|tag| node.tag.eq_ignore_ascii_case(tag));
        let attr_ok = attr.is_none_or(|(key, value)| match node.attr(key) {
            Some(found) => value.is_empty() || found == value,
            None => false,
        });
        if tag_ok && attr_ok {
            out.push(node_json(doc, node_ref));
        }
    });
    json!(out)
}

fn node_json(doc: &SceneDoc, node: &NodeRef) -> Value {
    let file = doc.relative_path(node.file);
    if node.is_root() {
        let scene_file = &doc.files[node.file];
        return json!({
            "selector": doc.selector(node),
            "file": file,
            "tag": scene_file.doc.root_tag,
            "attrs": attrs_json(&scene_file.doc.root_attrs),
            "child_count": scene_file.doc.children.len(),
        });
    }
    let Some(xml) = doc.node(node) else {
        return json!({ "selector": doc.selector(node), "missing": true });
    };
    let mut entry = json!({
        "selector": doc.selector(node),
        "file": file,
        "tag": xml.tag,
        "label": node_label(xml),
        "attrs": attrs_json(&xml.attrs),
        "child_count": xml.children.len(),
    });
    if !xml.text.is_empty() {
        entry["text"] = json!(xml.text);
    }
    entry
}

/// Atributos como objeto JSON (a ordem do ficheiro preserva-se no `serde_json` com
/// `preserve_order`; sem ela, alfabética — o conteúdo é o mesmo).
fn attrs_json(attrs: &[(String, String)]) -> Value {
    Value::Object(attrs.iter().map(|(k, v)| (k.clone(), json!(v))).collect())
}
