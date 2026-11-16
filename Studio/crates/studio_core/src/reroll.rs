//! Invalidação e re-roll por etapa.
//!
//! Regras do contrato:
//! - `invalidate --from X` preserva o artefacto de X e apaga os derivados.
//! - com `--include-stage` apaga também o artefacto de X (o re-roll explícito).
//! - `image`/`all` é reset total.
//!
//! Antes de apagar, o Studio copia os artefactos da etapa para
//! `.studio/history/<stamp>-<stage>/` (comparação A/B).

use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::Deserialize;

use crate::{
    check::CheckPayload,
    cli::{CliConfig, run_capture},
    project::{ProjectError, StudioProject},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Stage {
    Image,
    Shape,
    Clean,
    Refine,
    ToPaint,
    Paint,
    Rig,
    Animate,
    Lod,
    Collision,
}

impl Stage {
    pub const DAG: [Self; 10] = [
        Self::Image,
        Self::Shape,
        Self::Clean,
        Self::Refine,
        Self::ToPaint,
        Self::Paint,
        Self::Rig,
        Self::Animate,
        Self::Lod,
        Self::Collision,
    ];

    pub fn key(self) -> &'static str {
        match self {
            Self::Image => "image",
            Self::Shape => "shape",
            Self::Clean => "clean",
            Self::Refine => "refine",
            Self::ToPaint => "to_paint",
            Self::Paint => "paint",
            Self::Rig => "rig",
            Self::Animate => "animate",
            Self::Lod => "lod",
            Self::Collision => "collision",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Image => "Referência",
            Self::Shape => "Shape",
            Self::Clean => "Topologia",
            Self::Refine => "Hi3D",
            Self::ToPaint => "Orçamento",
            Self::Paint => "Pintura",
            Self::Rig => "Rig",
            Self::Animate => "Animação",
            Self::Lod => "LODs",
            Self::Collision => "Colisão",
        }
    }

    /// Chaves de artefacto no payload de `studio check`.
    pub fn artifact_keys(self) -> &'static [&'static str] {
        match self {
            Self::Image => &["image"],
            Self::Shape => &["shape"],
            Self::Clean => &["clean"],
            Self::Refine => &["refined"],
            Self::ToPaint => &["to_paint"],
            Self::Paint => &["painted"],
            Self::Rig => &["rigged"],
            Self::Animate => &["animated"],
            Self::Lod => &["lod0", "lod1", "lod2"],
            Self::Collision => &["collision", "precompute"],
        }
    }

    /// Sidecars/caches no payload de `studio check` que morrem com a etapa.
    pub fn sidecar_keys(self) -> &'static [&'static str] {
        match self {
            Self::Shape => &["shape_omni"],
            Self::Clean => &["clean_omni"],
            Self::Refine => &["refined_multiview", "views"],
            _ => &[],
        }
    }

    pub fn from_key(key: &str) -> Option<Self> {
        Self::DAG.into_iter().find(|stage| stage.key() == key)
    }

    /// Mapeia `state` / `need_*` do GameAssets para a etapa correspondente.
    pub fn from_check_state(state: &str) -> Option<Self> {
        match state {
            "need_image" => Some(Self::Image),
            "need_shape" => Some(Self::Shape),
            "need_topology_fix" => Some(Self::Clean),
            "need_paint" => Some(Self::Paint),
            "need_rig" => Some(Self::Rig),
            "need_animate" => Some(Self::Animate),
            "need_bake_master" | "need_lod_gen" => Some(Self::Lod),
            "need_collision" => Some(Self::Collision),
            _ => None,
        }
    }

    pub fn is_end(self) -> bool {
        matches!(self, Self::Lod | Self::Collision)
    }
}

#[derive(Debug, Clone, Default)]
pub struct InvalidateOutcome {
    pub deleted: Vec<PathBuf>,
    pub failed: Vec<(PathBuf, String)>,
    pub used_fallback: bool,
}

#[derive(Debug, Clone, Default)]
pub struct RerollOutcome {
    pub history: Vec<PathBuf>,
    pub invalidate: InvalidateOutcome,
}

