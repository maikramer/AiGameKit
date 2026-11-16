//! Construção dos comandos externos (gameassets/motion3d/vramd).
//!
//! `CmdSpec` é serializável o suficiente para os testes compararem argumentos
//! sem arrancar processos. Nada aqui bloqueia: quem chama decide thread.

use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use crate::{
    config::GpuMode,
    motion::MotionRequest,
    project::StudioProject,
    reroll::Stage,
};

#[derive(Debug, Clone)]
pub struct CliConfig {
    pub gameassets: String,
    pub motion3d: String,
    pub vramd: String,
}

fn env_or(keys: &[&str], default: &str) -> String {
    for key in keys {
        if let Ok(value) = std::env::var(key) {
            let value = value.trim();
            if !value.is_empty() {
                return value.to_string();
            }
        }
    }
    default.to_string()
}

impl CliConfig {
    pub fn resolve() -> Self {
        Self {
            gameassets: env_or(&["AIGAMEKIT_GAMEASSETS_BIN", "GAMEASSETS_BIN"], "gameassets"),
            motion3d: env_or(&["AIGAMEKIT_MOTION3D_BIN", "MOTION3D_BIN"], "motion3d"),
            vramd: env_or(&["AIGAMEKIT_VRAMD_BIN", "VRAMD_BIN"], "vramd"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct CmdSpec {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
}

impl CmdSpec {
    pub fn command(&self) -> Command {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.args)
            .current_dir(&self.cwd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (key, value) in &self.env {
            cmd.env(key, value);
        }
        cmd
    }

    pub fn display(&self) -> String {
        let mut parts = vec![self.program.clone()];
        parts.extend(self.args.iter().cloned());
        parts.join(" ")
    }
}

#[derive(Debug, Clone)]
pub struct ResumeSpec {
    pub spec: CmdSpec,
    pub log_path: PathBuf,
}

fn base_env(project: &StudioProject) -> Vec<(String, String)> {
    vec![
        (
            "AIGAMEKIT_PIPELINE_TRACE_LOG".into(),
            project.trace_path().to_string_lossy().into_owned(),
        ),
        ("AIGAMEKIT_STUDIO".into(), "1".into()),
        // Sem buffering de stdout: os eventos JSONL têm de chegar em tempo real.
        ("PYTHONUNBUFFERED".into(), "1".into()),
        ("NO_COLOR".into(), "1".into()),
        ("CLICOLOR".into(), "0".into()),
    ]
}

fn push_env(env: &mut Vec<(String, String)>, key: &str, value: impl Into<String>) {
    let value = value.into();
    env.retain(|(k, _)| k != key);
    env.push((key.to_string(), value));
}

impl CliConfig {
    pub fn studio_check(&self, project: &StudioProject) -> CmdSpec {
        CmdSpec {
            program: self.gameassets.clone(),
            args: vec![
                "studio".into(),
                "check".into(),
                "--project".into(),
                project.dir.to_string_lossy().into_owned(),
                "--json".into(),
            ],
            cwd: project.dir.clone(),
            env: base_env(project),
        }
    }

    pub fn studio_invalidate(
        &self,
        project: &StudioProject,
        stage: Stage,
        include_stage: bool,
    ) -> CmdSpec {
        let mut args = vec![
            "studio".into(),
            "invalidate".into(),
            "--project".into(),
            project.dir.to_string_lossy().into_owned(),
            "--from".into(),
            stage.key().into(),
        ];
        if include_stage {
            args.push("--include-stage".into());
        }
        args.push("--json".into());
        CmdSpec {
            program: self.gameassets.clone(),
            args,
            cwd: project.dir.clone(),
            env: base_env(project),
        }
    }

    pub fn resume(&self, project: &StudioProject, gpu: GpuMode, fail_fast: bool) -> ResumeSpec {
        let log_path = project.resume_log_path();
        let mut env = base_env(project);
        push_env(&mut env, "VRAMD_GPU_MODE", gpu.as_str());
        let mut args = vec![
            "resume".into(),
            "--profile".into(),
            project.dir.join(crate::project::GAME_FILE).to_string_lossy().into_owned(),
            "--manifest".into(),
            project
                .dir
                .join(crate::project::MANIFEST_FILE)
                .to_string_lossy()
                .into_owned(),
            "--no-dashboard".into(),
            "--gpu".into(),
            gpu.as_str().into(),
            "--log".into(),
            log_path.to_string_lossy().into_owned(),
        ];
        if fail_fast {
            args.push("--fail-fast".into());
        }
        ResumeSpec {
            spec: CmdSpec {
                program: self.gameassets.clone(),
                args,
                cwd: project.dir.clone(),
                env,
            },
            log_path,
        }
    }

    pub fn doctor(&self, cwd: &Path) -> CmdSpec {
        CmdSpec {
            program: self.gameassets.clone(),
            args: vec!["doctor".into(), "--json".into()],
            cwd: cwd.to_path_buf(),
            env: Vec::new(),
        }
    }

    pub fn vramd_queue(&self, cwd: &Path) -> CmdSpec {
        CmdSpec {
            program: self.vramd.clone(),
            args: vec!["queue".into(), "--json".into()],
            cwd: cwd.to_path_buf(),
            env: Vec::new(),
        }
    }

    pub fn vramd_status(&self, cwd: &Path) -> CmdSpec {
        CmdSpec {
            program: self.vramd.clone(),
            args: vec!["status".into(), "--json".into()],
            cwd: cwd.to_path_buf(),
            env: Vec::new(),
        }
    }

    pub fn motion_generate(&self, project: &StudioProject, req: &MotionRequest) -> CmdSpec {
        let npz = project.motion_npz(&req.name);
        let mut args = vec![
            "generate".into(),
            req.prompt.clone(),
            "-o".into(),
            npz.to_string_lossy().into_owned(),
            "--duration".into(),
            format!("{:.2}", req.duration_s),
            "--quality".into(),
            req.quality.clone(),
            "--attempts".into(),
            req.attempts.to_string(),
            "--json".into(),
        ];
        args.push(if req.looped { "--loop" } else { "--no-loop" }.into());
        args.push(
            if req.foot_lock {
                "--foot-lock"
            } else {
                "--no-foot-lock"
            }
            .into(),
        );
        if let Some(seed) = req.seed {
            args.push("--seed".into());
            args.push(seed.to_string());
        }
        CmdSpec {
            program: self.motion3d.clone(),
            args,
            cwd: project.dir.clone(),
            env: base_env(project),
        }
    }

    pub fn motion_apply(
        &self,
        project: &StudioProject,
        npz: &Path,
        rigged: &Path,
        out: &Path,
        clip: &str,
    ) -> CmdSpec {
        CmdSpec {
            program: self.motion3d.clone(),
            args: vec![
                "apply-rigged".into(),
                npz.to_string_lossy().into_owned(),
                rigged.to_string_lossy().into_owned(),
                "-o".into(),
                out.to_string_lossy().into_owned(),
                "--clip".into(),
                clip.into(),
            ],
            cwd: project.dir.clone(),
            env: base_env(project),
        }
    }

    pub fn motion_pack(
        &self,
        project: &StudioProject,
        rigged: &Path,
        out: &Path,
        motions: &[(String, PathBuf)],
        active: &str,
    ) -> CmdSpec {
        let mut args = vec![
            "pack-rigged".into(),
            rigged.to_string_lossy().into_owned(),
            "-o".into(),
            out.to_string_lossy().into_owned(),
            "--active".into(),
            active.into(),
        ];
        for (name, npz) in motions {
            args.push("-m".into());
            args.push(format!("{name}={}", npz.to_string_lossy()));
        }
        CmdSpec {
            program: self.motion3d.clone(),
            args,
            cwd: project.dir.clone(),
            env: base_env(project),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Capture {
    pub status: Option<i32>,
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

impl Capture {
    pub fn message(&self) -> String {
        let stderr = self.stderr.trim();
        if !stderr.is_empty() {
            return stderr.to_string();
        }
        let stdout = self.stdout.trim();
        if !stdout.is_empty() {
            return stdout.to_string();
        }
        format!("exit {:?}", self.status)
    }
}

pub fn run_capture(spec: &CmdSpec) -> std::io::Result<Capture> {
    let output = spec.command().output()?;
    Ok(Capture {
        status: output.status.code(),
        success: output.status.success(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

/// Extrai o último objeto JSON de uma saída (alguns CLIs imprimem uma linha
/// extra antes do payload com `--json`).
pub fn last_json_line(text: &str) -> Option<&str> {
    text.lines()
        .rev()
        .map(str::trim)
        .find(|line| line.starts_with('{') && line.ends_with('}'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{PipelineStage, StudioConfig};

    fn project() -> (tempfile::TempDir, StudioProject) {
        let tmp = tempfile::tempdir().expect("tmp");
        let config = StudioConfig::new("crate", "a wooden crate", "prop");
        let project = StudioProject::create(tmp.path().join("crate"), config).expect("project");
        (tmp, project)
    }

    #[test]
    fn resume_passes_gpu_log_and_trace_env() {
        let (_tmp, project) = project();
        let cli = CliConfig {
            gameassets: "gameassets".into(),
            motion3d: "motion3d".into(),
            vramd: "vramd".into(),
        };
        let resume = cli.resume(&project, GpuMode::Remote, true);
        let joined = resume.spec.display();
        assert!(joined.contains("resume"));
        assert!(joined.contains("--no-dashboard"));
        assert!(joined.contains("--gpu remote"));
        assert!(joined.contains("--fail-fast"));
        assert!(joined.contains("--log"));
        assert!(resume
            .spec
            .env
            .iter()
            .any(|(k, v)| k == "AIGAMEKIT_PIPELINE_TRACE_LOG" && v.contains("trace.jsonl")));
        assert!(resume
            .spec
            .env
            .iter()
            .any(|(k, v)| k == "VRAMD_GPU_MODE" && v == "remote"));
    }

    #[test]
    fn invalidate_can_include_stage() {
        let (_tmp, project) = project();
        let cli = CliConfig {
            gameassets: "gameassets".into(),
            motion3d: "motion3d".into(),
            vramd: "vramd".into(),
        };
        let spec = cli.studio_invalidate(&project, Stage::Paint, true);
        assert!(spec.display().contains("--from paint"));
        assert!(spec.display().contains("--include-stage"));
    }

    #[test]
    fn motion_pack_lists_each_motion() {
        let (_tmp, project) = project();
        let cli = CliConfig {
            gameassets: "gameassets".into(),
            motion3d: "motion3d".into(),
            vramd: "vramd".into(),
        };
        let motions = vec![
            ("walk".to_string(), PathBuf::from("/tmp/walk.npz")),
            ("run".to_string(), PathBuf::from("/tmp/run.npz")),
        ];
        let spec = cli.motion_pack(
            &project,
            &project.rigged_path(),
            &project.animated_path(),
            &motions,
            "walk",
        );
        let display = spec.display();
        assert!(display.contains("-m walk=/tmp/walk.npz"));
        assert!(display.contains("-m run=/tmp/run.npz"));
        assert!(display.contains("--active walk"));
    }

    #[test]
    fn config_pipeline_keywords_are_used() {
        let (_tmp, project) = project();
        assert!(project.config.pipeline.has(PipelineStage::Paint));
    }
}
