//! Plugin principal: estado, workers, execução de ações e sincronização.

use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};

use bevy::prelude::*;
use crossbeam_channel::{TryRecvError, bounded};
use studio_core::{
    check::CheckPayload,
    cli::{CmdSpec, run_capture},
    events::{PipelineEvent, Stream, parse_run_log, stage_failures, stage_timings},
    motion,
    project::{self, StudioProject},
    reroll::{self, Stage},
    runner,
};

use crate::{
    preview::{
        LoadRequest, PreviewCommand, PreviewState,
        thumbs::{ThumbnailRequest, ThumbnailState},
    },
    state::*,
};

pub struct StudioPlugin {
    pub initial_project: Option<PathBuf>,
    pub auto_run: bool,
}

#[derive(Resource, Default)]
pub struct InitialProject {
    pub path: Option<PathBuf>,
    pub auto_run: bool,
}

#[derive(Resource, Default)]
struct PreviewSync {
    generation: u64,
    selected: String,
}

impl Plugin for StudioPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(StudioState::default())
            .insert_resource(PendingActions::default())
            .insert_resource(InitialProject {
                path: self.initial_project.clone(),
                auto_run: self.auto_run,
            })
            .insert_resource(PreviewSync::default())
            .init_resource::<CheckWorker>()
            .init_resource::<JobWorker>()
            .init_resource::<VramdWorker>()
            .init_resource::<UiState>()
            .init_resource::<TextureCache>()
            .init_resource::<NewProjectForm>()
            .init_resource::<MotionForm>()
            .init_resource::<LibraryState>()
            .add_systems(Startup, setup_studio)
            .add_systems(
                Update,
                (
                    execute_actions,
                    poll_check_worker,
                    poll_job_worker,
                    poll_vramd_worker,
                    pump_run_events,
                    auto_check,
                    auto_vramd,
                    sync_preview,
                    auto_thumbnail,
                    expire_toasts,
                )
                    .chain(),
            )
            .add_systems(PostUpdate, keyboard_shortcuts);
    }
}

fn setup_studio(
    mut state: ResMut<StudioState>,
    initial: Res<InitialProject>,
    mut actions: ResMut<PendingActions>,
) {
    refresh_library(&mut state);
    if let Some(dir) = initial.path.clone() {
        actions.push(Action::OpenProject(dir));
        if initial.auto_run {
            actions.push(Action::Run);
        }
    } else if state.project.is_none() {
        state.tab = MainTab::Library;
    }
}

fn refresh_library(state: &mut StudioState) {
    if !state.workspace.is_dir() {
        let _ = fs::create_dir_all(&state.workspace);
    }
    state.library = project::scan_workspace(&state.workspace);
}

fn open_project(state: &mut StudioState, dir: PathBuf) {
    match StudioProject::open(&dir) {
        Ok(project) => {
            state.set_project(project);
            state.tab = MainTab::Studio;
            state.dirty = false;
            state.push_log(LogKind::Info, format!("projeto aberto: {}", dir.display()));
        }
        Err(err) => state.toast(ToastKind::Error, err.to_string()),
    }
}

fn save_project(state: &StudioState) -> Result<(), String> {
    let Some(project) = state.project.as_ref() else {
        return Err("sem projeto".into());
    };
    project.save_config().map_err(|err| err.to_string())
}

fn copy_reference(project: &StudioProject, reference: &Path) -> Result<(), String> {
    if !reference.is_file() {
        return Err(format!("referência ausente: {}", reference.display()));
    }
    let dest = project.image_path();
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("criar {}: {err}", parent.display()))?;
    }
    let is_png = reference
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("png"));
    if is_png {
        fs::copy(reference, &dest).map_err(|err| format!("copiar referência: {err}"))?;
    } else {
        let image = image::open(reference).map_err(|err| format!("ler referência: {err}"))?;
        image
            .save(&dest)
            .map_err(|err| format!("escrever {}: {err}", dest.display()))?;
    }
    Ok(())
}

