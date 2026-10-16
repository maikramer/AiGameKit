//! Operações de edição sobre o [`SceneDoc`] — o vocabulário único partilhado por agentes
//! (`viber scene apply ops.json`), pelo bridge (`viber.scene.apply`) e pelo ViberEditor.
//!
//! Cada operação aplicada devolve a sua **inversa** (o undo) e as [`Change`]s que provocou (o
//! que o sync ao vivo precisa de refletir no ECS). Um lote ([`apply_batch`]) é atómico: se uma
//! operação falha, as anteriores são revertidas e o documento fica como estava.
//!
//! Formato JSON (um array de objetos com `op`):
//!
//! ```json
//! [
//!   {"op": "set", "node": "name=well", "attrs": {"translation": "4 0 3", "scale": null}},
//!   {"op": "insert", "parent": "world/village.xml#", "xml": "<Entity name=\"cart\" />"},
//!   {"op": "move", "node": "name=cart", "parent": "name=props", "index": 0},
//!   {"op": "duplicate", "node": "name=crate"},
//!   {"op": "set_text", "node": "#4/2", "text": "100/100"},
//!   {"op": "remove", "node": "name=barrel"},
//!   {"op": "extract", "node": "name=forge", "to": "scenes/forge.xml"}
//! ]
//! ```
//!
//! `attrs` com `null` remove o atributo. Inserir/mover para dentro de um `<Include>` vai para o
//! elemento raiz do ficheiro incluído (como a árvore de cena o mostra).

use std::collections::BTreeMap;

use anyhow::{Context, Result, anyhow, bail};
use serde::{Deserialize, Serialize};

use super::doc::{NodeRef, SceneDoc};
use crate::xml::emit::emit_document;
use crate::xml::{XmlDocument, XmlNode, parse_fragment};

/// Uma operação de edição. Os nós endereçam-se por seletor (ver [`super::doc`]).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum EditOp {
    /// Define (ou, com `null`, remove) atributos. No raiz de um ficheiro (`ficheiro#`)
    /// mexe nos atributos do elemento raiz (`<world static="true">`).
    Set {
        node: String,
        attrs: BTreeMap<String, Option<String>>,
    },
    /// Substitui TODOS os atributos pela lista dada, por esta ordem (é a inversa exata de `set`:
    /// repõe também a ordem original, para o undo não deixar ruído no diff).
    ReplaceAttrs {
        node: String,
        attrs: Vec<(String, String)>,
    },
    /// Substitui o texto direto do nó (`<UiText>100/100</UiText>`).
    SetText { node: String, text: String },
    /// Insere um ou mais elementos XML como filhos de `parent`, a partir de `index` (omisso: no fim).
    Insert {
        parent: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        index: Option<usize>,
        xml: String,
    },
    /// Remove o nó e a sua subárvore.
    Remove { node: String },
    /// Move o nó para debaixo de `parent`, na posição `index` (omisso: no fim).
    Move {
        node: String,
        parent: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        index: Option<usize>,
    },
    /// Duplica o nó logo a seguir a ele próprio.
    Duplicate { node: String },
    /// "Salvar ramo como cena" (Godot): o nó e a subárvore passam para um ficheiro NOVO
    /// `to` (relativo à pasta do mundo; omisso: `scenes/<nome>.xml`) e no lugar fica
    /// `<Include src="/to">` com a pose do nó — a cena fica reutilizável (`instance`).
    Extract {
        node: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        to: Option<String>,
    },
    /// Cria um ficheiro de cena (`<world static="true">` + `xml`), gravado no `save`.
    /// Recusa ficheiros que já existem. Base do `extract` (e a sua inversa é `drop_file`).
    CreateFile {
        path: String,
        xml: String,
        /// `<world title>` da cena nova (projetos com cenas).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        title: Option<String>,
    },
    /// Esquece um ficheiro criado nesta sessão (o `save` apaga-o do disco).
    DropFile { path: String },
    /// Pincel no heightmap do mundo (`at` = XZ mundo, `radius` m). `amount`: metros para
    /// raise/lower/crater, força 0..1 para flatten/smooth (omisso 1). `height`: alvo do
    /// flatten (omisso: a cota no centro).
    TerrainBrush {
        mode: super::terrain::BrushMode,
        at: [f32; 2],
        radius: f32,
        #[serde(default = "one")]
        amount: f32,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        height: Option<f32>,
    },
    /// Valores crus exatos de um retângulo de células (base64 u16 LE) — a inversa de um
    /// pincel.
    TerrainPatch {
        rect: super::terrain::CellRect,
        data: String,
    },
    /// Pincel de MATERIAL: pinta a camada `layer` do pool (alias, ex. `dirt_trail`)
    /// com `amount` 0..1 (0 = limpa). As alturas não mudam.
    TerrainPaint {
        at: [f32; 2],
        radius: f32,
        layer: String,
        #[serde(default = "one")]
        amount: f32,
    },
    /// Bytes exatos da camada de pintura de um retângulo (base64 slot+peso)
    /// — a inversa de um pincel de material.
    SplatPatch {
        rect: super::terrain::CellRect,
        data: String,
    },
    /// Campo de BIOMAS completo (núcleo + transição + cunhas) — o editor de
    /// biomas. A inversa é o snapshot do campo anterior.
    TerrainBiomes {
        core: f32,
        blend: f32,
        /// `[dir_x, dir_y, biome]` por cunha (biome = nome canónico).
        wedges: Vec<(f32, f32, String)>,
    },
}

