//! `viber scene …` — edição offline de mundos para agentes (a mesma API do ViberEditor).
//!
//! Toda a saída é JSON. As mutações gravam os ficheiros alterados logo (`--dry-run` só
//! mostra o que mudaria). Exemplos:
//!
//! ```text
//! viber scene ls                              # topo do world.xml do cwd
//! viber scene --world w.xml ls '#2' --depth 2 # dentro do Include #2
//! viber scene find --tag Entity --attr name=well
//! viber scene get name=well
//! viber scene set name=well translation="4 0 3" --unset scale
//! viber scene insert 'world/village.xml#' '<Entity name="cart" />'
//! viber scene mv name=cart name=props --index 0
//! viber scene apply ops.json                  # lote atómico (ver viber::scene::ops)
//! viber scene --live set name=well translation="4 0 3"   # na engine a correr (bridge)
//! viber scene --live undo
//! viber scene --live save                     # grava os ficheiros alterados na engine
//! viber scene --live pick --x 400 --y 300     # px do ecrã → nó (câmara de editor)
//! viber scene --live entities 'name=props'    # entidades vivas + pose mundo
//! viber scene --live apply ops.json --coalesce k   # N applies com a mesma chave = 1 undo
//! ```
//!
//! Com `--live` o MESMO subcomando vai para a engine a correr (`viber.scene.*`): a edição
//! entra no histórico dela (undo/redo), é gravada e refletida no mundo vivo.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{Context, Result, bail};
use clap::Subcommand;
use serde_json::{Value, json};

use super::doc::{NodeRef, SceneDoc};
use super::ops::{Change, EditOp, Transaction, apply_batch, parse_ops};