fn new_project(state: &mut StudioState, request: CreateRequest) {
    if request.config.asset.idea.trim().is_empty() {
        state.toast(ToastKind::Error, "prompt vazio");
        return;
    }
    if request.config.has_errors() {
        let issues = request
            .config
            .validate()
            .into_iter()
            .filter(|issue| issue.level == studio_core::config::IssueLevel::Error)
            .map(|issue| issue.message)
            .collect::<Vec<_>>()
            .join("; ");
        state.toast(ToastKind::Error, issues);
        return;
    }
    let reference = request.reference.clone();
    match StudioProject::create(request.dir.clone(), request.config) {
        Ok(project) => {
            if let Some(reference) = reference {
                if let Err(err) = copy_reference(&project, &reference) {
                    state.toast(ToastKind::Warn, err);
                }
            }
            state.set_project(project);
            state.tab = MainTab::Studio;
            state.dirty = false;
            refresh_library(state);
            state.toast(ToastKind::Ok, format!("projeto {}", request.dir.display()));
        }
        Err(err) => state.toast(ToastKind::Error, err.to_string()),
    }
}

fn delete_project(state: &mut StudioState, dir: PathBuf) {
    let trash = state.workspace.join(".trash");
    let _ = fs::create_dir_all(&trash);
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
    let name = dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("projeto");
    let dest = trash.join(format!("{stamp}-{name}"));
    match fs::rename(&dir, &dest) {
        Ok(()) => {
            if state
                .project
                .as_ref()
                .is_some_and(|project| project.dir == dir)
            {
                state.project = None;
                state.check = None;
                state.tab = MainTab::Library;
            }
            refresh_library(state);
            state.toast(ToastKind::Ok, format!("movido para {}", dest.display()));
        }
        Err(err) => state.toast(ToastKind::Error, format!("apagar: {err}")),
    }
}

fn start_check(state: &StudioState, worker: &mut CheckWorker) {
    if worker.in_flight {
        return;
    }
    let Some(project) = state.project.clone() else {
        return;
    };
    let spec = state.cli.studio_check(&project);
    let (tx, rx) = bounded(1);
    std::thread::spawn(move || {
        let started = Instant::now();
        let result = match run_capture(&spec) {
            Ok(capture) if capture.success => {
                let raw = capture.stdout.clone();
                CheckPayload::parse(&capture.stdout)
                    .map(|payload| (payload, raw))
                    .map_err(|err| format!("json: {err}"))
            }
            Ok(capture) => Err(capture.message()),
            Err(err) => Err(format!("spawn {}: {err}", spec.program)),
        };
        let (result, raw) = match result {
            Ok((payload, raw)) => (Ok(payload), raw),
            Err(err) => (Err(err), String::new()),
        };
        let _ = tx.send(CheckResponse {
            result,
            raw,
            at: started,
        });
    });
    worker.rx = Some(rx);
    worker.in_flight = true;
}

fn start_run(state: &mut StudioState) {
    let Some(project) = state.project.clone() else {
        state.toast(ToastKind::Warn, "sem projeto");
        return;
    };
    if state.run.is_busy() {
        state.toast(ToastKind::Warn, "já há um run ativo");
        return;
    }
    if state.check.is_none() {
        state.pending_run = true;
        return;
    }
    state.pending_run = false;
    if let Some(lock) = state.lock_live() {
        state.toast(ToastKind::Warn, format!("lock ativo (pid {:?})", lock.pid));
        return;
    }
    if project.config.has_errors() {
        state.toast(
            ToastKind::Error,
            "config inválida; aplica/verifica primeiro",
        );
        return;
    }
    let reserve = state
        .cli
        .resume(&project, state.gpu, project.config.run.fail_fast);
    let (tx, rx) = bounded(8192);
    state.push_log(LogKind::Tool, format!("$ {}", reserve.spec.display()));
    match runner::spawn(reserve.spec, tx) {
        Ok(handle) => {
            let stage = state
                .check
                .as_ref()
                .map(|check| check.next_stage().to_string());
            state.run = RunState {
                handle: Some(handle),
                rx: Some(rx),
                log_path: Some(reserve.log_path),
                started: Some(Instant::now()),
                active_stage: stage,
                running: true,
                ..Default::default()
            };
            state.stage_errors.clear();
            state.preview_override = None;
        }
        Err(err) => state.toast(ToastKind::Error, format!("spawn: {err}")),
    }
}