fn one() -> f32 {
    1.0
}

/// O que uma operação mudou no documento — o sync ao vivo traduz isto para o ECS.
#[derive(Debug, Clone, PartialEq)]
pub enum Change {
    /// Atributos (nomes) ou texto de um nó mudaram.
    Attrs { node: NodeRef, keys: Vec<String> },
    /// Uma subárvore nova nasceu neste caminho.
    Inserted { node: NodeRef },
    /// A subárvore neste caminho desapareceu (caminho ANTES da remoção).
    Removed { node: NodeRef },
    /// Células do heightmap mudaram.
    Terrain { rect: super::terrain::CellRect },
    /// Células da camada de material (pintura) mudaram — repintar só os chunks tocados.
    Paint { rect: super::terrain::CellRect },
    /// O campo de biomas mudou — re-bake completo dos splats.
    Biomes,
}

/// Resultado de aplicar uma operação.
#[derive(Debug, Clone)]
pub struct Applied {
    /// A operação, com seletores canónicos (para redo).
    pub forward: EditOp,
    /// As operações que desfazem esta, pela ordem em que se aplicam.
    pub inverse: Vec<EditOp>,
    pub changes: Vec<Change>,
}

/// Um lote aplicado atomicamente — a unidade de undo/redo.
#[derive(Debug, Clone, Default)]
pub struct Transaction {
    pub label: String,
    /// As operações, canónicas, pela ordem em que foram aplicadas.
    pub forward: Vec<EditOp>,
    /// As inversas, pela ordem em que DESFAZEM (a última primeiro).
    pub inverse: Vec<EditOp>,
    pub changes: Vec<Change>,
    /// Chave de agrupamento: lotes seguidos com a mesma chave fundem-se nesta transação (um
    /// traço de pincel = um undo). `None` = transação própria.
    pub coalesce: Option<u64>,
}

