//! Projeto Studio em disco.
//!
//! Layout (relativo à pasta do projeto):
//!
//! ```text
//! game.yaml / manifest.yaml   gerados a partir de studio.toml
//! studio.toml                 fonte de verdade
//! images/ meshes/ motions/ logs/ .studio/
//! ```

use std::{
    fs,
    path::{Path, PathBuf},
    time::SystemTime,
};

use unicode_normalization::UnicodeNormalization;

use crate::{
    config::StudioConfig,
    manifest::{build_game_yaml, build_manifest_yaml},
};

pub const STUDIO_FILE: &str = "studio.toml";
pub const GAME_FILE: &str = "game.yaml";
pub const MANIFEST_FILE: &str = "manifest.yaml";

#[derive(Debug, thiserror::Error)]
pub enum ProjectError {
    #[error("pasta de projeto inválida: {0}")]
    InvalidDir(PathBuf),
    #[error("studio.toml ausente em {0} (projeto externo? adota-o primeiro)")]
    NotAStudioProject(PathBuf),
    #[error("config inválida:\n{0}")]
    InvalidConfig(String),
    #[error("io em {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("yaml: {0}")]
    Yaml(#[from] serde_yaml::Error),
}

fn io(path: &Path, source: std::io::Error) -> ProjectError {
    ProjectError::Io {
        path: path.to_path_buf(),
        source,
    }
}

#[derive(Debug, Clone)]
pub struct StudioProject {
    pub dir: PathBuf,
    pub config: StudioConfig,
}

#[derive(Debug, Clone)]
pub struct ProjectSummary {
    pub dir: PathBuf,
    pub id: String,
    pub idea: String,
    pub category: String,
    pub modified: Option<SystemTime>,
}

impl StudioProject {
    /// Cria a pasta e os três ficheiros de projeto. Falha se já existir um
    /// `studio.toml` (usar [`Self::open`]).
    pub fn create(dir: impl Into<PathBuf>, config: StudioConfig) -> Result<Self, ProjectError> {
        let dir = dir.into();
        if dir.join(STUDIO_FILE).is_file() {
            return Err(ProjectError::InvalidConfig(format!(
                "já existe um projeto em {}",
                dir.display()
            )));
        }
        let project = Self { dir, config };
        project.ensure_dirs()?;
        project.save_config()?;
        Ok(project)
    }

    pub fn open(dir: impl Into<PathBuf>) -> Result<Self, ProjectError> {
        let dir = dir.into();
        if !dir.is_dir() {
            return Err(ProjectError::InvalidDir(dir));
        }
        let path = dir.join(STUDIO_FILE);
        if !path.is_file() {
            return Err(ProjectError::NotAStudioProject(dir));
        }
        let text = fs::read_to_string(&path).map_err(|err| io(&path, err))?;
        let config = StudioConfig::from_toml(&text)
            .map_err(|err| ProjectError::InvalidConfig(format!("{}: {err}", path.display())))?;
        let project = Self { dir, config };
        project.ensure_dirs()?;
        // Projetos antigos/incompletos: regenera os YAML derivados.
        if !project.dir.join(GAME_FILE).is_file() || !project.dir.join(MANIFEST_FILE).is_file() {
            project.write_gameassets_files()?;
        }
        Ok(project)
    }

    pub fn reload_config(&mut self) -> Result<(), ProjectError> {
        let path = self.dir.join(STUDIO_FILE);
        let text = fs::read_to_string(&path).map_err(|err| io(&path, err))?;
        self.config = StudioConfig::from_toml(&text)
            .map_err(|err| ProjectError::InvalidConfig(format!("{}: {err}", path.display())))?;
        Ok(())
    }

    pub fn ensure_dirs(&self) -> Result<(), ProjectError> {
        for dir in [
            self.images_dir(),
            self.meshes_dir(),
            self.intermediate_dir(),
            self.motions_dir(),
            self.logs_dir(),
            self.studio_dir(),
            self.preview_dir(),
            self.history_dir(),
            self.thumbs_dir(),
        ] {
            fs::create_dir_all(&dir).map_err(|err| io(&dir, err))?;
        }
        Ok(())
    }

    /// Grava `studio.toml` e regenera os YAML do GameAssets.
    pub fn save_config(&self) -> Result<(), ProjectError> {
        let issues: Vec<String> = self
            .config
            .validate()
            .into_iter()
            .filter(|issue| issue.level == crate::config::IssueLevel::Error)
            .map(|issue| issue.message)
            .collect();
        if !issues.is_empty() {
            return Err(ProjectError::InvalidConfig(issues.join("\n")));
        }
        let text = self.config.to_toml().map_err(ProjectError::InvalidConfig)?;
        write_atomic(&self.dir.join(STUDIO_FILE), text.as_bytes())?;
        self.write_gameassets_files()
    }

