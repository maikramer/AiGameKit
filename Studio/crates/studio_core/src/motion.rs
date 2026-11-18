//! Clips de movimento por texto (`motion3d`) geridos pelo Studio.

use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::project::{ProjectError, StudioProject, slugify};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MotionRequest {
    pub name: String,
    pub prompt: String,
    pub duration_s: f32,
    #[serde(default)]
    pub looped: bool,
    #[serde(default)]
    pub foot_lock: bool,
    pub quality: String,
    #[serde(default = "default_attempts")]
    pub attempts: u32,
    #[serde(default)]
    pub seed: Option<i64>,
}

fn default_attempts() -> u32 {
    3
}

impl MotionRequest {
    pub fn new(name: impl Into<String>, prompt: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            prompt: prompt.into(),
            duration_s: 2.0,
            looped: true,
            foot_lock: true,
            quality: "medium".into(),
            attempts: 3,
            seed: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MotionClip {
    pub name: String,
    pub prompt: String,
    pub npz: String,
    pub duration_s: f32,
    pub looped: bool,
    pub foot_lock: bool,
    pub quality: String,
    pub attempts: u32,
    #[serde(default)]
    pub seed: Option<i64>,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(default)]
    pub foot_slide: Option<f64>,
    #[serde(default)]
    pub ground_penetration: Option<f64>,
    #[serde(default)]
    pub jerk: Option<f64>,
    pub created_at: String,
}

/// Resultado de `motion3d generate --json`.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct MotionGenerateResult {
    #[serde(default)]
    pub output: String,
    #[serde(default)]
    pub prompt: String,
    #[serde(default)]
    pub seconds: f64,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(default)]
    pub foot_slide: Option<f64>,
    #[serde(default)]
    pub ground_penetration: Option<f64>,
    #[serde(default)]
    pub jerk: Option<f64>,
}

pub fn parse_generate_result(text: &str) -> Option<MotionGenerateResult> {
    let line = text
        .lines()
        .rev()
        .map(str::trim)
        .find(|line| line.starts_with('{') && line.ends_with('}'))?;
    serde_json::from_str(line).ok()
}

pub fn clip_from_request(
    request: &MotionRequest,
    result: Option<&MotionGenerateResult>,
) -> MotionClip {
    MotionClip {
        name: request.name.clone(),
        prompt: request.prompt.clone(),
        npz: format!("{}.npz", request.name),
        duration_s: request.duration_s,
        looped: request.looped,
        foot_lock: request.foot_lock,
        quality: request.quality.clone(),
        attempts: request.attempts,
        seed: request.seed,
        score: result.and_then(|r| r.score),
        foot_slide: result.and_then(|r| r.foot_slide),
        ground_penetration: result.and_then(|r| r.ground_penetration),
        jerk: result.and_then(|r| r.jerk),
        created_at: chrono::Local::now().to_rfc3339(),
    }
}

pub fn unique_name(project: &StudioProject, base: &str) -> String {
    let base = slugify(base);
    let mut name = base.clone();
    let mut n = 2;
    while project.motion_npz(&name).exists() || project.motion_meta(&name).exists() {
        name = format!("{base}-{n}");
        n += 1;
    }
    name
}

pub fn preview_path(project: &StudioProject, name: &str) -> PathBuf {
    project.preview_dir().join(format!("{name}.glb"))
}

pub fn load_clips(project: &StudioProject) -> Vec<MotionClip> {
    let Ok(read_dir) = fs::read_dir(project.motions_dir()) else {
        return Vec::new();
    };
    let mut clips = Vec::new();
    for entry in read_dir.flatten() {
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        if let Ok(clip) = serde_json::from_str::<MotionClip>(&text) {
            clips.push(clip);
        }
    }
    clips.sort_by(|a, b| a.created_at.cmp(&b.created_at));
    clips
}

pub fn save_clip(project: &StudioProject, clip: &MotionClip) -> Result<(), ProjectError> {
    let path = project.motion_meta(&clip.name);
    let text = serde_json::to_string_pretty(clip)
        .map_err(|err| ProjectError::InvalidConfig(format!("motion json: {err}")))?;
    write_json(&path, &text)
}

pub fn delete_clip(project: &StudioProject, name: &str) -> Result<(), ProjectError> {
    for path in [
        project.motion_npz(name),
        project.motion_meta(name),
        preview_path(project, name),
    ] {
        if path.exists() {
            fs::remove_file(&path).map_err(|err| ProjectError::Io {
                path: path.clone(),
                source: err,
            })?;
        }
    }
    Ok(())
}

fn write_json(path: &Path, text: &str) -> Result<(), ProjectError> {
    let parent = path
        .parent()
        .ok_or_else(|| ProjectError::InvalidDir(path.to_path_buf()))?;
    fs::create_dir_all(parent).map_err(|err| ProjectError::Io {
        path: parent.to_path_buf(),
        source: err,
    })?;
    fs::write(path, text).map_err(|err| ProjectError::Io {
        path: path.to_path_buf(),
        source: err,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::StudioConfig;

    fn project() -> (tempfile::TempDir, StudioProject) {
        let tmp = tempfile::tempdir().expect("tmp");
        let config = StudioConfig::new("hero", "a hero", "humanoid");
        let project = StudioProject::create(tmp.path().join("hero"), config).expect("project");
        (tmp, project)
    }

    #[test]
    fn parse_generate_json_tail() {
        let result = parse_generate_result(
            "some noise\n{\"output\":\"/tmp/walk.npz\",\"prompt\":\"walk\",\"seconds\":0.2,\"score\":0.9,\"jerk\":0.02}\n",
        )
        .expect("json");
        assert_eq!(result.output, "/tmp/walk.npz");
        assert_eq!(result.score, Some(0.9));
    }

    #[test]
    fn clips_round_trip_and_unique_names() {
        let (_tmp, project) = project();
        let request = MotionRequest::new("walk", "walk forward");
        let mut clip = clip_from_request(&request, None);
        save_clip(&project, &clip).expect("save");
        assert_eq!(load_clips(&project).len(), 1);
        assert_eq!(unique_name(&project, "walk"), "walk-2");
        delete_clip(&project, "walk").expect("delete");
        assert!(load_clips(&project).is_empty());
        clip.name = "run".into();
        save_clip(&project, &clip).expect("save");
    }
}