/// Aplica uma operação.
pub fn apply(doc: &mut SceneDoc, op: &EditOp) -> Result<Applied> {
    match op {
        EditOp::Set { node, attrs } => {
            let node = doc.resolve(node)?;
            set_attrs(doc, node, attrs)
        }
        EditOp::ReplaceAttrs { node, attrs } => {
            let node = doc.resolve(node)?;
            if let Some((bad, _)) = attrs.iter().find(|(key, _)| !valid_attr_name(key)) {
                bail!("replace_attrs: nome de atributo inválido `{bad}`");
            }
            let previous = std::mem::replace(attrs_of_mut(doc, &node), attrs.clone());
            // Só as chaves cujo valor mudou: o undo de um `set` de pose/prop é um
            // `replace_attrs` com a lista inteira, e reportar tudo forçava o sync a
            // re-spawnar (o script perdia o estado) em vez de patchar.
            let value = |list: &[(String, String)], key: &str| {
                list.iter().find(|(k, _)| k == key).map(|(_, v)| v.clone())
            };
            let mut keys: Vec<String> = previous
                .iter()
                .chain(attrs.iter())
                .map(|(k, _)| k.clone())
                .filter(|k| value(&previous, k) != value(attrs, k))
                .collect();
            keys.sort();
            keys.dedup();
            doc.touch(node.file);
            let selector = doc.selector(&node);
            Ok(Applied {
                forward: EditOp::ReplaceAttrs {
                    node: selector.clone(),
                    attrs: attrs.clone(),
                },
                inverse: vec![EditOp::ReplaceAttrs {
                    node: selector,
                    attrs: previous,
                }],
                changes: vec![Change::Attrs { node, keys }],
            })
        }
        EditOp::SetText { node, text } => {
            let node = doc.resolve(node)?;
            if node.is_root() {
                bail!("set_text: o elemento raiz não tem texto editável");
            }
            let target = doc.node_mut(&node).expect("resolvido");
            let previous = std::mem::replace(&mut target.text, text.clone());
            doc.touch(node.file);
            let selector = doc.selector(&node);
            Ok(Applied {
                forward: EditOp::SetText {
                    node: selector.clone(),
                    text: text.clone(),
                },
                inverse: vec![EditOp::SetText {
                    node: selector,
                    text: previous,
                }],
                changes: vec![Change::Attrs {
                    node,
                    keys: vec!["#text".into()],
                }],
            })
        }
        EditOp::Insert { parent, index, xml } => {
            let nodes = parse_fragment(xml)?;
            if nodes.is_empty() {
                bail!("insert: o XML não tem elementos");
            }
            let parent = container(doc, doc.resolve(parent)?);
            insert_nodes(doc, &parent, *index, nodes)
        }
        EditOp::Remove { node } => {
            let node = doc.resolve(node)?;
            remove_node(doc, &node)
        }
        EditOp::Move {
            node,
            parent,
            index,
        } => {
            let node = doc.resolve(node)?;
            let parent = container(doc, doc.resolve(parent)?);
            move_node(doc, &node, &parent, *index)
        }
        EditOp::TerrainBrush {
            mode,
            at,
            radius,
            amount,
            height,
        } => {
            let terrain = doc.terrain_mut()?;
            let at_v = bevy::math::Vec2::from(*at);
            let rect = terrain
                .rect_around(at_v, radius.min(crate::terrain::delta::EDIT_MAX_RADIUS))
                .ok_or_else(|| anyhow!("terrain_brush: fora do mapa"))?;
            let before = terrain.snapshot(rect);
            let touched = terrain.brush(*mode, at_v, *radius, *amount, *height)?;
            debug_assert_eq!(touched, rect);
            doc.revision += 1;
            Ok(Applied {
                forward: op.clone(),
                inverse: vec![EditOp::TerrainPatch { rect, data: before }],
                changes: vec![Change::Terrain { rect }],
            })
        }
        EditOp::TerrainPatch { rect, data } => {
            let terrain = doc.terrain_mut()?;
            let before = terrain.snapshot(*rect);
            terrain.restore(*rect, data)?;
            doc.revision += 1;
            Ok(Applied {
                forward: op.clone(),
                inverse: vec![EditOp::TerrainPatch {
                    rect: *rect,
                    data: before,
                }],
                changes: vec![Change::Terrain { rect: *rect }],
            })
        }
        EditOp::TerrainPaint {
            at,
            radius,
            layer,
            amount,
        } => {
            let at_v = bevy::math::Vec2::from(*at);
            let slot = crate::terrain::paint::slot_for_alias(layer)
                .ok_or_else(|| anyhow!("terrain_paint: camada desconhecida `{layer}`"))?;
            if !amount.is_finite() || !(0.0..=1.0).contains(amount) {
                bail!("terrain_paint: amount fora de 0..1");
            }
            let terrain = doc.terrain_mut()?;
            // A janela vem da grelha de PINCEL (não da de alturas): o snapshot
            // cobre exatamente o que o pincel pode tocar.
            let rect = terrain
                .paint
                .window_around(at_v, radius.min(crate::terrain::delta::EDIT_MAX_RADIUS))
                .map(super::terrain::CellRect::from)
                .ok_or_else(|| anyhow!("terrain_paint: fora do mapa"))?;
            let before = terrain.paint_snapshot(rect);
            let touched = terrain
                .paint_circle(at_v, *radius, slot, *amount)
                .ok_or_else(|| anyhow!("terrain_paint: fora do mapa"))?;
            debug_assert_eq!(touched, rect);
            doc.revision += 1;
            Ok(Applied {
                forward: op.clone(),
                inverse: vec![EditOp::SplatPatch { rect, data: before }],
                changes: vec![Change::Paint { rect }],
            })
        }
        EditOp::TerrainBiomes {
            core,
            blend,
            wedges,
        } => {
            use crate::terrain::splat::{Biome, BiomeField, BiomeWedge};
            let terrain = doc.terrain_mut()?;
            let mut parsed = Vec::with_capacity(wedges.len());
            for (dx, dy, biome) in wedges {
                let biome = Biome::from_name(biome)
                    .ok_or_else(|| anyhow!("terrain_biomes: bioma desconhecido `{biome}`"))?;
                let dir = bevy::math::Vec2::new(*dx, *dy);
                let len = dir.length();
                if len < 1e-4 {
                    bail!("terrain_biomes: cunha com direção zero");
                }
                parsed.push(BiomeWedge {
                    dir: dir / len,
                    biome,
                });
            }
            let previous = terrain.meta.biomes.clone();
            terrain.meta.biomes = BiomeField {
                wedges: parsed,
                core: *core,
                blend: *blend,
            };
            terrain.dirty = true;
            doc.revision += 1;
            // inversa: reconstruir o campo anterior (nomes canónicos)
            let inv = EditOp::TerrainBiomes {
                core: previous.core,
                blend: previous.blend,
                wedges: previous
                    .wedges
                    .iter()
                    .map(|w| (w.dir.x, w.dir.y, w.biome.name().to_owned()))
                    .collect(),
            };
            Ok(Applied {
                forward: op.clone(),
                inverse: vec![inv],
                changes: vec![Change::Biomes],
            })
        }
        EditOp::SplatPatch { rect, data } => {
            let terrain = doc.terrain_mut()?;
            let before = terrain.paint_snapshot(*rect);
            terrain.paint_restore(*rect, data)?;
            doc.revision += 1;
            Ok(Applied {
                forward: op.clone(),
                inverse: vec![EditOp::SplatPatch {
                    rect: *rect,
                    data: before,
                }],
                changes: vec![Change::Paint { rect: *rect }],
            })
        }
        EditOp::Extract { node, to } => {
            let node = doc.resolve(node)?;
            extract(doc, &node, to.as_deref())
        }
        EditOp::CreateFile { path, xml, title } => {
            let children = parse_fragment(xml)?;
            let rel = path.trim().trim_start_matches('/').to_owned();
            let mut document = scene_document(children);
            if let Some(title) = title.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
                document.root_attrs.push(("title".into(), title.to_owned()));
            }
            doc.create_file(&rel, document)?;
            Ok(Applied {
                forward: EditOp::CreateFile {
                    path: rel.clone(),
                    xml: xml.clone(),
                    title: title.clone(),
                },
                inverse: vec![EditOp::DropFile { path: rel }],
                changes: Vec::new(),
            })
        }
        EditOp::DropFile { path } => {
            let rel = path.trim().trim_start_matches('/').to_owned();
            let content = doc.drop_file(&rel)?;
            Ok(Applied {
                forward: EditOp::DropFile { path: rel.clone() },
                inverse: vec![EditOp::CreateFile {
                    path: rel,
                    xml: fragment_xml(&content.children),
                    title: content
                        .root_attrs
                        .iter()
                        .find(|(k, _)| k == "title")
                        .map(|(_, v)| v.clone()),
                }],
                changes: Vec::new(),
            })
        }
        EditOp::Duplicate { node } => {
            let node = doc.resolve(node)?;
            let Some(parent) = node.parent() else {
                bail!("duplicate: o elemento raiz não se duplica");
            };
            let copy = doc.node(&node).expect("resolvido").duplicate();
            let index = node.path.last().copied().unwrap_or(0) + 1;
            let mut applied = insert_nodes(doc, &parent, Some(index), vec![copy])?;
            applied.forward = EditOp::Duplicate {
                node: doc.selector(&node),
            };
            Ok(applied)
        }
    }
}