#[derive(Debug, thiserror::Error)]
pub enum RerollError {
    #[error("io em {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("gameassets falhou: {0}")]
    Cli(String),
    #[error("payload inválido: {0}")]
    Json(String),
    #[error(transparent)]
    Project(#[from] ProjectError),
}

#[derive(Debug, Deserialize, Default)]
struct InvalidatePayload {
    #[serde(default)]
    deleted: Vec<String>,
    #[serde(default)]
    failed: Vec<FailedEntry>,
}

#[derive(Debug, Deserialize)]
struct FailedEntry {
    path: String,
    #[serde(default)]
    error: String,
}

fn parse_payload(text: &str) -> Result<InvalidatePayload, RerollError> {
    let line = crate::cli::last_json_line(text)
        .ok_or_else(|| RerollError::Json(format!("sem JSON na saída: {text}")))?;
    serde_json::from_str(line).map_err(|err| RerollError::Json(err.to_string()))
}

fn unknown_option(capture: &crate::cli::Capture) -> bool {
    let blob = format!("{}\n{}", capture.stdout, capture.stderr).to_lowercase();
    [
        "no such option",
        "unknown option",
        "unexpected argument",
        "unexpected keyword",
        "got an unexpected",
    ]
    .iter()
    .any(|needle| blob.contains(needle))
}

/// Apaga os artefactos da própria etapa (fallback quando o `gameassets`
/// instalado ainda não tem `--include-stage`).
fn delete_own_artifacts(
    project: &StudioProject,
    check: &CheckPayload,
    stage: Stage,
) -> Result<Vec<PathBuf>, RerollError> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    for key in stage.artifact_keys().iter().chain(stage.sidecar_keys()) {
        if let Some(artifact) = check.artifact(key) {
            if !artifact.path.is_empty() {
                candidates.push(PathBuf::from(&artifact.path));
            }
        }
    }
    candidates.extend(project.stage_artifact_paths(stage));
    let mut deleted = Vec::new();
    for path in candidates {
        if path.is_file() {
            fs::remove_file(&path).map_err(|err| RerollError::Io {
                path: path.clone(),
                source: err,
            })?;
            deleted.push(path);
        }
    }
    deleted.sort();
    deleted.dedup();
    Ok(deleted)
}

pub fn invalidate(
    project: &StudioProject,
    check: &CheckPayload,
    stage: Stage,
    include_stage: bool,
    cli: &CliConfig,
) -> Result<InvalidateOutcome, RerollError> {
    let spec = cli.studio_invalidate(project, stage, include_stage);
    let capture = run_capture(&spec).map_err(|err| RerollError::Io {
        path: project.dir.clone(),
        source: err,
    })?;
    if capture.success {
        let payload = parse_payload(&capture.stdout)?;
        return Ok(InvalidateOutcome {
            deleted: payload.deleted.into_iter().map(PathBuf::from).collect(),
            failed: payload
                .failed
                .into_iter()
                .map(|entry| (PathBuf::from(entry.path), entry.error))
                .collect(),
            used_fallback: false,
        });
    }

    if include_stage && unknown_option(&capture) {
        let deleted = delete_own_artifacts(project, check, stage)?;
        let spec = cli.studio_invalidate(project, stage, false);
        let capture = run_capture(&spec).map_err(|err| RerollError::Io {
            path: project.dir.clone(),
            source: err,
        })?;
        if !capture.success {
            return Err(RerollError::Cli(capture.message()));
        }
        let payload = parse_payload(&capture.stdout)?;
        let mut all_deleted = deleted;
        all_deleted.extend(payload.deleted.into_iter().map(PathBuf::from));
        all_deleted.sort();
        all_deleted.dedup();
        return Ok(InvalidateOutcome {
            deleted: all_deleted,
            failed: payload
                .failed
                .into_iter()
                .map(|entry| (PathBuf::from(entry.path), entry.error))
                .collect(),
            used_fallback: true,
        });
    }

    Err(RerollError::Cli(capture.message()))
}

pub fn copy_history(
    project: &StudioProject,
    check: &CheckPayload,
    stage: Stage,
) -> Result<Vec<PathBuf>, RerollError> {
    let mut sources: Vec<PathBuf> = Vec::new();
    for key in stage.artifact_keys() {
        if let Some(artifact) = check.artifact(key) {
            if artifact.exists && !artifact.path.is_empty() {
                sources.push(PathBuf::from(&artifact.path));
            }
        }
    }
    if sources.is_empty() {
        sources.extend(
            project
                .stage_artifact_paths(stage)
                .into_iter()
                .filter(|path| path.is_file()),
        );
    }
    sources.sort();
    sources.dedup();
    if sources.is_empty() {
        return Ok(Vec::new());
    }
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
    let dir = project
        .history_dir()
        .join(format!("{stamp}-{}", stage.key()));
    fs::create_dir_all(&dir).map_err(|err| RerollError::Io {
        path: dir.clone(),
        source: err,
    })?;
    let mut copied = Vec::new();
    for source in sources {
        let Some(name) = source.file_name() else {
            continue;
        };
        let dest = dir.join(name);
        fs::copy(&source, &dest).map_err(|err| RerollError::Io {
            path: dest.clone(),
            source: err,
        })?;
        copied.push(dest);
    }
    Ok(copied)
}