#[derive(Debug, Subcommand)]
pub enum SceneCommand {
    /// Lista os filhos de um nó (omisso: topo do mundo) com seletores e atributos
    Ls {
        /// Seletor (`ficheiro#i/j`, `name=…`); um Include lista o ficheiro incluído
        selector: Option<String>,
        /// Níveis a descer
        #[arg(long, default_value_t = 1)]
        depth: usize,
    },
    /// Mostra um nó (atributos, texto, ficheiro, include)
    Get { selector: String },
    /// Procura nós por tag e/ou atributo em todos os ficheiros
    Find {
        /// Tag (sem distinguir maiúsculas)
        #[arg(long)]
        tag: Option<String>,
        /// `chave=valor`, ou só `chave` para "tem o atributo"
        #[arg(long)]
        attr: Option<String>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
    },
    /// Define atributos: `chave=valor` …; `--unset chave` remove
    Set {
        selector: String,
        /// Pares `chave=valor`
        pairs: Vec<String>,
        #[arg(long)]
        unset: Vec<String>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Insere XML (um ou mais elementos) como filhos de `parent`
    Insert {
        parent: String,
        xml: String,
        #[arg(long)]
        index: Option<usize>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Remove um nó e a subárvore
    Rm {
        selector: String,
        #[arg(long)]
        dry_run: bool,
    },
    /// Move um nó para debaixo de outro
    Mv {
        selector: String,
        parent: String,
        #[arg(long)]
        index: Option<usize>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Duplica um nó (a cópia fica logo a seguir)
    Dup {
        selector: String,
        #[arg(long)]
        dry_run: bool,
    },
    /// Desfaz a última edição (só `--live`: o histórico vive na engine)
    Undo,
    /// Refaz a última edição desfeita (só `--live`)
    Redo,
    /// Grava os ficheiros alterados do documento (offline: flush do que está sujo; com
    /// `--live`: o estado vivo da engine — o mesmo `viber.scene.save` da bridge)
    Save,
    /// Píxeis do ecrã → nó do documento (raycast pela câmara de editor; só `--live`)
    Pick {
        /// Coordenada X na janela (px lógicos)
        #[arg(long)]
        x: f32,
        /// Coordenada Y na janela (px lógicos)
        #[arg(long)]
        y: f32,
        /// Alcance máximo do raycast em metros (omisso: 2000)
        #[arg(long)]
        max_distance: Option<f32>,
    },
    /// Entidades vivas de um nó (pose local + mundo; sem seletor: todas as do documento)
    Entities {
        /// Seletor do nó origem (`name=…`, `ficheiro#i/j`); omisso = todas
        selector: Option<String>,
    },
    /// Aplica um lote JSON de operações, atomicamente (`-` lê do stdin)
    Apply {
        file: String,
        #[arg(long)]
        dry_run: bool,
        /// Funda este lote com o anterior que tenha a mesma chave (só `--live`):
        /// N applies com a mesma chave = 1 undo (um traço de pincel)
        #[arg(long)]
        coalesce: Option<u64>,
    },
    /// O catálogo Add Node: tags com snippets válidos (o mesmo diálogo do editor)
    Catalog,
    /// Props exportados de um script (`--@export`): o esquema tipado e, num nó, os valores
    /// efetivos da instância (`prop-<nome>`). Alvo: seletor de nó com `script=` ou `x.lua`
    Schema { target: String },
    /// Lint estático do mundo: sintaxe Luau de TODOS os scripts, alvos
    /// `viber.load("…")`, referências das quests (`npc` ↔ DialogueNPC,
    /// `visit` ↔ Landmark) e nomes de entidade duplicados. Sem engine.
    /// `--strict` devolve exit non-zero se houver erros (relatório sai na
    /// mesma em stdout).
    Lint {
        /// Falhar quando houver findings de severidade error
        #[arg(long)]
        strict: bool,
    },
    /// Insere o snippet do catálogo de uma tag (o "Add Node" do editor)
    Add {
        tag: String,
        /// Contentor (omisso: raiz do mundo)
        #[arg(long, default_value = "#")]
        parent: String,
        #[arg(long)]
        index: Option<usize>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Larga um `.glb` no mundo: `Entity` + `GltfScene` na posição MUNDO `--at`, convertida
    /// para o referencial de `--parent` (o drag-drop do editor)
    Drop {
        glb: String,
        /// Posição mundo: "x y z"
        #[arg(long, allow_hyphen_values = true)]
        at: String,
        #[arg(long, default_value = "#")]
        parent: String,
        #[arg(long)]
        dry_run: bool,
    },
    /// "Salvar ramo como cena": o nó (e a subárvore) passa para um ficheiro novo e fica no
    /// lugar um `<Include>` com a pose dele (desfazível: o `inverse` repõe tudo)
    Extract {
        selector: String,
        /// Ficheiro da cena, relativo à pasta do mundo (omisso: `scenes/<nome>.xml`)
        #[arg(long)]
        to: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Instancia uma cena (`.xml` da pasta do mundo) como `<Include>` — o "Instance Child
    /// Scene" do Godot. Várias instâncias partilham o ficheiro: editá-lo muda todas
    Instance {
        scene: String,
        /// Contentor (omisso: raiz do mundo)
        #[arg(long, default_value = "#")]
        parent: String,
        /// Posição MUNDO "x y z" (convertida para o referencial do pai)
        #[arg(long, allow_hyphen_values = true)]
        at: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
    /// Grupos (`groups="…"`) do mundo e os seus membros — os alvos `@grupo` dos sinais
    Groups {
        /// Só este grupo
        name: Option<String>,
    },
    /// Lista a pasta do mundo em árvore (world/scene/lua/glb/… — o dock FileSystem do editor)
    Files {
        /// Profundidade máxima (0 = só o topo)
        #[arg(long, default_value_t = 6)]
        depth: usize,
    },
}

/// O que um subcomando pede: uma consulta ou um lote de operações.
enum Request {
    Ls {
        selector: Option<String>,
        depth: usize,
    },
    Get {
        selector: String,
    },
    Find {
        tag: Option<String>,
        attr: Option<String>,
        limit: usize,
    },
    Ops {
        ops: Vec<EditOp>,
        dry_run: bool,
        coalesce: Option<u64>,
    },
    Undo,
    Redo,
    Save,
    Pick {
        x: f32,
        y: f32,
        max_distance: Option<f32>,
    },
    Entities {
        selector: Option<String>,
    },
    Files {
        depth: usize,
    },
    Catalog,
    Schema {
        target: String,
    },
    Groups {
        name: Option<String>,
    },
    Instance {
        scene: String,
        parent: String,
        at: Option<[f32; 3]>,
        dry_run: bool,
    },
    Drop {
        glb: String,
        at: [f32; 3],
        parent: String,
        dry_run: bool,
    },
}

fn request(command: SceneCommand) -> Result<Request> {
    Ok(match command {
        SceneCommand::Ls { selector, depth } => Request::Ls {
            selector,
            depth: depth.max(1),
        },
        SceneCommand::Get { selector } => Request::Get { selector },
        SceneCommand::Find { tag, attr, limit } => Request::Find { tag, attr, limit },
        SceneCommand::Set {
            selector,
            pairs,
            unset,
            dry_run,
        } => {
            let mut attrs = BTreeMap::new();
            for pair in pairs {
                let Some((key, value)) = pair.split_once('=') else {
                    bail!("set: `{pair}` não é `chave=valor`");
                };
                attrs.insert(key.trim().to_owned(), Some(value.to_owned()));
            }
            for key in unset {
                attrs.insert(key, None);
            }
            Request::Ops {
                ops: vec![EditOp::Set {
                    node: selector,
                    attrs,
                }],
                dry_run,
                coalesce: None,
            }
        }
        SceneCommand::Insert {
            parent,
            xml,
            index,
            dry_run,
        } => Request::Ops {
            ops: vec![EditOp::Insert { parent, index, xml }],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Rm { selector, dry_run } => Request::Ops {
            ops: vec![EditOp::Remove { node: selector }],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Mv {
            selector,
            parent,
            index,
            dry_run,
        } => Request::Ops {
            ops: vec![EditOp::Move {
                node: selector,
                parent,
                index,
            }],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Dup { selector, dry_run } => Request::Ops {
            ops: vec![EditOp::Duplicate { node: selector }],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Apply {
            file,
            dry_run,
            coalesce,
        } => {
            let text = if file == "-" {
                std::io::read_to_string(std::io::stdin()).context("lendo ops do stdin")?
            } else {
                std::fs::read_to_string(&file).with_context(|| format!("lendo {file}"))?
            };
            Request::Ops {
                ops: parse_ops(&text)?,
                dry_run,
                coalesce,
            }
        }
        SceneCommand::Undo => Request::Undo,
        SceneCommand::Redo => Request::Redo,
        SceneCommand::Save => Request::Save,
        SceneCommand::Pick { x, y, max_distance } => Request::Pick { x, y, max_distance },
        SceneCommand::Entities { selector } => Request::Entities { selector },
        SceneCommand::Files { depth } => Request::Files { depth },
        SceneCommand::Catalog => Request::Catalog,
        SceneCommand::Schema { target } => Request::Schema { target },
        SceneCommand::Groups { name } => Request::Groups { name },
        SceneCommand::Extract {
            selector,
            to,
            dry_run,
        } => Request::Ops {
            ops: vec![EditOp::Extract { node: selector, to }],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Instance {
            scene,
            parent,
            at,
            dry_run,
        } => Request::Instance {
            scene,
            parent,
            at: at
                .map(|at| crate::xml::values::parse_vec3(&at, "--at"))
                .transpose()?,
            dry_run,
        },
        // `run` trata o lint ANTES de carregar o documento; `live_request`
        // encaminha para a bridge. Nunca chega aqui.
        SceneCommand::Lint { .. } => bail!("lint não passa pelo documento de edição"),
        SceneCommand::Add {
            tag,
            parent,
            index,
            dry_run,
        } => Request::Ops {
            ops: vec![super::catalog::add_node(&parent, &tag, index)],
            dry_run,
            coalesce: None,
        },
        SceneCommand::Drop {
            glb,
            at,
            parent,
            dry_run,
        } => Request::Drop {
            glb,
            at: crate::xml::values::parse_vec3(&at, "--at")?,
            parent,
            dry_run,
        },
    })
}

/// Corre um subcomando contra os ficheiros de `world` (offline) e devolve o JSON a imprimir.
pub fn run(world: &Path, command: SceneCommand) -> Result<Value> {
    if let SceneCommand::Lint { strict } = command {
        return crate::lint::run_cli(world, strict);
    }
    let mut doc = SceneDoc::load(world)?;
    let (ops, dry_run) = match request(command)? {
        Request::Ls { selector, depth } => {
            let node = match selector {
                Some(selector) => doc.resolve(&selector)?,
                None => NodeRef::root(0),
            };
            return Ok(super::list(&doc, &node, depth));
        }
        Request::Get { selector } => return Ok(super::describe(&doc, &doc.resolve(&selector)?)),
        Request::Find { tag, attr, limit } => {
            let attr = attr
                .as_deref()
                .map(|attr| attr.split_once('=').unwrap_or((attr, "")));
            return Ok(super::find(&doc, tag.as_deref(), attr, limit));
        }
        Request::Files { depth } => {
            let dir = world.parent().unwrap_or(Path::new("."));
            return Ok(super::files::to_json(&super::files::scan(dir, depth)));
        }
        Request::Undo | Request::Redo => {
            bail!(
                "undo/redo só existem com --live: offline cada comando grava logo (desfaz com o `inverse` que ele devolveu, ou com o git)"
            )
        }
        Request::Save => {
            // Offline os subcomandos gravam logo: o flush só tem trabalho se o
            // documento veio sujo do disco (o caminho útil é o `--live`).
            let files = doc.dirty_paths();
            let written = doc.save()?;
            return Ok(json!({ "files": files, "written": written }));
        }
        Request::Pick { .. } => {
            bail!("pick precisa da engine viva — usa --live (o raycast é pela câmara da janela)")
        }
        Request::Entities { .. } => {
            bail!(
                "entities precisa da engine viva — usa --live (só a engine tem as entidades com pose)"
            )
        }
        Request::Catalog => return Ok(super::catalog::to_json()),
        Request::Schema { target } => return super::script_schema(&doc, &target),
        Request::Groups { name } => return Ok(super::groups(&doc, name.as_deref())),
        Request::Instance {
            scene,
            parent,
            at,
            dry_run,
        } => (
            vec![super::catalog::scene_instance(&doc, &parent, &scene, at)?],
            dry_run,
        ),
        Request::Drop {
            glb,
            at,
            parent,
            dry_run,
        } => (
            vec![super::catalog::glb_drop(&doc, &parent, &glb, at)?],
            dry_run,
        ),
        Request::Ops {
            coalesce: Some(_), ..
        } => {
            bail!(
                "--coalesce só existe com --live: offline não há histórico a fundir (cada comando grava logo)"
            )
        }
        Request::Ops {
            ops,
            dry_run,
            coalesce: None,
        } => (ops, dry_run),
    };
    let tx = apply_batch(&mut doc, "cli", &ops)?;
    let dirty: Vec<String> = doc.dirty_paths();
    if !dry_run {
        doc.save()?;
    }
    Ok(transaction_json(&doc, &tx, &dirty, !dry_run))
}

/// O mesmo subcomando contra a engine a correr: o método `viber.scene.*` e os params.
pub fn live_request(command: SceneCommand) -> Result<(&'static str, Value)> {
    // O lint não passa pelo documento — contra a engine, o método lê os
    // MESMOS ficheiros do disco (a engine tem o root_dir do mundo).
    if let SceneCommand::Lint { strict } = command {
        return Ok(("viber.scene.lint", json!({ "strict": strict })));
    }
    Ok(match request(command)? {
        Request::Ls { selector, depth } => (
            "viber.scene.ls",
            json!({ "selector": selector, "depth": depth }),
        ),
        Request::Get { selector } => ("viber.scene.get", json!({ "selector": selector })),
        Request::Find { tag, attr, limit } => (
            "viber.scene.find",
            json!({ "tag": tag, "attr": attr, "limit": limit }),
        ),
        Request::Ops { dry_run: true, .. } => {
            bail!("--dry-run não existe com --live (a engine aplicaria a edição); corre sem --live")
        }
        Request::Ops { ops, coalesce, .. } => (
            "viber.scene.apply",
            json!({ "ops": ops, "label": "cli", "coalesce": coalesce }),
        ),
        Request::Undo => ("viber.scene.undo", json!({})),
        Request::Redo => ("viber.scene.redo", json!({})),
        Request::Save => ("viber.scene.save", json!({})),
        Request::Pick { x, y, max_distance } => (
            "viber.scene.pick",
            json!({ "x": x, "y": y, "max_distance": max_distance }),
        ),
        Request::Entities { selector } => ("viber.scene.entities", json!({ "selector": selector })),
        Request::Files { depth } => ("viber.scene.files", json!({ "depth": depth })),
        Request::Catalog => ("viber.scene.catalog", json!({})),
        Request::Schema { target } => ("viber.scene.schema", json!({ "target": target })),
        Request::Groups { name } => ("viber.scene.groups", json!({ "name": name })),
        Request::Instance { dry_run: true, .. } => {
            bail!("--dry-run não existe com --live (a engine aplicaria a edição); corre sem --live")
        }
        Request::Instance {
            scene, parent, at, ..
        } => (
            "viber.scene.instance",
            json!({ "scene": scene, "parent": parent, "at": at }),
        ),
        Request::Drop { dry_run: true, .. } => {
            bail!("--dry-run não existe com --live (a engine aplicaria a edição); corre sem --live")
        }
        Request::Drop {
            glb, at, parent, ..
        } => (
            "viber.scene.drop",
            json!({ "glb": glb, "at": at, "parent": parent }),
        ),
    })
}

/// Relatório de uma transação: o que mudou, os ficheiros e as ops inversas (o undo de um
/// agente é aplicar `inverse`).
pub fn transaction_json(doc: &SceneDoc, tx: &Transaction, files: &[String], saved: bool) -> Value {
    let changes: Vec<Value> = tx
        .changes
        .iter()
        .map(|change| match change {
            Change::Attrs { node, keys } => {
                json!({ "kind": "attrs", "node": doc.selector(node), "keys": keys })
            }
            Change::Inserted { node } => json!({ "kind": "inserted", "node": doc.selector(node) }),
            Change::Removed { node } => json!({ "kind": "removed", "node": doc.selector(node) }),
            Change::Terrain { rect } => json!({ "kind": "terrain", "cells": rect }),
            Change::Paint { rect } => json!({ "kind": "paint", "cells": rect }),
            Change::Biomes => json!({ "kind": "biomes" }),
        })
        .collect();
    json!({
        "applied": tx.forward.len(),
        "changes": changes,
        "files": files,
        "saved": saved,
        "inverse": tx.inverse,
    })
}