/// Aplica um lote atomicamente: falhou uma, revertem-se as anteriores e devolve-se o erro.
pub fn apply_batch(doc: &mut SceneDoc, label: &str, ops: &[EditOp]) -> Result<Transaction> {
    let mut tx = Transaction {
        label: label.to_owned(),
        ..Transaction::default()
    };
    for (index, op) in ops.iter().enumerate() {
        match apply(doc, op) {
            Ok(applied) => {
                tx.forward.push(applied.forward);
                tx.inverse.splice(0..0, applied.inverse);
                tx.changes.extend(applied.changes);
            }
            Err(error) => {
                for inverse in &tx.inverse {
                    // As inversas de operações acabadas de aplicar não podem falhar; se
                    // falharem, o documento já está incoerente e o erro original é o útil.
                    let _ = apply(doc, inverse);
                }
                return Err(error.context(format!("operação #{index} ({})", op_name(op))));
            }
        }
    }
    Ok(tx)
}

/// Histórico de undo/redo de transações.
#[derive(Debug, Default, Clone, bevy::prelude::Resource)]
pub struct History {
    undo: Vec<Transaction>,
    redo: Vec<Transaction>,
}

impl History {
    /// Aplica um lote e regista-o (limpa o redo, como qualquer editor).
    pub fn apply(
        &mut self,
        doc: &mut SceneDoc,
        label: &str,
        ops: &[EditOp],
    ) -> Result<Transaction> {
        let tx = apply_batch(doc, label, ops)?;
        if !tx.forward.is_empty() {
            self.undo.push(tx.clone());
            self.redo.clear();
        }
        Ok(tx)
    }