    pub fn write_gameassets_files(&self) -> Result<(), ProjectError> {
        let game = build_game_yaml(&self.config)?;
        let manifest = build_manifest_yaml(&self.config)?;
        write_atomic(&self.dir.join(GAME_FILE), game.as_bytes())?;
        write_atomic(&self.dir.join(MANIFEST_FILE), manifest.as_bytes())?;
        Ok(())
    }

    pub fn id(&self) -> &str {
        &self.config.asset.id
    }

    pub fn images_dir(&self) -> PathBuf {
        self.dir.join("images")
    }

    pub fn meshes_dir(&self) -> PathBuf {
        self.dir.join("meshes")
    }

    pub fn intermediate_dir(&self) -> PathBuf {
        self.meshes_dir().join("_intermediate")
    }

    pub fn image_path(&self) -> PathBuf {
        self.images_dir().join(format!("{}.png", self.id()))
    }

    pub fn shape_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_shape.glb", self.id()))
    }

    pub fn clean_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_clean.glb", self.id()))
    }

    pub fn refined_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_refined.glb", self.id()))
    }

    pub fn to_paint_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_to_paint.glb", self.id()))
    }

    pub fn painted_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_painted.glb", self.id()))
    }

    pub fn rigged_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_rigged.glb", self.id()))
    }

    pub fn animated_path(&self) -> PathBuf {
        self.intermediate_dir()
            .join(format!("{}_rigged_animated.glb", self.id()))
    }

    pub fn lod_path(&self, level: u32) -> PathBuf {
        self.meshes_dir()
            .join(format!("{}_lod{level}.glb", self.id()))
    }

    pub fn collision_path(&self) -> PathBuf {
        self.meshes_dir()
            .join(format!("{}_collision.glb", self.id()))
    }

    pub fn precompute_path(&self) -> PathBuf {
        self.meshes_dir()
            .join(format!("{}_precompute.json", self.id()))
    }

    pub fn motions_dir(&self) -> PathBuf {
        self.dir.join("motions")
    }

    pub fn motion_npz(&self, name: &str) -> PathBuf {
        self.motions_dir().join(format!("{name}.npz"))
    }

    pub fn motion_meta(&self, name: &str) -> PathBuf {
        self.motions_dir().join(format!("{name}.json"))
    }

    pub fn logs_dir(&self) -> PathBuf {
        self.dir.join("logs")
    }

    pub fn resume_log_path(&self) -> PathBuf {
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        self.logs_dir().join(format!("resume-{stamp}.jsonl"))
    }

    pub fn studio_log_path(&self) -> PathBuf {
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        self.logs_dir().join(format!("studio-{stamp}.log"))
    }

    pub fn studio_dir(&self) -> PathBuf {
        self.dir.join(".studio")
    }

    pub fn trace_path(&self) -> PathBuf {
        self.studio_dir().join("trace.jsonl")
    }

    pub fn preview_dir(&self) -> PathBuf {
        self.studio_dir().join("preview")
    }

    pub fn history_dir(&self) -> PathBuf {
        self.studio_dir().join("history")
    }

    pub fn history_run_dir(&self) -> PathBuf {
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        self.history_dir().join(stamp.to_string())
    }

    pub fn thumbs_dir(&self) -> PathBuf {
        self.studio_dir().join("thumbs")
    }

    pub fn cover_path(&self) -> PathBuf {
        self.thumbs_dir().join("cover.png")
    }

    /// Caminho do artefacto de uma etapa, para o histórico/fallback.
    pub fn stage_artifact_paths(&self, stage: crate::reroll::Stage) -> Vec<PathBuf> {
        use crate::reroll::Stage;
        match stage {
            Stage::Image => vec![self.image_path()],
            Stage::Shape => vec![self.shape_path()],
            Stage::Clean => vec![self.clean_path()],
            Stage::Refine => vec![self.refined_path()],
            Stage::ToPaint => vec![self.to_paint_path()],
            Stage::Paint => vec![self.painted_path()],
            Stage::Rig => vec![self.rigged_path()],
            Stage::Animate => vec![self.animated_path()],
            Stage::Lod => vec![self.lod_path(0), self.lod_path(1), self.lod_path(2)],
            Stage::Collision => vec![self.collision_path(), self.precompute_path()],
        }
    }
}

pub fn default_workspace() -> PathBuf {
    if let Ok(value) = std::env::var("AIGAMEKIT_STUDIO_WORKSPACE") {
        let value = value.trim();
        if !value.is_empty() {
            return PathBuf::from(value);
        }
    }
    directories::UserDirs::new()
        .map(|dirs| dirs.home_dir().join("AiGameKitStudio"))
        .unwrap_or_else(|| PathBuf::from("AiGameKitStudio"))
}

