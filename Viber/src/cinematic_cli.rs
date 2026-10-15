//! `viber cinematic …` — cinemáticas para agentes (a mesma lógica do bridge
//! `viber.cinematic.*` e do Inspector do editor).
//!
//! ```bash
//! viber cinematic list                          # ids, duração, planos, seletor
//! viber cinematic show prologo                  # timeline completa (JSON)
//! viber cinematic play prologo --live           # toca no jogo a correr (versão do documento)
//! viber cinematic stop --live
//! viber cinematic shot prologo --live --duration 4   # câmara atual → novo <Shot> no fim
//! viber cinematic shot prologo --from "0 40 60" --look "0 38 0"   # offline, pose explícita
//! ```
//!
//! As cinemáticas são XML normal (`<Cinematic id>` com `<Shot>`/`<Line>`/…): editar
//! texto, tempos e poses é `viber scene set|insert|rm` como qualquer nó — o `shot`
//! só poupa a conta de transformar "o que a câmara vê agora" num plano.

use std::path::Path;

use anyhow::{Context, Result, anyhow, bail};
use bevy::math::Vec3;
use serde_json::{Value, json};

use crate::scene::doc::{NodeRef, SceneDoc};
use crate::scene::ops::{EditOp, apply_batch};

#[derive(Debug, clap::Subcommand)]
pub enum CinematicCommand {
    /// Lista as cinemáticas do mundo (id, duração, nº de planos/falas, seletor)
    List,
    /// A timeline de uma cinemática em JSON (tempos absolutos já resolvidos)
    Show { id: String },
    /// Toca uma cinemática no jogo a correr (só `--live`; usa a versão do documento)
    Play {
        id: String,
        /// Âncora `self` dos planos: o `name` de uma entidade
        #[arg(long)]
        anchor: Option<String>,
    },
    /// Pára a cinemática a tocar (só `--live`)
    Stop,
    /// Acrescenta um `<Shot>` a uma cinemática: com `--live`, a pose ATUAL da câmara;
    /// offline, `--from`/`--look` explícitos
    Shot {
        id: String,
        #[arg(long, default_value_t = 4.0)]
        duration: f32,
        /// Olho "x y z" (offline)
        #[arg(long, allow_hyphen_values = true)]
        from: Option<String>,
        /// Ponto olhado "x y z" (offline)
        #[arg(long, allow_hyphen_values = true)]
        look: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
}

/// Nós `<Cinematic>` do documento: (nó, id).
pub fn doc_cinematics(doc: &SceneDoc) -> Vec<(NodeRef, String)> {
    let mut out = Vec::new();
    doc.walk(|node, xml| {
        if xml.tag.eq_ignore_ascii_case("cinematic") {
            out.push((node.clone(), xml.attr("id").unwrap_or("").to_owned()));
        }
    });
    out
}

/// A cinemática `id` lida do documento (a versão EDITADA, não a do arranque).
pub fn doc_spec(doc: &SceneDoc, id: &str) -> Result<(NodeRef, crate::cinematic::CinematicSpec)> {
    let (node, _) = doc_cinematics(doc)
        .into_iter()
        .find(|(_, found)| found == id)
        .ok_or_else(|| anyhow!("cinemática `{id}` não existe no mundo (viber cinematic list)"))?;
    let xml = doc.node(&node).expect("encontrado acima");
    let spec = crate::cinematic::parse(xml).map_err(|e| anyhow!(e))?;
    Ok((node, spec))
}

/// `viber cinematic list` (offline e bridge).
pub fn list_json(doc: &SceneDoc) -> Value {
    let rows: Vec<Value> = doc_cinematics(doc)
        .into_iter()
        .map(|(node, id)| {
            let xml = doc.node(&node).expect("percorrido");
            match crate::cinematic::parse(xml) {
                Ok(spec) => json!({
                    "id": id,
                    "selector": doc.selector(&node),
                    "duration": spec.duration,
                    "shots": spec.shots.len(),
                    "lines": spec.lines.len(),
                    "titles": spec.titles.len(),
                }),
                Err(error) => json!({ "id": id, "selector": doc.selector(&node), "error": error }),
            }
        })
        .collect();
    json!(rows)
}

/// A op que acrescenta um plano no fim da cinemática.
pub fn shot_op(doc: &SceneDoc, id: &str, eye: Vec3, look: Vec3, duration: f32) -> Result<EditOp> {
    if !(duration.is_finite() && duration > 0.0) {
        bail!("shot: duration tem de ser > 0");
    }
    let (node, _) = doc_cinematics(doc)
        .into_iter()
        .find(|(_, found)| found == id)
        .ok_or_else(|| anyhow!("cinemática `{id}` não existe no mundo"))?;
    // Depois do último <Shot> existente (os planos correm por ordem).
    let xml = doc.node(&node).expect("encontrado");
    let index = xml
        .children
        .iter()
        .rposition(|c| c.tag.eq_ignore_ascii_case("shot"))
        .map(|i| i + 1);
    Ok(EditOp::Insert {
        parent: doc.selector(&node),
        index,
        xml: crate::cinematic::shot_xml(eye, look, duration),
    })
}

fn vec3(raw: &str, what: &str) -> Result<Vec3> {
    crate::xml::values::parse_vec3(raw, what).map(Vec3::from)
}

/// Cliente do bridge: (método, params) → resultado.
pub type LiveCall<'a> = &'a dyn Fn(&str, Value) -> Result<Value>;

/// Corre um subcomando. `live` = cliente do bridge da engine a correr.
pub fn run(world: &Path, command: CinematicCommand, live: Option<LiveCall<'_>>) -> Result<Value> {
    if let Some(call) = live {
        return match command {
            CinematicCommand::List => call("viber.cinematic.list", json!({})),
            CinematicCommand::Show { id } => call("viber.cinematic.show", json!({ "id": id })),
            CinematicCommand::Play { id, anchor } => call(
                "viber.cinematic.play",
                json!({ "id": id, "anchor": anchor }),
            ),
            CinematicCommand::Stop => call("viber.cinematic.stop", json!({})),
            CinematicCommand::Shot {
                id,
                duration,
                from,
                look,
                dry_run,
            } => {
                if dry_run {
                    bail!("--dry-run não existe com --live");
                }
                call(
                    "viber.cinematic.shot",
                    json!({ "id": id, "duration": duration, "from": from, "look": look }),
                )
            }
        };
    }
    let mut doc = SceneDoc::load(world)?;
    match command {
        CinematicCommand::List => Ok(list_json(&doc)),
        CinematicCommand::Show { id } => Ok(doc_spec(&doc, &id)?.1.to_json()),
        CinematicCommand::Play { .. } | CinematicCommand::Stop => {
            bail!("play/stop precisam da engine a correr — usa --live")
        }
        CinematicCommand::Shot {
            id,
            duration,
            from,
            look,
            dry_run,
        } => {
            let (Some(from), Some(look)) = (from, look) else {
                bail!("offline o plano precisa de --from e --look (com --live usa a câmara atual)")
            };
            let op = shot_op(
                &doc,
                &id,
                vec3(&from, "--from")?,
                vec3(&look, "--look")?,
                duration,
            )?;
            let tx = apply_batch(&mut doc, "cinematic shot", &[op])?;
            let files = doc.dirty_paths();
            if !dry_run {
                doc.save().context("gravar")?;
            }
            Ok(crate::scene::cli::transaction_json(
                &doc, &tx, &files, !dry_run,
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_show_and_shot_offline() {
        let tmp = tempfile::tempdir().unwrap();
        let world = tmp.path().join("world.xml");
        std::fs::write(
            &world,
            r#"<world static="true"><Cinematic id="intro"><Shot duration="2" from="0 5 5" look="0 0 0" /><Line at="0" duration="2" speaker="A">Olá</Line></Cinematic></world>"#,
        )
        .unwrap();
        let list = run(&world, CinematicCommand::List, None).unwrap();
        assert_eq!(list[0]["id"], "intro");
        assert_eq!(list[0]["shots"], 1);
        let show = run(&world, CinematicCommand::Show { id: "intro".into() }, None).unwrap();
        assert_eq!(show["lines"][0]["text"], "Olá");
        run(
            &world,
            CinematicCommand::Shot {
                id: "intro".into(),
                duration: 3.0,
                from: Some("1 2 3".into()),
                look: Some("0 0 0".into()),
                dry_run: false,
            },
            None,
        )
        .unwrap();
        let show = run(&world, CinematicCommand::Show { id: "intro".into() }, None).unwrap();
        assert_eq!(show["shots"].as_array().unwrap().len(), 2);
        assert_eq!(show["shots"][1]["start"], 2.0);
        assert!(run(&world, CinematicCommand::Show { id: "nope".into() }, None).is_err());
        assert!(run(&world, CinematicCommand::Stop, None).is_err());
    }
}