    /// Como [`Self::apply`], mas um lote com a mesma `key` da última transação funde-se nela
    /// (traço de pincel: N pinceladas, um undo). Uma chave nova abre transação nova.
    pub fn apply_coalesced(
        &mut self,
        doc: &mut SceneDoc,
        label: &str,
        ops: &[EditOp],
        key: u64,
    ) -> Result<Transaction> {
        let mut tx = apply_batch(doc, label, ops)?;
        tx.coalesce = Some(key);
        if tx.forward.is_empty() {
            return Ok(tx);
        }
        self.redo.clear();
        match self.undo.last_mut() {
            Some(last) if last.coalesce == Some(key) => {
                last.forward.extend(tx.forward.iter().cloned());
                let mut inverse = tx.inverse.clone();
                inverse.append(&mut last.inverse);
                last.inverse = inverse;
                last.changes.extend(tx.changes.iter().cloned());
            }
            _ => self.undo.push(tx.clone()),
        }
        Ok(tx)
    }

    /// Desfaz a última transação; devolve a transação aplicada (as inversas) ou `None`.
    pub fn undo(&mut self, doc: &mut SceneDoc) -> Result<Option<Transaction>> {
        let Some(tx) = self.undo.pop() else {
            return Ok(None);
        };
        match apply_batch(doc, &format!("undo {}", tx.label), &tx.inverse) {
            Ok(applied) => {
                self.redo.push(tx);
                Ok(Some(applied))
            }
            Err(error) => {
                self.undo.push(tx);
                Err(error)
            }
        }
    }

    /// Refaz a última transação desfeita.
    pub fn redo(&mut self, doc: &mut SceneDoc) -> Result<Option<Transaction>> {
        let Some(tx) = self.redo.pop() else {
            return Ok(None);
        };
        match apply_batch(doc, &tx.label, &tx.forward) {
            Ok(applied) => {
                self.undo.push(applied.clone());
                Ok(Some(applied))
            }
            Err(error) => {
                self.redo.push(tx);
                Err(error)
            }
        }
    }

    pub fn can_undo(&self) -> bool {
        !self.undo.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.redo.is_empty()
    }

    /// As labels do histórico de undo (a mais recente última) — o painel de
    /// Histórico do editor. O índice de cada linha é quantos undos ela custa.
    pub fn undo_labels(&self) -> Vec<&str> {
        self.undo.iter().map(|tx| tx.label.as_str()).collect()
    }

    /// As labels do histórico de redo (a próxima primeira).
    pub fn redo_labels(&self) -> Vec<&str> {
        self.redo.iter().rev().map(|tx| tx.label.as_str()).collect()
    }

    /// Rótulo da próxima transação a desfazer.
    pub fn undo_label(&self) -> Option<&str> {
        self.undo.last().map(|tx| tx.label.as_str())
    }

    /// Esquece tudo (ex.: o documento foi recarregado do disco).
    pub fn clear(&mut self) {
        self.undo.clear();
        self.redo.clear();
    }
}