fn start_job(worker: &mut JobWorker, state: &mut StudioState, kind: JobKind, spec: CmdSpec) {
    if worker.in_flight {
        state.toast(ToastKind::Warn, "já há um job a correr");
        return;
    }
    let label = spec.display();
    let (tx, rx) = bounded(1);
    std::thread::spawn(move || {
        let capture = run_capture(&spec).map_err(|err| err.to_string());
        let _ = tx.send(JobResponse { kind, capture });
    });
    worker.rx = Some(rx);
    worker.in_flight = true;
    worker.label = label.clone();
    state.push_log(LogKind::Tool, format!("$ {label}"));
}

fn start_vramd(state: &StudioState, worker: &mut VramdWorker) {
    if worker.in_flight {
        return;
    }
    let cwd = state
        .project_dir()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| state.workspace.clone());
    let spec = state.cli.vramd_queue(&cwd);
    let (tx, rx) = bounded(1);
    std::thread::spawn(move || {
        let result = match run_capture(&spec) {
            Ok(capture) if capture.success => parse_vramd(&capture.stdout),
            Ok(capture) => Err(capture.message()),
            Err(err) => Err(format!("spawn vramd: {err}")),
        };
        let _ = tx.send(result);
    });
    worker.rx = Some(rx);
    worker.in_flight = true;
    worker.last_poll = Some(Instant::now());
}

fn parse_vramd(text: &str) -> Result<(usize, usize, Option<String>), String> {
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|err| format!("vramd json: {err}"))?;
    let running = value
        .get("running")
        .and_then(|running| running.as_array())
        .map(Vec::len)
        .unwrap_or(0);
    let queued = value
        .get("queued")
        .and_then(|queued| queued.as_array())
        .map(Vec::len)
        .unwrap_or(0);
    let job = value
        .get("running")
        .and_then(|running| running.as_array())
        .and_then(|running| running.first())
        .map(|job| {
            let backend = job
                .get("backend")
                .and_then(|backend| backend.as_str())
                .unwrap_or("?");
            let pct = job
                .get("progress_pct")
                .and_then(|pct| pct.as_f64())
                .unwrap_or(0.0);
            let message = job
                .get("progress_msg")
                .and_then(|msg| msg.as_str())
                .unwrap_or("");
            format!("{backend} {pct:.0}% {message}")
        });
    Ok((running, queued, job))
}

fn sync_preview(
    state: Res<StudioState>,
    preview: Res<PreviewState>,
    mut sync: ResMut<PreviewSync>,
    mut command: ResMut<PreviewCommand>,
) {
    if state.preview_override.is_some() {
        return;
    }
    if sync.generation == state.check_generation && sync.selected == state.selected {
        return;
    }
    sync.generation = state.check_generation;
    sync.selected = state.selected.clone();
    if state.selected == "image" {
        return;
    }
    let Some(path) = state.artifact_path(&state.selected) else {
        return;
    };
    if preview
        .loaded
        .as_ref()
        .is_some_and(|loaded| loaded.source == path)
        && preview.pending_decode.is_none()
        && preview.pending_scene.is_none()
    {
        return;
    }
    command.load = Some(LoadRequest {
        source: path,
        artifact: state.selected.clone(),
        clip: None,
    });
}

fn auto_thumbnail(
    state: Res<StudioState>,
    preview: Res<PreviewState>,
    thumbs: Res<ThumbnailState>,
    mut request: ResMut<ThumbnailRequest>,
    mut ui: ResMut<UiState>,
) {
    if state.run.is_busy() || request.source.is_some() || thumbs.active.is_some() {
        return;
    }
    if let Some(last) = thumbs.last_attempt {
        if last.elapsed().as_secs() < 10 {
            return;
        }
    }
    let Some(project) = state.project.as_ref() else {
        return;
    };
    let Some(loaded) = preview.loaded.as_ref() else {
        return;
    };
    if !matches!(
        loaded.artifact.as_str(),
        "lod0" | "painted" | "animated" | "rigged"
    ) {
        return;
    }
    let cover = project.cover_path();
    let source_time = fs::metadata(&loaded.source)
        .and_then(|meta| meta.modified())
        .ok();
    let cover_time = fs::metadata(&cover).and_then(|meta| meta.modified()).ok();
    let force = ui.force_thumb;
    if !force {
        if let (Some(source_time), Some(cover_time)) = (source_time, cover_time) {
            if cover_time >= source_time {
                return;
            }
        }
    }
    request.source = Some(loaded.source.clone());
    request.out = Some(cover);
    ui.force_thumb = false;
}

