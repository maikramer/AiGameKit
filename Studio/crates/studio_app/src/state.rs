//! Estado da aplicação, ações e workers.

use std::{
    collections::{BTreeMap, VecDeque},
    fs::File,
    path::{Path, PathBuf},
    time::Instant,
};

use bevy::prelude::*;
use bevy_egui::egui;
use crossbeam_channel::Receiver;
use studio_core::{
    check::CheckPayload,
    cli::{Capture, CliConfig},
    config::{GpuMode, StudioConfig},
    motion::MotionClip,
    project::{ProjectSummary, StudioProject},
    reroll::Stage,
    runner::RunHandle,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum MainTab {
    #[default]
    Studio,
    Library,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BottomTab {
    #[default]
    Tasks,
    Console,
    Motion,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogKind {
    Info,
    Ok,
    Warn,
    Error,
    Tool,
    Progress,
}

#[derive(Debug, Clone)]
pub struct LogLine {
    pub at: String,
    pub kind: LogKind,
    pub text: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToastKind {
    Info,
    Ok,
    Warn,
    Error,
}

#[derive(Debug, Clone)]
pub struct Toast {
    pub text: String,
    pub kind: ToastKind,
    pub until: Instant,
}

#[derive(Debug, Clone)]
pub struct CreateRequest {
    pub dir: PathBuf,
    pub config: StudioConfig,
    pub reference: Option<PathBuf>,
}

#[derive(Debug, Clone)]
#[allow(clippy::large_enum_variant)]
pub enum Action {
    Run,
    Cancel,
    Reroll(Stage),
    InvalidateFrom(Stage),
    CompareHistory(Stage),
    ReloadPreview,
    Reset,
    RefreshCheck,
    SaveConfig,
    RevertConfig,
    OpenProject(PathBuf),
    NewProject(CreateRequest),
    CloseProject,
    DeleteProject(PathBuf),
    ConfirmDelete(PathBuf),
    SetGpu(GpuMode),
    SelectArtifact(String),
    Frame,
    TogglePlay,
    SetClip(String),
    SetSpeed(f32),
    GenerateMotion(studio_core::motion::MotionRequest),
    PreviewMotion(String),
    PackMotions(Vec<String>),
    DeleteMotion(String),
    Doctor,
    #[allow(dead_code)]
    BakeThumbnail,
    RefreshLibrary,
    SetReference(PathBuf),
}

#[derive(Resource, Default)]
pub struct PendingActions {
    actions: VecDeque<Action>,
}

impl PendingActions {
    pub fn push(&mut self, action: Action) {
        self.actions.push_back(action);
    }

    pub fn next(&mut self) -> Option<Action> {
        self.actions.pop_front()
    }
}

#[derive(Debug, Default)]
pub struct RunState {
    pub handle: Option<RunHandle>,
    pub rx: Option<Receiver<studio_core::events::PipelineEvent>>,
    pub log_path: Option<PathBuf>,
    pub started: Option<Instant>,
    pub active_stage: Option<String>,
    pub active_id: String,
    pub phase: Option<String>,
    pub percent: Option<f32>,
    pub running: bool,
    pub cancelled: bool,
    pub last_error: Option<String>,
}

impl RunState {
    pub fn elapsed_secs(&self) -> f64 {
        self.started
            .map(|started| started.elapsed().as_secs_f64())
            .unwrap_or(0.0)
    }

    pub fn is_busy(&self) -> bool {
        self.handle.is_some() || self.running
    }
}

#[derive(Resource)]
pub struct StudioState {
    pub cli: CliConfig,
    pub workspace: PathBuf,
    pub project: Option<StudioProject>,
    pub check: Option<CheckPayload>,
    pub check_error: Option<String>,
    pub last_check: Option<Instant>,
    pub run: RunState,
    pub log: VecDeque<LogLine>,
    pub log_cap: usize,
    pub log_file: Option<File>,
    pub dirty: bool,
    pub tab: MainTab,
    pub bottom: BottomTab,
    pub selected: String,
    pub gpu: GpuMode,
    pub toasts: Vec<Toast>,
    pub library: Vec<ProjectSummary>,
    pub clips: Vec<MotionClip>,
    pub timings: BTreeMap<String, f64>,
    pub stage_errors: BTreeMap<String, String>,
    pub vramd_running: usize,
    pub vramd_queued: usize,
    pub vramd_job: Option<String>,
    pub vramd_error: Option<String>,
    pub check_generation: u64,
    pub preview_override: Option<String>,
    pub pending_run: bool,
}

impl Default for StudioState {
    fn default() -> Self {
        Self {
            cli: CliConfig::resolve(),
            workspace: studio_core::project::default_workspace(),
            project: None,
            check: None,
            check_error: None,
            last_check: None,
            run: RunState::default(),
            log: VecDeque::new(),
            log_cap: 4000,
            log_file: None,
            dirty: false,
            tab: MainTab::Studio,
            bottom: BottomTab::Tasks,
            selected: "painted".into(),
            gpu: GpuMode::Auto,
            toasts: Vec::new(),
            library: Vec::new(),
            clips: Vec::new(),
            timings: BTreeMap::new(),
            stage_errors: BTreeMap::new(),
            vramd_running: 0,
            vramd_queued: 0,
            vramd_job: None,
            vramd_error: None,
            check_generation: 0,
            preview_override: None,
            pending_run: false,
        }
    }
}

impl StudioState {
    pub fn project_dir(&self) -> Option<&Path> {
        self.project.as_ref().map(|project| project.dir.as_path())
    }

    pub fn push_log(&mut self, kind: LogKind, text: impl Into<String>) {
        let text = text.into();
        let at = chrono::Local::now().format("%H:%M:%S").to_string();
        if let Some(file) = self.log_file.as_mut() {
            use std::io::Write;
            let _ = writeln!(file, "[{at}] {text}");
        }
        self.log.push_back(LogLine { at, kind, text });
        while self.log.len() > self.log_cap {
            self.log.pop_front();
        }
    }

    pub fn open_log_file(&mut self) {
        let Some(project) = self.project.as_ref() else {
            return;
        };
        let path = project.studio_log_path();
        self.log_file = File::create(&path).ok();
        let path = path.display().to_string();
        self.push_log(LogKind::Info, format!("log: {path}"));
    }

    pub fn toast(&mut self, kind: ToastKind, text: impl Into<String>) {
        let text = text.into();
        self.push_log(
            match kind {
                ToastKind::Info => LogKind::Info,
                ToastKind::Ok => LogKind::Ok,
                ToastKind::Warn => LogKind::Warn,
                ToastKind::Error => LogKind::Error,
            },
            text.clone(),
        );
        self.toasts.push(Toast {
            text,
            kind,
            until: Instant::now() + std::time::Duration::from_secs(5),
        });
        if self.toasts.len() > 4 {
            self.toasts.remove(0);
        }
    }

    pub fn set_project(&mut self, project: StudioProject) {
        self.check = None;
        self.check_error = None;
        self.stage_errors.clear();
        self.timings.clear();
        self.preview_override = None;
        self.gpu = project.config.run.gpu;
        self.clips = studio_core::motion::load_clips(&project);
        self.selected = default_artifact(&self.check, &project);
        self.project = Some(project);
        self.check_generation += 1;
        self.open_log_file();
    }

    pub fn select_default_artifact(&mut self) {
        if let Some(project) = self.project.as_ref() {
            if !self
                .check
                .as_ref()
                .is_some_and(|check| check.has_artifact(&self.selected))
            {
                self.selected = default_artifact(&self.check, project);
            }
        }
    }

    pub fn artifact_path(&self, key: &str) -> Option<PathBuf> {
        self.check
            .as_ref()
            .and_then(|check| check.artifact(key))
            .filter(|artifact| artifact.exists && !artifact.path.is_empty())
            .map(|artifact| PathBuf::from(&artifact.path))
    }

    pub fn stage_stale(&self, stage: Stage) -> bool {
        let Some(check) = self.check.as_ref() else {
            return false;
        };
        match stage {
            Stage::Shape => check.stale.omni,
            Stage::Refine => check.stale.refine,
            _ => false,
        }
    }

    pub fn can_run(&self) -> bool {
        self.project.is_some()
            && self.check.is_some()
            && !self.run.is_busy()
            && self.lock_live().is_none()
    }

    pub fn lock_live(&self) -> Option<&studio_core::check::LockInfo> {
        self.check
            .as_ref()
            .and_then(|check| check.lock.as_ref())
            .filter(|lock| lock.live)
    }
}

pub fn default_artifact(check: &Option<CheckPayload>, project: &StudioProject) -> String {
    let order = [
        "lod0", "painted", "animated", "rigged", "refined", "clean", "shape", "image",
    ];
    if let Some(check) = check {
        for key in order {
            if check.has_artifact(key) {
                return key.to_string();
            }
        }
    }
    for (key, path) in [
        ("lod0", project.lod_path(0)),
        ("painted", project.painted_path()),
        ("animated", project.animated_path()),
        ("rigged", project.rigged_path()),
        ("refined", project.refined_path()),
        ("clean", project.clean_path()),
        ("shape", project.shape_path()),
        ("image", project.image_path()),
    ] {
        if path.is_file() {
            return key.to_string();
        }
    }
    "shape".into()
}

#[derive(Debug, Clone)]
pub struct CheckResponse {
    pub result: Result<CheckPayload, String>,
    pub raw: String,
    pub at: Instant,
}

#[derive(Resource, Default)]
pub struct CheckWorker {
    pub rx: Option<Receiver<CheckResponse>>,
    pub in_flight: bool,
}

pub type MotionList = Vec<(String, PathBuf)>;

#[derive(Debug, Clone)]
pub enum JobKind {
    MotionGenerate {
        request: studio_core::motion::MotionRequest,
        project: PathBuf,
    },
    MotionPreview {
        name: String,
        project: PathBuf,
    },
    MotionPack {
        active: String,
        motions: MotionList,
        project: PathBuf,
    },
    Doctor,
}

#[derive(Debug, Clone)]
pub struct JobResponse {
    pub kind: JobKind,
    pub capture: Result<Capture, String>,
}

#[derive(Resource, Default)]
pub struct JobWorker {
    pub rx: Option<Receiver<JobResponse>>,
    pub in_flight: bool,
    pub label: String,
}

pub type VramdPoll = Result<(usize, usize, Option<String>), String>;

#[derive(Resource, Default)]
pub struct VramdWorker {
    pub rx: Option<Receiver<VramdPoll>>,
    pub in_flight: bool,
    pub last_poll: Option<Instant>,
}

#[derive(Resource, Default)]
pub struct UiState {
    pub viewport: Option<egui::Rect>,
    pub egui_wants_pointer: bool,
    pub egui_wants_keyboard: bool,
    pub pointer_in_viewport: bool,
    pub show_new_project: bool,
    pub confirm_delete: Option<PathBuf>,
    pub turntable: bool,
    pub wireframe: bool,
    pub show_grid: bool,
    pub force_thumb: bool,
    pub invalidate_after_save: Option<Stage>,
    pub console_errors_only: bool,
    pub console_autoscroll: bool,
}

#[derive(Resource, Default)]
pub struct TextureCache {
    pub map: BTreeMap<PathBuf, (egui::TextureHandle, std::time::SystemTime)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AssetPreset {
    #[default]
    Prop,
    Character,
    Creature,
    Building,
}

impl AssetPreset {
    pub const ALL: [Self; 4] = [Self::Prop, Self::Character, Self::Creature, Self::Building];

    pub fn label(self) -> &'static str {
        match self {
            Self::Prop => "Prop estático",
            Self::Character => "Personagem",
            Self::Creature => "Criatura",
            Self::Building => "Edifício",
        }
    }

    pub fn category(self) -> &'static str {
        match self {
            Self::Prop => "prop",
            Self::Character => "humanoid",
            Self::Creature => "creature",
            Self::Building => "building",
        }
    }
}

#[derive(Resource)]
pub struct NewProjectForm {
    pub prompt: String,
    pub id: String,
    pub id_touched: bool,
    pub preset: AssetPreset,
    pub quality: String,
    pub gpu: GpuMode,
    pub reference: Option<PathBuf>,
    pub error: Option<String>,
}

impl Default for NewProjectForm {
    fn default() -> Self {
        Self {
            prompt: String::new(),
            id: String::new(),
            id_touched: false,
            preset: AssetPreset::Prop,
            quality: "medium".into(),
            gpu: GpuMode::Auto,
            reference: None,
            error: None,
        }
    }
}

impl NewProjectForm {
    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn sync_id(&mut self) {
        if !self.id_touched {
            self.id = studio_core::project::slugify(&self.prompt);
        }
    }

    pub fn to_config(&self) -> StudioConfig {
        let mut config =
            StudioConfig::new(self.id.trim(), self.prompt.trim(), self.preset.category());
        config.asset.kind = match self.preset {
            AssetPreset::Prop => None,
            AssetPreset::Character => Some("character".into()),
            AssetPreset::Creature => Some("creature".into()),
            AssetPreset::Building => Some("building".into()),
        };
        config.asset.quality = (!self.quality.trim().is_empty()).then(|| self.quality.clone());
        config.run.gpu = self.gpu;
        match self.preset {
            AssetPreset::Building => {
                config.omni.control_type = Some("bbox".into());
                config.omni.bbox_preset = Some("building".into());
            }
            AssetPreset::Prop => {
                config.omni.control_type = Some("bbox".into());
            }
            AssetPreset::Character | AssetPreset::Creature => {
                config.omni.control_type = Some("pose".into());
            }
        }
        config
    }
}

#[derive(Resource)]
pub struct MotionForm {
    pub prompt: String,
    pub duration_s: f32,
    pub looped: bool,
    pub foot_lock: bool,
    pub quality: String,
    pub seed: String,
    pub attempts: u32,
    pub selected: Vec<String>,
    pub error: Option<String>,
}

impl Default for MotionForm {
    fn default() -> Self {
        Self {
            prompt: "a tired traveler walks forward".into(),
            duration_s: 2.0,
            looped: true,
            foot_lock: true,
            quality: "medium".into(),
            seed: String::new(),
            attempts: 3,
            selected: Vec::new(),
            error: None,
        }
    }
}

#[derive(Resource, Default)]
pub struct LibraryState {
    pub search: String,
    pub last_refresh: Option<Instant>,
}