/// Lê um lote de operações em JSON: um array, ou um objeto `{"ops": [...]}`.
pub fn parse_ops(json: &str) -> Result<Vec<EditOp>> {
    let value: serde_json::Value = serde_json::from_str(json).context("ops: JSON inválido")?;
    let array = match value {
        serde_json::Value::Object(mut object) => object
            .remove("ops")
            .ok_or_else(|| anyhow!("ops: objeto sem campo `ops`"))?,
        other => other,
    };
    serde_json::from_value(array).context("ops: formato inválido (ver viber::scene::ops)")
}

pub fn op_name(op: &EditOp) -> &'static str {
    match op {
        EditOp::Set { .. } => "set",
        EditOp::ReplaceAttrs { .. } => "replace_attrs",
        EditOp::SetText { .. } => "set_text",
        EditOp::Insert { .. } => "insert",
        EditOp::Remove { .. } => "remove",
        EditOp::Move { .. } => "move",
        EditOp::Duplicate { .. } => "duplicate",
        EditOp::Extract { .. } => "extract",
        EditOp::CreateFile { .. } => "create_file",
        EditOp::DropFile { .. } => "drop_file",
        EditOp::TerrainBrush { .. } => "terrain_brush",
        EditOp::TerrainPatch { .. } => "terrain_patch",
        EditOp::TerrainPaint { .. } => "terrain_paint",
        EditOp::SplatPatch { .. } => "splat_patch",
        EditOp::TerrainBiomes { .. } => "terrain_biomes",
    }
}

// ---------------------------------------------------------------------------

/// Um `<Include>` como contentor é o raiz do ficheiro que ele carrega.
fn container(doc: &SceneDoc, node: NodeRef) -> NodeRef {
    match doc.include_target(&node) {
        Some(file) => NodeRef::root(file),
        None => node,
    }
}

fn set_attrs(
    doc: &mut SceneDoc,
    node: NodeRef,
    attrs: &BTreeMap<String, Option<String>>,
) -> Result<Applied> {
    if attrs.is_empty() {
        bail!("set: sem atributos");
    }
    if let Some(bad) = attrs.keys().find(|key| !valid_attr_name(key)) {
        bail!("set: nome de atributo inválido `{bad}`");
    }
    let target = attrs_of_mut(doc, &node);
    let previous = target.clone();
    for (key, value) in attrs {
        match value {
            Some(value) => match target.iter_mut().find(|(k, _)| k == key) {
                Some(slot) => slot.1 = value.clone(),
                None => target.push((key.clone(), value.clone())),
            },
            None => target.retain(|(k, _)| k != key),
        }
    }
    doc.touch(node.file);
    let selector = doc.selector(&node);
    Ok(Applied {
        forward: EditOp::Set {
            node: selector.clone(),
            attrs: attrs.clone(),
        },
        inverse: vec![EditOp::ReplaceAttrs {
            node: selector,
            attrs: previous,
        }],
        changes: vec![Change::Attrs {
            node,
            keys: attrs.keys().cloned().collect(),
        }],
    })
}

/// Os atributos de um nó (do raiz: os do elemento raiz do ficheiro). O nó tem de existir.
fn attrs_of_mut<'a>(doc: &'a mut SceneDoc, node: &NodeRef) -> &'a mut Vec<(String, String)> {
    if node.is_root() {
        &mut doc.files[node.file].doc.root_attrs
    } else {
        &mut doc.node_mut(node).expect("resolvido").attrs
    }
}

fn insert_nodes(
    doc: &mut SceneDoc,
    parent: &NodeRef,
    index: Option<usize>,
    nodes: Vec<XmlNode>,
) -> Result<Applied> {
    let count = nodes.len();
    // Para o redo: o XML exato que entrou.
    let xml = fragment_xml(&nodes);
    let children = doc
        .children_mut(parent)
        .ok_or_else(|| anyhow!("insert: contentor inexistente"))?;
    let at = index.unwrap_or(children.len());
    if at > children.len() {
        bail!(
            "insert: índice {at} fora do intervalo (0..={})",
            children.len()
        );
    }
    for (offset, node) in nodes.into_iter().enumerate() {
        children.insert(at + offset, node);
    }
    doc.touch(parent.file);
    // `link`: um `<Include>` novo de uma cena que o documento ainda não tem
    // (instanciar) carrega-a — fica editável e o sync spawna o conteúdo.
    doc.link_includes(parent.file);
    // Desfazer N irmãos inseridos em `at` = remover N vezes o índice `at`.
    let first = doc.selector(&parent.child(at));
    Ok(Applied {
        forward: EditOp::Insert {
            parent: doc.selector(parent),
            index: Some(at),
            xml,
        },
        inverse: vec![EditOp::Remove { node: first }; count],
        changes: (0..count)
            .map(|offset| Change::Inserted {
                node: parent.child(at + offset),
            })
            .collect(),
    })
}