fn expire_toasts(mut state: ResMut<StudioState>) {
    let now = Instant::now();
    state.toasts.retain(|toast| toast.until > now);
}

fn keyboard_shortcuts(
    keys: Res<ButtonInput<KeyCode>>,
    ui: Res<UiState>,
    mut actions: ResMut<PendingActions>,
) {
    if ui.egui_wants_keyboard || ui.show_new_project || ui.confirm_delete.is_some() {
        return;
    }
    let ctrl = keys.pressed(KeyCode::ControlLeft) || keys.pressed(KeyCode::ControlRight);
    if ctrl && keys.just_pressed(KeyCode::Enter) {
        actions.push(Action::Run);
    }
    if keys.just_pressed(KeyCode::Escape) {
        actions.push(Action::Cancel);
    }
    if keys.just_pressed(KeyCode::KeyF) {
        actions.push(Action::Frame);
    }
    if keys.just_pressed(KeyCode::Space) {
        actions.push(Action::TogglePlay);
    }
}

fn execute_actions(
    mut state: ResMut<StudioState>,
    mut actions: ResMut<PendingActions>,
    mut check_worker: ResMut<CheckWorker>,
    mut job_worker: ResMut<JobWorker>,
    mut preview_command: ResMut<PreviewCommand>,
    mut ui: ResMut<UiState>,
) {
    let mut budget = 32;
    while budget > 0 {
        budget -= 1;
        let Some(action) = actions.next() else {
            break;
        };
        match action {
            Action::Run => {
                if state.check.is_none() {
                    state.pending_run = true;
                    start_check(&state, &mut check_worker);
                } else {
                    start_run(&mut state);
                }
            }
            Action::Cancel => {
                if let Some(handle) = state.run.handle.as_ref() {
                    handle.cancel();
                    state.run.cancelled = true;
                    state.toast(ToastKind::Warn, "cancelamento pedido");
                }
            }
            Action::Reroll(stage) => {
                let run_after = true;
                reroll_stage(&mut state, &mut check_worker, stage, run_after);
            }
            Action::CompareHistory(stage) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                let Some(entry) = studio_core::reroll::list_history(&project, stage)
                    .into_iter()
                    .next()
                else {
                    state.toast(ToastKind::Info, "sem histórico para esta etapa");
                    continue;
                };
                let path = studio_core::reroll::history_file(&entry, &state.selected)
                    .map(Path::to_path_buf)
                    .or_else(|| {
                        entry
                            .files
                            .iter()
                            .find(|path| {
                                path.extension()
                                    .and_then(|ext| ext.to_str())
                                    .is_some_and(|ext| ext.eq_ignore_ascii_case("glb"))
                            })
                            .cloned()
                    });
                let Some(path) = path else {
                    state.toast(ToastKind::Warn, "histórico sem GLB");
                    continue;
                };
                state.preview_override = Some(format!("history:{}", stage.key()));
                preview_command.load = Some(LoadRequest {
                    source: path,
                    artifact: format!("history:{}", stage.key()),
                    clip: None,
                });
            }
            Action::ReloadPreview => {
                state.preview_override = None;
                if let Some(path) = state.artifact_path(&state.selected) {
                    preview_command.load = Some(LoadRequest {
                        source: path,
                        artifact: state.selected.clone(),
                        clip: None,
                    });
                } else {
                    preview_command.clear = true;
                }
            }
            Action::InvalidateFrom(stage) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                let Some(check) = state.check.clone() else {
                    state.toast(ToastKind::Warn, "sem estado; a atualizar");
                    continue;
                };
                match reroll::invalidate(&project, &check, stage, false, &state.cli) {
                    Ok(outcome) => {
                        state.push_log(
                            LogKind::Info,
                            format!(
                                "invalidate {}: {} ficheiro(s)",
                                stage.key(),
                                outcome.deleted.len()
                            ),
                        );
                        state.stage_errors.remove(stage.key());
                        start_check(&state, &mut check_worker);
                    }
                    Err(err) => state.toast(ToastKind::Error, err.to_string()),
                }
            }
            Action::Reset => reroll_stage(&mut state, &mut check_worker, Stage::Image, true),
            Action::RefreshCheck => start_check(&state, &mut check_worker),
            Action::SaveConfig => match save_project(&state) {
                Ok(()) => {
                    state.dirty = false;
                    state.toast(ToastKind::Ok, "gravado");
                    if let Some(stage) = ui.invalidate_after_save.take() {
                        let Some(project) = state.project.clone() else {
                            continue;
                        };
                        let Some(check) = state.check.clone() else {
                            start_check(&state, &mut check_worker);
                            continue;
                        };
                        match reroll::invalidate(&project, &check, stage, false, &state.cli) {
                            Ok(outcome) => state.push_log(
                                LogKind::Info,
                                format!(
                                    "etapa {} desligada: {} derivado(s) apagado(s)",
                                    stage.key(),
                                    outcome.deleted.len()
                                ),
                            ),
                            Err(err) => state.toast(ToastKind::Error, err.to_string()),
                        }
                    }
                    start_check(&state, &mut check_worker);
                }
                Err(err) => state.toast(ToastKind::Error, err),
            },
            Action::RevertConfig => {
                if let Some(project) = state.project.as_mut() {
                    match project.reload_config() {
                        Ok(()) => {
                            state.gpu = project.config.run.gpu;
                            state.dirty = false;
                            state.check_generation += 1;
                            state.toast(ToastKind::Info, "revertido");
                        }
                        Err(err) => state.toast(ToastKind::Error, err.to_string()),
                    }
                }
            }
            Action::OpenProject(dir) => open_project(&mut state, dir),
            Action::NewProject(request) => new_project(&mut state, request),
            Action::CloseProject => {
                state.project = None;
                state.check = None;
                state.tab = MainTab::Library;
                state.preview_override = None;
                preview_command.clear = true;
                refresh_library(&mut state);
            }
            Action::DeleteProject(dir) => {
                ui.confirm_delete = Some(dir);
            }
            Action::ConfirmDelete(dir) => delete_project(&mut state, dir),
            Action::SetGpu(mode) => {
                state.gpu = mode;
                if let Some(project) = state.project.as_mut() {
                    project.config.run.gpu = mode;
                }
                state.dirty = true;
                if let Err(err) = save_project(&state) {
                    state.toast(ToastKind::Error, err);
                } else {
                    state.dirty = false;
                }
            }
            Action::SelectArtifact(key) => {
                state.selected = key;
                state.preview_override = None;
            }
            Action::Frame => preview_command.frame = true,
            Action::TogglePlay => preview_command.toggle_play = true,
            Action::SetClip(name) => preview_command.set_clip = Some(name),
            Action::SetSpeed(speed) => preview_command.speed = Some(speed),
            Action::GenerateMotion(request) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                let spec = state.cli.motion_generate(&project, &request);
                start_job(
                    &mut job_worker,
                    &mut state,
                    JobKind::MotionGenerate {
                        request,
                        project: project.dir.clone(),
                    },
                    spec,
                );
            }
            Action::PreviewMotion(name) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                let rigged = project.rigged_path();
                let npz = project.motion_npz(&name);
                if !rigged.is_file() || !npz.is_file() {
                    state.toast(ToastKind::Warn, "rig ou clip em falta");
                    continue;
                }
                let out = motion::preview_path(&project, &name);
                let spec = state.cli.motion_apply(&project, &npz, &rigged, &out, &name);
                start_job(
                    &mut job_worker,
                    &mut state,
                    JobKind::MotionPreview {
                        name: name.clone(),
                        project: project.dir.clone(),
                    },
                    spec,
                );
            }
            Action::PackMotions(names) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                if !project
                    .config
                    .pipeline
                    .has(studio_core::config::PipelineStage::Animate)
                {
                    state.toast(
                        ToastKind::Warn,
                        "ativa a etapa Animação no pipeline para empacotar",
                    );
                    continue;
                }
                if names.is_empty() {
                    state.toast(ToastKind::Warn, "seleciona pelo menos um clip");
                    continue;
                }
                if !project.rigged_path().is_file() {
                    state.toast(ToastKind::Warn, "rig em falta");
                    continue;
                }
                let motions: Vec<(String, PathBuf)> = names
                    .iter()
                    .map(|name| (name.clone(), project.motion_npz(name)))
                    .collect();
                let active = names[0].clone();
                let out = project.animated_path();
                let spec = state.cli.motion_pack(
                    &project,
                    &project.rigged_path(),
                    &out,
                    &motions,
                    &active,
                );
                start_job(
                    &mut job_worker,
                    &mut state,
                    JobKind::MotionPack {
                        active,
                        motions,
                        project: project.dir.clone(),
                    },
                    spec,
                );
            }
            Action::DeleteMotion(name) => {
                if let Some(project) = state.project.as_ref() {
                    if let Err(err) = motion::delete_clip(project, &name) {
                        state.toast(ToastKind::Error, err.to_string());
                    } else {
                        state.clips = motion::load_clips(project);
                        state.toast(ToastKind::Ok, format!("clip {name} apagado"));
                    }
                }
            }
            Action::Doctor => {
                let cwd = state
                    .project_dir()
                    .map(Path::to_path_buf)
                    .unwrap_or_else(|| state.workspace.clone());
                let spec = state.cli.doctor(&cwd);
                start_job(&mut job_worker, &mut state, JobKind::Doctor, spec);
            }
            Action::BakeThumbnail => {
                ui.force_thumb = true;
            }
            Action::RefreshLibrary => refresh_library(&mut state),
            Action::SetReference(path) => {
                let Some(project) = state.project.clone() else {
                    continue;
                };
                match copy_reference(&project, &path) {
                    Ok(()) => {
                        state.check_generation += 1;
                        state.toast(
                            ToastKind::Ok,
                            "referência copiada; usa Reset/Re-roll Shape para aplicar",
                        );
                    }
                    Err(err) => state.toast(ToastKind::Error, err),
                }
            }
        }
    }
}