pub fn slugify(text: &str) -> String {
    let mut out = String::new();
    let mut last_dash = false;
    for ch in text.nfd().flat_map(char::to_lowercase) {
        if unicode_normalization::char::is_combining_mark(ch) {
            continue;
        }
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            last_dash = false;
        } else if !last_dash && !out.is_empty() {
            out.push('-');
            last_dash = true;
        }
    }
    let out = out.trim_matches('-').to_string();
    let out = if out.is_empty() {
        "asset".to_string()
    } else {
        out
    };
    out.chars().take(40).collect::<String>()
}

pub fn unique_project_dir(workspace: &Path, base: &str) -> PathBuf {
    let mut candidate = workspace.join(base);
    let mut n = 2;
    while candidate.join(STUDIO_FILE).is_file() || candidate.exists() {
        candidate = workspace.join(format!("{base}-{n}"));
        n += 1;
    }
    candidate
}

pub fn scan_workspace(workspace: &Path) -> Vec<ProjectSummary> {
    let Ok(read_dir) = fs::read_dir(workspace) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in read_dir.flatten() {
        let dir = entry.path();
        if !dir.is_dir() {
            continue;
        }
        let path = dir.join(STUDIO_FILE);
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(config) = StudioConfig::from_toml(&text) else {
            continue;
        };
        let modified = fs::metadata(&dir).and_then(|m| m.modified()).ok();
        out.push(ProjectSummary {
            dir,
            id: config.asset.id,
            idea: config.asset.idea,
            category: config.asset.category,
            modified,
        });
    }
    out.sort_by_key(|entry| std::cmp::Reverse(entry.modified));
    out
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), ProjectError> {
    let parent = path
        .parent()
        .ok_or_else(|| ProjectError::InvalidDir(path.to_path_buf()))?;
    fs::create_dir_all(parent).map_err(|err| io(parent, err))?;
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("file");
    let tmp = parent.join(format!(".{file_name}.tmp-{}", std::process::id()));
    fs::write(&tmp, bytes).map_err(|err| io(&tmp, err))?;
    if let Err(err) = fs::rename(&tmp, path) {
        let _ = fs::remove_file(&tmp);
        return Err(io(path, err));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugify_handles_accents_and_spaces() {
        assert_eq!(slugify("Caixa de Madeira"), "caixa-de-madeira");
        assert_eq!(slugify("Coração Antigo"), "coracao-antigo");
        assert_eq!(slugify("  ***  "), "asset");
    }

    #[test]
    fn create_writes_three_files_and_paths() {
        let tmp = tempfile::tempdir().expect("tmp");
        let dir = tmp.path().join("crate");
        let config = StudioConfig::new("crate", "a wooden crate", "prop");
        let project = StudioProject::create(&dir, config).expect("create");
        assert!(dir.join(STUDIO_FILE).is_file());
        assert!(dir.join(GAME_FILE).is_file());
        assert!(dir.join(MANIFEST_FILE).is_file());
        assert_eq!(project.image_path(), dir.join("images/crate.png"));
        assert_eq!(project.lod_path(0), dir.join("meshes/crate_lod0.glb"));
        assert!(project.intermediate_dir().is_dir());
    }

    #[test]
    fn open_round_trips_and_repairs_missing_yaml() {
        let tmp = tempfile::tempdir().expect("tmp");
        let dir = tmp.path().join("crate");
        StoryConfigHelper::create(&dir);
        fs::remove_file(dir.join(GAME_FILE)).expect("remove");
        let project = StudioProject::open(&dir).expect("open");
        assert_eq!(project.id(), "crate");
        assert!(dir.join(GAME_FILE).is_file());
    }

    struct StoryConfigHelper;
    impl StoryConfigHelper {
        fn create(dir: &Path) {
            let config = StudioConfig::new("crate", "a wooden crate", "prop");
            StudioProject::create(dir, config).expect("create");
        }
    }

    #[test]
    fn scan_lists_projects() {
        let tmp = tempfile::tempdir().expect("tmp");
        StoryConfigHelper::create(&tmp.path().join("a"));
        StoryConfigHelper::create(&tmp.path().join("b"));
        let projects = scan_workspace(tmp.path());
        assert_eq!(projects.len(), 2);
    }

    #[test]
    fn unique_dir_avoids_collisions() {
        let tmp = tempfile::tempdir().expect("tmp");
        StoryConfigHelper::create(&tmp.path().join("crate"));
        let next = unique_project_dir(tmp.path(), "crate");
        assert_eq!(next.file_name().unwrap(), "crate-2");
    }
}