fn remove_node(doc: &mut SceneDoc, node: &NodeRef) -> Result<Applied> {
    let Some(parent) = node.parent() else {
        bail!("remove: o elemento raiz de um ficheiro não se remove");
    };
    let index = *node.path.last().expect("não-raiz");
    let selector = doc.selector(node);
    let parent_selector = doc.selector(&parent);
    let removed = doc
        .children_mut(&parent)
        .ok_or_else(|| anyhow!("remove: pai inexistente"))?
        .remove(index);
    doc.touch(node.file);
    doc.reindex_includes(node.file);
    Ok(Applied {
        forward: EditOp::Remove { node: selector },
        inverse: vec![EditOp::Insert {
            parent: parent_selector,
            index: Some(index),
            xml: fragment_xml(std::slice::from_ref(&removed)),
        }],
        changes: vec![Change::Removed { node: node.clone() }],
    })
}

fn move_node(
    doc: &mut SceneDoc,
    node: &NodeRef,
    parent: &NodeRef,
    index: Option<usize>,
) -> Result<Applied> {
    let Some(old_parent) = node.parent() else {
        bail!("move: o elemento raiz de um ficheiro não se move");
    };
    if parent.file == node.file && parent.path.starts_with(&node.path) {
        bail!("move: um nó não pode ir para dentro de si próprio");
    }
    let old_index = *node.path.last().expect("não-raiz");
    let original_selector = doc.selector(node);
    // O redo reaplica a partir do estado ANTERIOR: o destino grava-se com o caminho de antes.
    let original_parent = doc.selector(parent);
    // Remover desloca os irmãos seguintes — o caminho do destino pode mudar.
    let target = shift_after_removal(parent, node);
    let taken = doc
        .children_mut(&old_parent)
        .ok_or_else(|| anyhow!("move: pai inexistente"))?
        .remove(old_index);
    let children = match doc.children_mut(&target) {
        Some(children) => children,
        None => {
            // Repor antes de falhar.
            doc.children_mut(&old_parent)
                .expect("acabou de existir")
                .insert(old_index, taken);
            bail!("move: destino inexistente");
        }
    };
    let at = index.unwrap_or(children.len()).min(children.len());
    children.insert(at, taken);
    doc.touch(node.file);
    doc.touch(target.file);
    doc.reindex_includes(node.file);
    doc.reindex_includes(target.file);
    let moved = target.child(at);
    // O pai antigo, visto DEPOIS da inserção no destino.
    let back_parent = shift_after_insert(&old_parent, &moved);
    let changes = vec![
        Change::Removed { node: node.clone() },
        Change::Inserted {
            node: moved.clone(),
        },
    ];
    Ok(Applied {
        forward: EditOp::Move {
            node: original_selector,
            parent: original_parent,
            index: Some(at),
        },
        inverse: vec![EditOp::Move {
            node: doc.selector(&moved),
            parent: doc.selector(&back_parent),
            index: Some(old_index),
        }],
        changes,
    })
}

/// Um ficheiro de cena com estes nós (`<world static="true">`, como os includes).
fn scene_document(children: Vec<XmlNode>) -> XmlDocument {
    XmlDocument {
        root_tag: "world".into(),
        root_attrs: vec![("static".into(), "true".into())],
        root_text: String::new(),
        children,
    }
}

/// Caminho por omissão de um ramo extraído: `scenes/<nome|tag>.xml`, com sufixo
/// `-2`, `-3`… se já existir (no disco ou no documento).
pub fn default_scene_path(doc: &SceneDoc, xml: &XmlNode) -> String {
    let base = xml.attr("name").unwrap_or(&xml.tag);
    let mut slug: String = base
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    slug = slug.trim_matches('-').to_owned();
    if slug.is_empty() {
        slug = "scene".into();
    }
    let taken = |rel: &str| doc.file_by_path(rel).is_some() || doc.root_dir.join(rel).exists();
    let mut rel = format!("scenes/{slug}.xml");
    let mut n = 2;
    while taken(&rel) {
        rel = format!("scenes/{slug}-{n}.xml");
        n += 1;
    }
    rel
}