fn reroll_stage(
    state: &mut StudioState,
    check_worker: &mut CheckWorker,
    stage: Stage,
    run_after: bool,
) {
    let Some(project) = state.project.clone() else {
        return;
    };
    let Some(check) = state.check.clone() else {
        state.toast(ToastKind::Warn, "sem estado; a atualizar");
        start_check(state, check_worker);
        return;
    };
    match reroll::reroll(&project, &check, stage, &state.cli) {
        Ok(outcome) => {
            state.push_log(
                LogKind::Info,
                format!(
                    "re-roll {}: {} → history, {} apagado(s)",
                    stage.key(),
                    outcome.history.len(),
                    outcome.invalidate.deleted.len()
                ),
            );
            if !outcome.invalidate.failed.is_empty() {
                state.push_log(
                    LogKind::Warn,
                    format!(
                        "{} ficheiro(s) não apagados",
                        outcome.invalidate.failed.len()
                    ),
                );
            }
            state.stage_errors.remove(stage.key());
            if outcome.invalidate.used_fallback {
                state.push_log(
                    LogKind::Warn,
                    "gameassets sem --include-stage; apagado pelo Studio",
                );
            }
            if run_after {
                start_run(state);
            } else {
                start_check(state, check_worker);
            }
        }
        Err(err) => state.toast(ToastKind::Error, err.to_string()),
    }
}

