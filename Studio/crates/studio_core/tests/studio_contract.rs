//! Gate de contrato: os YAML gerados pelo Studio têm de ser aceites pelo
//! `gameassets studio check` real.
//!
//! Corre quando `gameassets` está no PATH (ou `AIGAMEKIT_GAMEASSETS_BIN`);
//! caso contrário é um skip suave para não falhar em ambientes sem o venv.

use std::process::Command;

use studio_core::{check::CheckPayload, config::StudioConfig, project::StudioProject};

fn gameassets() -> Option<String> {
    if let Ok(bin) = std::env::var("AIGAMEKIT_GAMEASSETS_BIN")
        && !bin.trim().is_empty()
    {
        return Some(bin);
    }
    let ok = Command::new("gameassets")
        .arg("--version")
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false);
    ok.then(|| "gameassets".to_string())
}

#[test]
fn generated_project_is_accepted_by_studio_check() {
    let Some(bin) = gameassets() else {
        eprintln!("gameassets indisponível; gate saltado");
        return;
    };
    let tmp = tempfile::tempdir().expect("tmp");
    let config = StudioConfig::new("crate", "a small wooden crate with iron bands", "prop");
    let project = StudioProject::create(tmp.path().join("crate"), config).expect("criar projeto");

    let output = Command::new(&bin)
        .args([
            "studio",
            "check",
            "--project",
            project.dir.to_str().expect("utf8"),
            "--json",
        ])
        .output()
        .expect("correr gameassets");
    assert!(
        output.status.success(),
        "gameassets falhou: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload = CheckPayload::parse(&String::from_utf8_lossy(&output.stdout)).expect("payload");
    assert_eq!(payload.asset.id, "crate");
    assert_eq!(payload.asset.category, "prop");
    assert_eq!(payload.state, "need_image");
    assert_eq!(
        payload.asset.pipeline,
        vec!["3d", "paint", "lod", "collision"]
    );
    assert!(!payload.has_artifact("shape"));
    assert!(payload.artifact("image").is_some());
}

#[test]
fn generated_character_project_has_rig_and_animate() {
    let Some(bin) = gameassets() else {
        eprintln!("gameassets indisponível; gate saltado");
        return;
    };
    let tmp = tempfile::tempdir().expect("tmp");
    let config = StudioConfig::new("hero", "a young adventurer in A-pose", "humanoid");
    let project = StudioProject::create(tmp.path().join("hero"), config).expect("criar projeto");

    let output = Command::new(&bin)
        .args([
            "studio",
            "check",
            "--project",
            project.dir.to_str().expect("utf8"),
            "--json",
        ])
        .output()
        .expect("correr gameassets");
    assert!(
        output.status.success(),
        "gameassets falhou: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload = CheckPayload::parse(&String::from_utf8_lossy(&output.stdout)).expect("payload");
    assert_eq!(
        payload.asset.pipeline,
        vec!["3d", "paint", "rig", "animate", "lod", "collision"]
    );
    assert_eq!(payload.asset.category, "humanoid");
    assert!(!payload.prompt.text2d.is_empty());
}