/// `extract`: cria o ficheiro, troca o nó por `<Include>` com a pose dele.
fn extract(doc: &mut SceneDoc, node: &NodeRef, to: Option<&str>) -> Result<Applied> {
    let Some(parent) = node.parent() else {
        bail!("extract: o elemento raiz de um ficheiro não se extrai");
    };
    let original = doc.node(node).expect("resolvido").clone();
    if original.tag.eq_ignore_ascii_case("include") {
        bail!(
            "extract: o nó já é um <Include> (a cena é `{}`)",
            original.attr("src").unwrap_or("?")
        );
    }
    let rel = match to {
        Some(to) => to.trim().trim_start_matches('/').to_owned(),
        None => default_scene_path(doc, &original),
    };
    let selector = doc.selector(node);
    let index = *node.path.last().expect("não-raiz");
    // A pose fica na INSTÂNCIA (o `<Include>`); a cena guarda o ramo na origem.
    let pose_keys = crate::xml::include::INCLUDE_POSE_KEYS;
    let mut branch = original.clone();
    branch
        .attrs
        .retain(|(k, _)| !pose_keys.contains(&k.as_str()));
    let mut include = XmlNode {
        tag: "Include".into(),
        attrs: vec![("src".into(), format!("/{rel}"))],
        text: String::new(),
        children: Vec::new(),
        origin: None,
    };
    include.attrs.extend(
        original
            .attrs
            .iter()
            .filter(|(k, _)| pose_keys.contains(&k.as_str()))
            .cloned(),
    );
    let branch_xml = fragment_xml(std::slice::from_ref(&branch));
    let mut applied = apply(
        doc,
        &EditOp::CreateFile {
            path: rel.clone(),
            xml: branch_xml,
            title: None,
        },
    )?;
    let removed = remove_node(doc, node);
    let inserted = removed.and_then(|removed| {
        insert_nodes(doc, &parent, Some(index), vec![include]).map(|inserted| (removed, inserted))
    });
    let (removed, inserted) = match inserted {
        Ok(pair) => pair,
        Err(error) => {
            // Repor: o ficheiro e (se saiu) o nó.
            if doc.node(node).is_none_or(|n| *n != original) {
                let _ = insert_nodes(doc, &parent, Some(index), vec![original]);
            }
            let _ = doc.drop_file(&rel);
            return Err(error);
        }
    };
    let mut inverse = inserted.inverse;
    inverse.extend(removed.inverse);
    inverse.append(&mut applied.inverse);
    let mut changes = removed.changes;
    changes.extend(inserted.changes);
    Ok(Applied {
        forward: EditOp::Extract {
            node: selector,
            to: Some(rel),
        },
        inverse,
        changes,
    })
}

/// Caminho de `target` depois de `removed` sair do mesmo ficheiro.
fn shift_after_removal(target: &NodeRef, removed: &NodeRef) -> NodeRef {
    let mut out = target.clone();
    if target.file != removed.file {
        return out;
    }
    let depth = removed.path.len() - 1;
    if out.path.len() > depth
        && out.path[..depth] == removed.path[..depth]
        && out.path[depth] > removed.path[depth]
    {
        out.path[depth] -= 1;
    }
    out
}

/// Caminho de `target` depois de `inserted` entrar no mesmo ficheiro.
fn shift_after_insert(target: &NodeRef, inserted: &NodeRef) -> NodeRef {
    let mut out = target.clone();
    if target.file != inserted.file {
        return out;
    }
    let depth = inserted.path.len() - 1;
    if out.path.len() > depth
        && out.path[..depth] == inserted.path[..depth]
        && out.path[depth] >= inserted.path[depth]
    {
        out.path[depth] += 1;
    }
    out
}

/// Serializa elementos como fragmento XML (sem declaração), com o emissor do Viber.
pub(crate) fn fragment_xml(nodes: &[XmlNode]) -> String {
    let doc = XmlDocument {
        root_tag: "fragment".into(),
        root_attrs: Vec::new(),
        root_text: String::new(),
        children: nodes.to_vec(),
    };
    let text = emit_document(&doc);
    let start = text
        .find("<fragment>")
        .map(|i| i + "<fragment>".len())
        .unwrap_or(0);
    let end = text.rfind("</fragment>").unwrap_or(text.len());
    text[start..end].trim().to_owned()
}

fn valid_attr_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | ':' | '.'))
}