/// Re-roll de uma etapa: histórico + invalidação (incluindo a própria etapa).
pub fn reroll(
    project: &StudioProject,
    check: &CheckPayload,
    stage: Stage,
    cli: &CliConfig,
) -> Result<RerollOutcome, RerollError> {
    let history = copy_history(project, check, stage)?;
    let invalidate = invalidate(project, check, stage, true, cli)?;
    Ok(RerollOutcome {
        history,
        invalidate,
    })
}

/// Reset completo (imagem + tudo o que deriva dela).
pub fn reset(
    project: &StudioProject,
    check: &CheckPayload,
    cli: &CliConfig,
) -> Result<RerollOutcome, RerollError> {
    let history = copy_history(project, check, Stage::Image)?;
    let invalidate = invalidate(project, check, Stage::Image, true, cli)?;
    Ok(RerollOutcome {
        history,
        invalidate,
    })
}

#[derive(Debug, Clone)]
pub struct HistoryEntry {
    pub dir: PathBuf,
    pub files: Vec<PathBuf>,
}

pub fn list_history(project: &StudioProject, stage: Stage) -> Vec<HistoryEntry> {
    let Ok(read_dir) = fs::read_dir(project.history_dir()) else {
        return Vec::new();
    };
    let suffix = format!("-{}", stage.key());
    let mut entries = Vec::new();
    for entry in read_dir.flatten() {
        let dir = entry.path();
        if !dir.is_dir()
            || !dir
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.ends_with(&suffix))
        {
            continue;
        }
        let Ok(files) = fs::read_dir(&dir) else {
            continue;
        };
        let files: Vec<PathBuf> = files
            .flatten()
            .map(|f| f.path())
            .filter(|path| path.is_file())
            .collect();
        if !files.is_empty() {
            entries.push(HistoryEntry { dir, files });
        }
    }
    entries.sort_by(|a, b| b.dir.cmp(&a.dir));
    entries
}

/// Caminho de preview para comparar um artefacto histórico.
pub fn history_file<'a>(entry: &'a HistoryEntry, artifact_key: &str) -> Option<&'a Path> {
    let needle = match artifact_key {
        "lod0" => "_lod0",
        "lod1" => "_lod1",
        "lod2" => "_lod2",
        "collision" => "_collision",
        "animated" => "_rigged_animated",
        "rigged" => "_rigged",
        "painted" => "_painted",
        "to_paint" => "_to_paint",
        "refined" => "_refined",
        "clean" => "_clean",
        "shape" => "_shape",
        _ => "",
    };
    entry
        .files
        .iter()
        .find(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.contains(needle))
        })
        .map(PathBuf::as_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stage_keys_match_the_cli_contract() {
        assert_eq!(Stage::ToPaint.key(), "to_paint");
        assert_eq!(Stage::from_key("to_paint"), Some(Stage::ToPaint));
        assert_eq!(Stage::from_check_state("need_lod_gen"), Some(Stage::Lod));
        assert_eq!(Stage::from_check_state("done"), None);
    }

    #[test]
    fn history_file_matches_artifact_names() {
        let entry = HistoryEntry {
            dir: PathBuf::from("/tmp/h"),
            files: vec![
                PathBuf::from("/tmp/h/hero_painted.glb"),
                PathBuf::from("/tmp/h/hero_rigged_animated.glb"),
            ],
        };
        assert_eq!(
            history_file(&entry, "animated"),
            Some(Path::new("/tmp/h/hero_rigged_animated.glb"))
        );
        assert_eq!(
            history_file(&entry, "painted"),
            Some(Path::new("/tmp/h/hero_painted.glb"))
        );
    }

    #[test]
    fn unknown_option_detects_click_error() {
        let capture = crate::cli::Capture {
            status: Some(2),
            success: false,
            stdout: String::new(),
            stderr: "Error: No such option: --include-stage".into(),
        };
        assert!(unknown_option(&capture));
    }
}
