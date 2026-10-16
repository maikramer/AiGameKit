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

/// A entidade é o ROOT de uma instância `<Use prototype="…">` — o editor
/// distingue instância da definição (banner, reset aos overrides).
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct PrototypeInstance(pub String);

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
    // Nó com script: os props exportados efetivos (o esquema completo é `scene schema`).
    if doc
        .node(node)
        .is_some_and(|xml| xml.attr("script").is_some())
        && let Ok(schema) = script_schema(doc, &doc.selector(node))
    {
        entry["script_props"] = schema["values"].clone();
    }
    entry
}

/// Pasta de scripts do mundo do documento (`config.yaml` → `game.scripts_dir`; sem
/// config, `<mundo>/scripts`).
pub fn scripts_dir(doc: &SceneDoc) -> std::path::PathBuf {
    let world_dir = doc.root_dir.clone();
    match crate::config::load(&world_dir) {
        Ok(config) => config.scripts_dir_on(&world_dir),
        Err(_) => world_dir.join("scripts"),
    }
}

/// O esquema de um script (`--@export` + `--@signal` + funções globais) e, num nó, os
/// valores efetivos da instância e as ligações:
/// `{script, exports:[{name,type,attr,default,doc,min,max,options}], signals:[{name,args,
/// attr,doc}], methods:[…], errors, values:{nome:{value, source:"instance"|"default",
/// error?}}, unknown:[…], groups:[…], connections:{sinal:{value,targets,declared}}}`.
/// `target` é um seletor de nó (com `script=`) ou o caminho de um `.lua` (só o esquema).
/// É o que um agente lê antes de `scene set <nó> prop-<nome>=…`.
pub fn script_schema(doc: &SceneDoc, target: &str) -> anyhow::Result<Value> {
    use crate::luau::exports::{parse_exports, prop_name};
    let scripts_dir = scripts_dir(doc);
    let (script, props, selector) = if target.ends_with(".lua") {
        (target.to_owned(), None, None)
    } else {
        let node = doc.resolve(target)?;
        let xml = doc
            .node(&node)
            .ok_or_else(|| anyhow::anyhow!("{target}: nó sem atributos"))?;
        let script = xml
            .attr("script")
            .ok_or_else(|| anyhow::anyhow!("{target}: o nó não tem script="))?
            .to_owned();
        let props: Vec<(String, String)> = xml
            .attrs
            .iter()
            .filter_map(|(k, v)| prop_name(k).map(|name| (name.to_owned(), v.clone())))
            .collect();
        let selector = doc.selector(&node);
        (script, Some((props, node)), Some(selector))
    };
    let path = crate::lint::resolve_script(&scripts_dir, &script);
    let source = std::fs::read_to_string(&path)
        .map_err(|e| anyhow::anyhow!("a ler {}: {e}", path.display()))?;
    let exports = parse_exports(&source);
    let mut out = exports.to_json();
    out["script"] = json!(script);
    if let Some(selector) = selector {
        out["selector"] = json!(selector);
    }
    if let Some((props, node)) = props {
        out["values"] = props_values(&exports, &props);
        out["unknown"] = json!(exports.unknown(&props));
        let xml = doc.node(&node).expect("resolvido acima");
        let links = crate::recipes::NodeLinks::of_node(xml);
        out["groups"] = json!(links.groups);
        out["connections"] = connections_json(doc, &exports, &links);
    }
    Ok(out)
}

/// `{sinal: {value, targets:[{target, method, nodes}], declared, error?}}` — cada sinal
/// declarado (ligado ou não) e cada `connect-*` do nó; `nodes` = quantos nós o alvo
/// alcança no documento (0 = alvo partido).
fn connections_json(
    doc: &SceneDoc,
    exports: &crate::luau::exports::ScriptExports,
    links: &crate::recipes::NodeLinks,
) -> Value {
    use crate::luau::exports::{ConnTarget, parse_connections};
    let mut out = serde_json::Map::new();
    for signal in &exports.signals {
        out.insert(
            signal.name.clone(),
            json!({ "value": null, "targets": [], "declared": true }),
        );
    }
    let index = group_index(doc);
    for (signal, raw) in &links.connections {
        let mut entry = json!({
            "value": raw,
            "declared": exports.signal(signal).is_some(),
        });
        match parse_connections(raw) {
            Ok(conns) => {
                entry["targets"] = conns
                    .iter()
                    .map(|conn| {
                        let nodes = match &conn.target {
                            ConnTarget::Name(name) if name == "self" => 1,
                            ConnTarget::Name(name) => doc.find_by_attr("name", name).len(),
                            ConnTarget::Group(group) => index.get(group).map_or(0, Vec::len),
                        };
                        json!({ "target": conn.target.to_string(), "method": conn.method, "nodes": nodes })
                    })
                    .collect();
            }
            Err(error) => {
                entry["targets"] = json!([]);
                entry["error"] = json!(error);
            }
        }
        out.insert(signal.clone(), entry);
    }
    Value::Object(out)
}

/// grupo → nós com `groups="…"` que o contêm (todos os ficheiros, pré-ordem).
pub fn group_index(doc: &SceneDoc) -> std::collections::BTreeMap<String, Vec<NodeRef>> {
    let mut index: std::collections::BTreeMap<String, Vec<NodeRef>> = Default::default();
    doc.walk(|node_ref, xml| {
        if let Some(value) = xml.attr("groups") {
            for group in crate::luau::exports::parse_groups(value).0 {
                index.entry(group).or_default().push(node_ref.clone());
            }
        }
    });
    index
}

/// `viber scene groups [nome]`: `{grupo: [{selector, tag, label, script?}]}`.
pub fn groups(doc: &SceneDoc, only: Option<&str>) -> Value {
    let mut out = serde_json::Map::new();
    for (group, nodes) in group_index(doc) {
        if only.is_some_and(|only| only != group) {
            continue;
        }
        let members: Vec<Value> = nodes
            .iter()
            .filter_map(|node| {
                let xml = doc.node(node)?;
                let mut entry = json!({
                    "selector": doc.selector(node),
                    "tag": xml.tag,
                    "label": node_label(xml),
                });
                if let Some(script) = xml.attr("script") {
                    entry["script"] = json!(script);
                }
                Some(entry)
            })
            .collect();
        out.insert(group, json!(members));
    }
    Value::Object(out)
}

/// `{nome: {value, source, error?}}` — o valor efetivo de cada export.
fn props_values(
    exports: &crate::luau::exports::ScriptExports,
    props: &[(String, String)],
) -> Value {
    let mut values = serde_json::Map::new();
    for prop in exports.resolve(props) {
        let mut entry = json!({
            "value": prop.value.as_ref().map(crate::luau::exports::PropValue::to_json),
            "source": prop.source.as_str(),
        });
        if let Some(error) = &prop.error {
            entry["error"] = json!(error);
        }
        values.insert(prop.def.name.clone(), entry);
    }
    Value::Object(values)
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