fn poll_check_worker(mut state: ResMut<StudioState>, mut worker: ResMut<CheckWorker>) {
    let received = worker.rx.as_ref().map(|rx| rx.try_recv());
    match received {
        Some(Ok(response)) => {
            worker.in_flight = false;
            worker.rx = None;
            state.last_check = Some(response.at);
            state.check_generation += 1;
            match response.result {
                Ok(payload) => {
                    state.check = Some(payload);
                    state.check_error = None;
                    state.select_default_artifact();
                    if state.pending_run && !state.run.is_busy() {
                        state.pending_run = false;
                        if state.lock_live().is_none() {
                            start_run(&mut state);
                        } else {
                            state.toast(ToastKind::Warn, "lock ativo; o run fica para depois");
                        }
                    }
                }
                Err(err) => {
                    state.check_error = Some(err.clone());
                    if !state.run.is_busy() {
                        state.push_log(LogKind::Warn, format!("check: {err}"));
                    }
                }
            }
            if !response.raw.is_empty() {
                // mantém o raw acessível no log de debug
                tracing::trace!("studio check: {}", response.raw.len());
            }
        }
        Some(Err(TryRecvError::Disconnected)) => {
            worker.in_flight = false;
            worker.rx = None;
            state.check_error = Some("check interrompido".into());
        }
        Some(Err(TryRecvError::Empty)) | None => {}
    }
}

fn poll_job_worker(
    mut state: ResMut<StudioState>,
    mut worker: ResMut<JobWorker>,
    mut actions: ResMut<PendingActions>,
    mut preview_command: ResMut<PreviewCommand>,
) {
    let received = worker.rx.as_ref().map(|rx| rx.try_recv());
    let Some(Ok(response)) = received else {
        if let Some(Err(TryRecvError::Disconnected)) = received {
            worker.in_flight = false;
            worker.rx = None;
            state.toast(ToastKind::Error, "job interrompido");
        }
        return;
    };
    worker.in_flight = false;
    worker.rx = None;
    let capture = match response.capture {
        Ok(capture) => capture,
        Err(err) => {
            state.toast(ToastKind::Error, err);
            return;
        }
    };
    if !capture.success {
        state.toast(ToastKind::Error, capture.message());
        return;
    }

    match response.kind {
        JobKind::MotionGenerate { request, project } => {
            let result = motion::parse_generate_result(&capture.stdout);
            let clip = motion::clip_from_request(&request, result.as_ref());
            let Some(current) = state.project.as_ref() else {
                return;
            };
            if current.dir != project {
                return;
            }
            match motion::save_clip(current, &clip) {
                Ok(()) => {
                    state.clips = motion::load_clips(current);
                    state.toast(
                        ToastKind::Ok,
                        format!(
                            "clip {} (score {:.3})",
                            clip.name,
                            clip.score.unwrap_or(0.0)
                        ),
                    );
                }
                Err(err) => state.toast(ToastKind::Error, err.to_string()),
            }
        }
        JobKind::MotionPreview { name, project } => {
            let Some(current) = state.project.as_ref() else {
                return;
            };
            if current.dir != project {
                return;
            }
            let out = motion::preview_path(current, &name);
            state.preview_override = Some(name.clone());
            preview_command.load = Some(LoadRequest {
                source: out,
                artifact: format!("motion:{name}"),
                clip: Some(name),
            });
        }
        JobKind::MotionPack {
            active,
            motions,
            project,
        } => {
            state.push_log(
                LogKind::Ok,
                format!("pack: {} clip(s), ativo {active}", motions.len()),
            );
            let Some(current) = state.project.as_ref() else {
                return;
            };
            if current.dir != project {
                return;
            }
            actions.push(Action::Reroll(Stage::Lod));
        }
        JobKind::Doctor => {
            state.push_log(LogKind::Tool, capture.stdout.trim().to_string());
            state.toast(ToastKind::Ok, "doctor ok");
        }
    }
}

fn poll_vramd_worker(mut state: ResMut<StudioState>, mut worker: ResMut<VramdWorker>) {
    let received = worker.rx.as_ref().map(|rx| rx.try_recv());
    match received {
        Some(Ok(Ok((running, queued, job)))) => {
            worker.in_flight = false;
            worker.rx = None;
            state.vramd_running = running;
            state.vramd_queued = queued;
            state.vramd_job = job;
            state.vramd_error = None;
        }
        Some(Ok(Err(err))) => {
            worker.in_flight = false;
            worker.rx = None;
            state.vramd_error = Some(err);
        }
        Some(Err(_)) => {
            worker.in_flight = false;
            worker.rx = None;
        }
        None => {}
    }
}

fn auto_check(state: Res<StudioState>, mut worker: ResMut<CheckWorker>) {
    if state.project.is_none() || worker.in_flight {
        return;
    }
    let interval = if state.run.is_busy() { 2.0 } else { 8.0 };
    let due = state
        .last_check
        .map(|last| last.elapsed().as_secs_f32() > interval)
        .unwrap_or(true);
    if due {
        start_check(&state, &mut worker);
    }
}

fn auto_vramd(state: Res<StudioState>, mut worker: ResMut<VramdWorker>) {
    if !state.run.is_busy() || worker.in_flight {
        return;
    }
    let due = worker
        .last_poll
        .map(|last| last.elapsed().as_secs_f32() > 1.5)
        .unwrap_or(true);
    if due {
        start_vramd(&state, &mut worker);
    }
}

fn pump_run_events(mut state: ResMut<StudioState>, mut actions: ResMut<PendingActions>) {
    let Some(rx) = state.run.rx.take() else {
        return;
    };
    let mut finished = false;
    while let Ok(event) = rx.try_recv() {
        match event {
            PipelineEvent::Started { command } => {
                state.run.running = true;
                state.push_log(LogKind::Tool, format!("▶ {command}"));
            }
            PipelineEvent::Line { stream, line } => {
                if let Some(phase) = parse_phase(&line) {
                    state.run.phase = Some(phase);
                }
                let kind = match stream {
                    Stream::Stdout => LogKind::Tool,
                    Stream::Stderr => LogKind::Warn,
                };
                state.push_log(kind, line);
            }
            PipelineEvent::Progress(progress) => {
                state.run.active_stage = Some(progress.stage_name().to_string());
                state.run.active_id = progress.id.clone();
                state.run.phase = progress.phase.clone();
                state.run.percent = progress.percent;
                if progress.is_start() {
                    state.push_log(
                        LogKind::Progress,
                        format!("· {} · {}", progress.stage_name(), progress.id),
                    );
                } else if progress.is_finish() {
                    state.push_log(
                        LogKind::Ok,
                        format!(
                            "✓ {} · {} ({:.1}s)",
                            progress.stage_name(),
                            progress.id,
                            progress.seconds.unwrap_or(0.0)
                        ),
                    );
                }
            }
            PipelineEvent::Finished {
                code,
                success,
                cancelled,
                duration,
            } => {
                finished = true;
                state.run.running = false;
                state.run.handle = None;
                state.run.started = None;
                state.run.percent = None;
                state.run.phase = None;
                if cancelled {
                    state.toast(ToastKind::Warn, "run cancelado");
                } else if success {
                    state.toast(
                        ToastKind::Ok,
                        format!("run ok ({:.1}s)", duration.as_secs_f64()),
                    );
                } else {
                    state.run.last_error = Some(format!("exit {code:?}"));
                    state.toast(
                        ToastKind::Error,
                        format!("run falhou (exit {code:?}, {:.1}s)", duration.as_secs_f64()),
                    );
                }
                read_run_log(&mut state);
                actions.push(Action::RefreshCheck);
            }
            PipelineEvent::SpawnError(err) => {
                state.push_log(LogKind::Error, format!("spawn: {err}"));
            }
        }
    }
    if !finished {
        state.run.rx = Some(rx);
    } else {
        state.run.rx = None;
    }
}

fn parse_phase(line: &str) -> Option<String> {
    let index = line.find("Fase ")?;
    let rest = &line[index + 5..];
    let after_colon = rest.split_once(':').map(|(_, tail)| tail).unwrap_or(rest);
    let name = after_colon.split('(').next().unwrap_or(after_colon).trim();
    (!name.is_empty()).then(|| name.to_string())
}

fn read_run_log(state: &mut StudioState) {
    let Some(path) = state.run.log_path.clone() else {
        return;
    };
    let Ok(text) = fs::read_to_string(&path) else {
        return;
    };
    let records = parse_run_log(&text);
    state.timings = stage_timings(&records);
    for failure in stage_failures(&records) {
        let key = normalise_stage(&failure.stage);
        state.stage_errors.insert(key, failure.error);
    }
    if let Some(record) = records.last() {
        if let Some(seconds) = record.total_elapsed_s {
            state.push_log(LogKind::Info, format!("log: {seconds:.1}s total"));
        }
    }
}

fn normalise_stage(name: &str) -> String {
    match name {
        "topology_fix" | "topology-fix" => "clean".into(),
        "validate_lod0" | "validate-lod0" | "finish" => "lod".into(),
        other => other.replace('-', "_"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phase_lines_are_detected() {
        assert_eq!(
            parse_phase("Fase 2: Shape (1 meshes)").as_deref(),
            Some("Shape")
        );
        assert_eq!(
            parse_phase("Fase 3.6: Master pipeline (1)").as_deref(),
            Some("Master pipeline")
        );
        assert_eq!(parse_phase("nada"), None);
    }

    #[test]
    fn vramd_queue_is_tolerant() {
        let (running, queued, job) = parse_vramd(
            r#"{"running":[{"backend":"text3d","progress_pct":42.0,"progress_msg":"inference"}],"queued":[{}]}"#,
        )
        .expect("json");
        assert_eq!(running, 1);
        assert_eq!(queued, 1);
        assert!(job.unwrap().contains("text3d 42%"));
    }

    #[test]
    fn stage_names_are_normalised() {
        assert_eq!(normalise_stage("topology-fix"), "clean");
        assert_eq!(normalise_stage("validate-lod0"), "lod");
        assert_eq!(normalise_stage("to_paint"), "to_paint");
    }
}
