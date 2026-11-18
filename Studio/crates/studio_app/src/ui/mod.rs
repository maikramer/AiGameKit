//! UI egui do Studio.

pub mod bottom;
pub mod left;
pub mod library;
pub mod right;

use std::{path::Path, time::Duration};

use bevy::prelude::*;
use bevy::window::FileDragAndDrop;
use bevy_egui::{EguiContexts, EguiPrimaryContextPass, egui};

use crate::{
    preview::{PreviewCommand, PreviewState, ViewportInfo},
    state::*,
    theme,
};

pub struct UiPlugin;

impl Plugin for UiPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(EguiPrimaryContextPass, draw_ui)
            .add_systems(Update, handle_file_drop);
    }
}

pub const ARTIFACTS: [(&str, &str); 12] = [
    ("image", "Imagem"),
    ("shape", "Shape"),
    ("clean", "Clean"),
    ("refined", "Hi3D"),
    ("to_paint", "ToPaint"),
    ("painted", "Pintura"),
    ("rigged", "Rig"),
    ("animated", "Animado"),
    ("lod0", "LOD0"),
    ("lod1", "LOD1"),
    ("lod2", "LOD2"),
    ("collision", "Colisão"),
];

pub struct UiCtx<'a> {
    pub state: &'a mut StudioState,
    pub actions: &'a mut PendingActions,
    pub ui: &'a mut UiState,
    pub new_form: &'a mut NewProjectForm,
    pub motion_form: &'a mut MotionForm,
    pub library: &'a mut LibraryState,
    pub textures: &'a mut TextureCache,
    pub preview: &'a PreviewState,
    pub job_running: bool,
    pub check_in_flight: bool,
}

fn handle_file_drop(
    mut drops: MessageReader<FileDragAndDrop>,
    state: Res<StudioState>,
    mut actions: ResMut<PendingActions>,
    mut new_form: ResMut<NewProjectForm>,
    mut ui: ResMut<UiState>,
) {
    for drop in drops.read() {
        if let FileDragAndDrop::DroppedFile { path_buf, .. } = drop {
            let is_image = path_buf
                .extension()
                .and_then(|ext| ext.to_str())
                .is_some_and(|ext| {
                    matches!(
                        ext.to_ascii_lowercase().as_str(),
                        "png" | "jpg" | "jpeg" | "webp" | "bmp"
                    )
                });
            if is_image {
                if state.project.is_some() {
                    actions.push(Action::SetReference(path_buf.clone()));
                } else {
                    new_form.reference = Some(path_buf.clone());
                    new_form.sync_id();
                    ui.show_new_project = true;
                }
                continue;
            }
            if path_buf.is_dir() {
                actions.push(Action::OpenProject(path_buf.clone()));
            } else if path_buf.file_name().and_then(|n| n.to_str()) == Some("studio.toml") {
                if let Some(parent) = path_buf.parent() {
                    actions.push(Action::OpenProject(parent.to_path_buf()));
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_ui(
    mut contexts: EguiContexts,
    mut state: ResMut<StudioState>,
    mut actions: ResMut<PendingActions>,
    mut ui_state: ResMut<UiState>,
    mut new_form: ResMut<NewProjectForm>,
    mut motion_form: ResMut<MotionForm>,
    mut library: ResMut<LibraryState>,
    mut textures: ResMut<TextureCache>,
    mut viewport: ResMut<ViewportInfo>,
    mut preview_command: ResMut<PreviewCommand>,
    preview: Res<PreviewState>,
    job_worker: Res<JobWorker>,
    check_worker: Res<CheckWorker>,
    mut themed: Local<bool>,
) -> Result {
    let ctx = contexts.ctx_mut()?;
    if !*themed {
        theme::apply(ctx);
        *themed = true;
    }
    let mut root = egui::Ui::new(
        (*ctx).clone(),
        egui::Id::new("studio_root"),
        egui::UiBuilder::new(),
    );

    let mut cx = UiCtx {
        state: &mut state,
        actions: &mut actions,
        ui: &mut ui_state,
        new_form: &mut new_form,
        motion_form: &mut motion_form,
        library: &mut library,
        textures: &mut textures,
        preview: &preview,
        job_running: job_worker.in_flight,
        check_in_flight: check_worker.in_flight,
    };

    top_bar(&mut root, &mut cx, &mut preview_command);

    let studio_area = cx.state.tab == MainTab::Studio && cx.state.project.is_some();
    match cx.state.tab {
        MainTab::Library => {
            egui::CentralPanel::default()
                .frame(
                    egui::Frame::new()
                        .fill(theme::BG)
                        .inner_margin(egui::Margin::same(14)),
                )
                .show(&mut root, |ui| library::draw(ui, &mut cx));
        }
        MainTab::Studio => {
            if cx.state.project.is_some() {
                egui::Panel::left("studio_left")
                    .resizable(true)
                    .size_range(300.0..=560.0)
                    .default_size(360.0)
                    .frame(
                        egui::Frame::new()
                            .fill(theme::PANEL)
                            .inner_margin(egui::Margin::symmetric(10, 10)),
                    )
                    .show(&mut root, |ui| {
                        egui::ScrollArea::vertical()
                            .auto_shrink([false, false])
                            .show(ui, |ui| left::draw(ui, &mut cx));
                    });
                egui::Panel::right("studio_right")
                    .resizable(true)
                    .size_range(320.0..=620.0)
                    .default_size(430.0)
                    .frame(
                        egui::Frame::new()
                            .fill(theme::PANEL)
                            .inner_margin(egui::Margin::symmetric(10, 10)),
                    )
                    .show(&mut root, |ui| {
                        egui::ScrollArea::vertical()
                            .auto_shrink([false, false])
                            .show(ui, |ui| right::draw(ui, &mut cx));
                    });
                egui::Panel::bottom("studio_bottom")
                    .resizable(true)
                    .size_range(120.0..=520.0)
                    .default_size(220.0)
                    .frame(
                        egui::Frame::new()
                            .fill(theme::PANEL)
                            .inner_margin(egui::Margin::symmetric(10, 6)),
                    )
                    .show(&mut root, |ui| bottom::draw(ui, &mut cx));
            } else {
                egui::CentralPanel::default()
                    .frame(
                        egui::Frame::new()
                            .fill(theme::BG)
                            .inner_margin(egui::Margin::same(14)),
                    )
                    .show(&mut root, |ui| welcome(ui, &mut cx));
            }
        }
    }

    if cx.ui.show_new_project {
        new_project_dialog(ctx, &mut cx);
    }
    if cx.ui.confirm_delete.is_some() {
        confirm_delete_dialog(ctx, &mut cx);
    }
    toasts(ctx, &mut cx);

    if studio_area {
        let rect = root.available_rect_before_wrap();
        let pointer = ctx.input(|input| input.pointer.hover_pos());
        viewport.rect = Some(rect);
        viewport.pointer_in_viewport = pointer.is_some_and(|pos| rect.contains(pos));
        viewport.egui_wants_pointer = ctx.egui_wants_pointer_input();
        viewport.egui_wants_keyboard = ctx.egui_wants_keyboard_input();
        viewport.mouse_pos = pointer;
        cx.ui.viewport = Some(rect);
        cx.ui.egui_wants_pointer = viewport.egui_wants_pointer;
        cx.ui.egui_wants_keyboard = viewport.egui_wants_keyboard;
        cx.ui.pointer_in_viewport = viewport.pointer_in_viewport;
        reference_preview(ctx, &mut cx, rect);
        viewport_hint(ctx, &mut cx, rect);
    } else {
        viewport.rect = None;
        viewport.pointer_in_viewport = false;
        viewport.egui_wants_pointer = true;
        viewport.egui_wants_keyboard = ctx.egui_wants_keyboard_input();
        viewport.mouse_pos = None;
        cx.ui.viewport = None;
        cx.ui.pointer_in_viewport = false;
        cx.ui.egui_wants_pointer = true;
        cx.ui.egui_wants_keyboard = viewport.egui_wants_keyboard;
    }

    if cx.state.run.is_busy() || !cx.state.toasts.is_empty() || cx.job_running || cx.check_in_flight
    {
        ctx.request_repaint_after(Duration::from_millis(120));
    } else {
        ctx.request_repaint_after(Duration::from_millis(600));
    }
    Ok(())
}

fn top_bar(root: &mut egui::Ui, cx: &mut UiCtx, preview_command: &mut PreviewCommand) {
    egui::Panel::top("studio_top")
        .frame(
            egui::Frame::new()
                .fill(theme::PANEL_HIGH)
                .inner_margin(egui::Margin::symmetric(10, 6)),
        )
        .show(root, |ui| {
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new("AiGameKit Studio")
                        .strong()
                        .size(15.0)
                        .color(theme::ACCENT),
                );
                ui.separator();
                if ui
                    .selectable_label(cx.state.tab == MainTab::Studio, "Editor")
                    .clicked()
                {
                    cx.state.tab = MainTab::Studio;
                }
                if ui
                    .selectable_label(cx.state.tab == MainTab::Library, "Biblioteca")
                    .clicked()
                {
                    cx.state.tab = MainTab::Library;
                    cx.actions.push(Action::RefreshLibrary);
                }
                ui.separator();
                if let Some(project) = cx.state.project.as_ref() {
                    ui.label(egui::RichText::new(project.id()).strong());
                }
                state_chip(ui, cx.state);

                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let cancel = ui.add_enabled(
                        cx.state.run.is_busy(),
                        egui::Button::new(egui::RichText::new("Cancelar").color(theme::ERROR)),
                    );
                    if cancel.clicked() {
                        cx.actions.push(Action::Cancel);
                    }
                    let run = ui.add_enabled(
                        cx.state.can_run(),
                        egui::Button::new(egui::RichText::new("Correr").strong().color(theme::BG))
                            .fill(theme::ACCENT),
                    );
                    if run.clicked() {
                        cx.actions.push(Action::Run);
                    }
                    ui.separator();
                    if ui
                        .add_enabled(cx.state.dirty, egui::Button::new("Aplicar"))
                        .clicked()
                    {
                        cx.actions.push(Action::SaveConfig);
                    }
                    if ui
                        .add_enabled(cx.state.dirty, egui::Button::new("Reverter"))
                        .clicked()
                    {
                        cx.actions.push(Action::RevertConfig);
                    }
                    if ui.button("Doctor").clicked() {
                        cx.actions.push(Action::Doctor);
                    }
                    ui.separator();
                    gpu_combo(ui, cx);
                    vramd_chip(ui, cx.state);
                    if cx.state.run.is_busy() || cx.job_running {
                        ui.spinner();
                    }
                });
            });
            if cx.state.tab == MainTab::Studio && cx.state.project.is_some() {
                ui.separator();
                artifact_bar(ui, cx, preview_command);
            }
        });
}

fn artifact_bar(ui: &mut egui::Ui, cx: &mut UiCtx, preview_command: &mut PreviewCommand) {
    ui.horizontal_wrapped(|ui| {
        for (key, label) in ARTIFACTS {
            let exists = cx
                .state
                .check
                .as_ref()
                .is_some_and(|check| check.has_artifact(key));
            let stale = matches!(key, "shape")
                && cx.state.stage_stale(studio_core::reroll::Stage::Shape)
                || matches!(key, "refined")
                    && cx.state.stage_stale(studio_core::reroll::Stage::Refine);
            let dot = if exists {
                if stale { "◐" } else { "●" }
            } else {
                "○"
            };
            let color = if exists {
                if stale { theme::WARN } else { theme::OK }
            } else {
                theme::MUTED
            };
            let text = egui::RichText::new(format!("{label} {dot}")).color(color);
            if ui
                .selectable_label(cx.state.selected == key, text)
                .clicked()
            {
                cx.actions.push(Action::SelectArtifact(key.to_string()));
            }
        }
        ui.separator();
        if let Some(override_view) = cx.state.preview_override.as_ref() {
            ui.label(theme::chip(
                if override_view.starts_with("history:") {
                    "histórico"
                } else {
                    "preview de movimento"
                },
                theme::WARN,
            ));
            if ui.button("Voltar ao atual").clicked() {
                cx.actions.push(Action::ReloadPreview);
            }
        }
        if ui.button("Enquadrar").clicked() {
            cx.actions.push(Action::Frame);
        }
        let mut turntable = cx.ui.turntable;
        if ui.checkbox(&mut turntable, "Turntable").changed() {
            cx.ui.turntable = turntable;
            preview_command.turntable = Some(turntable);
        }
        let mut wireframe = cx.ui.wireframe;
        if ui.checkbox(&mut wireframe, "Wireframe").changed() {
            cx.ui.wireframe = wireframe;
            preview_command.wireframe = Some(wireframe);
        }
        let mut grid = cx.ui.show_grid;
        if ui.checkbox(&mut grid, "Grid").changed() {
            cx.ui.show_grid = grid;
            preview_command.show_grid = Some(grid);
        }
        ui.separator();
        if let Some(loaded) = cx.preview.loaded.as_ref() {
            if !loaded.clips.is_empty() {
                let current = loaded
                    .active_clip
                    .and_then(|index| loaded.clips.get(index))
                    .cloned()
                    .unwrap_or_else(|| loaded.clips[0].clone());
                let mut selected = current.clone();
                egui::ComboBox::from_id_salt("clip_combo")
                    .selected_text(selected.clone())
                    .width(150.0)
                    .show_ui(ui, |ui| {
                        for clip in &loaded.clips {
                            ui.selectable_value(&mut selected, clip.clone(), clip);
                        }
                    });
                if selected != current {
                    cx.actions.push(Action::SetClip(selected));
                }
                let play_label = if cx.preview.playing { "⏸" } else { "▶" };
                if ui.button(play_label).clicked() {
                    cx.actions.push(Action::TogglePlay);
                }
                let mut speed = loaded.speed;
                if ui
                    .add(egui::Slider::new(&mut speed, 0.1..=3.0).text("×"))
                    .changed()
                {
                    cx.actions.push(Action::SetSpeed(speed));
                }
            }
        }
    });
}

fn gpu_combo(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let mut current = cx.state.gpu;
    egui::ComboBox::from_id_salt("gpu_combo")
        .selected_text(match current {
            studio_core::config::GpuMode::Auto => "GPU: auto",
            studio_core::config::GpuMode::Local => "GPU: local",
            studio_core::config::GpuMode::Remote => "GPU: remota",
        })
        .width(120.0)
        .show_ui(ui, |ui| {
            for mode in studio_core::config::GpuMode::ALL {
                ui.selectable_value(
                    &mut current,
                    mode,
                    match mode {
                        studio_core::config::GpuMode::Auto => "auto",
                        studio_core::config::GpuMode::Local => "local",
                        studio_core::config::GpuMode::Remote => "remota",
                    },
                );
            }
        });
    if current != cx.state.gpu {
        cx.actions.push(Action::SetGpu(current));
    }
}

fn vramd_chip(ui: &mut egui::Ui, state: &StudioState) {
    if let Some(error) = &state.vramd_error {
        ui.label(theme::chip(format!("vramd: {error}"), theme::ERROR));
        return;
    }
    let total = state.vramd_running + state.vramd_queued;
    if total > 0 {
        ui.label(theme::chip(
            format!("vramd {}▶ {}…", state.vramd_running, state.vramd_queued),
            theme::WARN,
        ));
        if let Some(job) = &state.vramd_job {
            ui.label(theme::muted(job.clone()));
        }
    }
}

fn state_chip(ui: &mut egui::Ui, state: &StudioState) {
    if let Some(error) = &state.check_error {
        ui.label(theme::chip(format!("estado: {error}"), theme::ERROR));
        return;
    }
    let Some(check) = state.check.as_ref() else {
        ui.label(theme::chip("estado: —", theme::MUTED));
        return;
    };
    let color = if check.state == "done" {
        theme::OK
    } else {
        theme::WARN
    };
    ui.label(theme::chip(format!("estado: {}", check.state), color));
    if check.promotion.lod0 != "none" {
        ui.label(theme::chip(
            format!("lod0: {}", check.promotion.lod0),
            theme::OK,
        ));
    }
    if let Some(lock) = &check.lock {
        if lock.live {
            ui.label(theme::chip(
                format!("lock pid {:?}", lock.pid),
                theme::ERROR,
            ));
        }
    }
}

fn welcome(ui: &mut egui::Ui, cx: &mut UiCtx) {
    ui.add_space(24.0);
    ui.heading(egui::RichText::new("AiGameKit Studio").color(theme::ACCENT));
    ui.label(theme::muted(
        "Um projeto por asset. Cria um projeto, corre o pipeline e pré-visualiza cada etapa.",
    ));
    ui.add_space(10.0);
    if ui.button("Novo projeto").clicked() {
        cx.new_form.reset();
        cx.ui.show_new_project = true;
    }
    if ui.button("Biblioteca").clicked() {
        cx.state.tab = MainTab::Library;
    }
    ui.add_space(16.0);
    ui.label(theme::muted(format!(
        "workspace: {}",
        cx.state.workspace.display()
    )));
    library::draw(ui, cx);
}

fn new_project_dialog(ctx: &egui::Context, cx: &mut UiCtx) {
    let mut open = true;
    egui::Window::new("Novo projeto")
        .anchor(egui::Align2::CENTER_CENTER, egui::vec2(0.0, 0.0))
        .collapsible(false)
        .resizable(false)
        .default_width(480.0)
        .show(ctx, |ui| {
            let before = cx.new_form.prompt.clone();
            ui.label("Prompt");
            ui.add(
                egui::TextEdit::multiline(&mut cx.new_form.prompt)
                    .desired_rows(4)
                    .desired_width(f32::INFINITY)
                    .hint_text("ex.: caixa de madeira com cantos de ferro"),
            );
            if cx.new_form.prompt != before {
                cx.new_form.sync_id();
                cx.new_form.error = None;
            }
            ui.horizontal(|ui| {
                ui.label("ID");
                let response =
                    ui.add(egui::TextEdit::singleline(&mut cx.new_form.id).desired_width(220.0));
                if response.changed() {
                    cx.new_form.id_touched = true;
                }
            });
            ui.horizontal(|ui| {
                ui.label("Preset");
                egui::ComboBox::from_id_salt("preset")
                    .selected_text(cx.new_form.preset.label())
                    .show_ui(ui, |ui| {
                        for preset in AssetPreset::ALL {
                            if ui
                                .selectable_value(&mut cx.new_form.preset, preset, preset.label())
                                .clicked()
                            {
                                if !cx.new_form.id_touched {
                                    cx.new_form.sync_id();
                                }
                            }
                        }
                    });
                ui.label(theme::muted(format!(
                    "categoria: {}",
                    cx.new_form.preset.category()
                )));
            });
            ui.horizontal(|ui| {
                ui.label("Qualidade");
                let quality_text = if cx.new_form.quality.is_empty() {
                    "herdar".to_string()
                } else {
                    cx.new_form.quality.clone()
                };
                egui::ComboBox::from_id_salt("quality")
                    .selected_text(quality_text)
                    .show_ui(ui, |ui| {
                        for quality in ["fast", "low", "medium", "high", "highest"] {
                            ui.selectable_value(
                                &mut cx.new_form.quality,
                                quality.to_string(),
                                quality,
                            );
                        }
                    });
                ui.label("GPU");
                for mode in studio_core::config::GpuMode::ALL {
                    ui.selectable_value(&mut cx.new_form.gpu, mode, mode.as_str());
                }
            });
            ui.separator();
            ui.label("Referência (opcional)");
            ui.horizontal(|ui| {
                if let Some(reference) = cx.new_form.reference.as_ref() {
                    ui.label(theme::muted(reference.display().to_string()));
                    if ui.button("limpar").clicked() {
                        cx.new_form.reference = None;
                    }
                } else {
                    ui.label(theme::muted(
                        "arrasta um PNG/JPG para a janela; sem referência o text2d gera-a",
                    ));
                }
            });
            if let Some(error) = &cx.new_form.error {
                ui.colored_label(theme::ERROR, error);
            }
            ui.separator();
            ui.horizontal(|ui| {
                let create = ui.add_enabled(
                    !cx.new_form.prompt.trim().is_empty() && !cx.new_form.id.trim().is_empty(),
                    egui::Button::new(egui::RichText::new("Criar").strong().color(theme::BG))
                        .fill(theme::ACCENT),
                );
                if create.clicked() {
                    let slug = studio_core::project::slugify(&cx.new_form.id);
                    let dir = studio_core::project::unique_project_dir(&cx.state.workspace, &slug);
                    let config = cx.new_form.to_config();
                    cx.actions.push(Action::NewProject(CreateRequest {
                        dir,
                        config,
                        reference: cx.new_form.reference.clone(),
                    }));
                    cx.new_form.reset();
                    cx.ui.show_new_project = false;
                    open = false;
                }
                if ui.button("Cancelar").clicked() {
                    cx.new_form.reset();
                    cx.ui.show_new_project = false;
                    open = false;
                }
            });
        });
    if !open {
        cx.ui.show_new_project = false;
    }
}

fn confirm_delete_dialog(ctx: &egui::Context, cx: &mut UiCtx) {
    let Some(dir) = cx.ui.confirm_delete.clone() else {
        return;
    };
    egui::Window::new("Apagar projeto?")
        .anchor(egui::Align2::CENTER_CENTER, egui::vec2(0.0, 0.0))
        .collapsible(false)
        .resizable(false)
        .show(ctx, |ui| {
            ui.label(format!("Mover para .trash: {}", dir.display()));
            ui.horizontal(|ui| {
                if ui
                    .button(egui::RichText::new("Apagar").color(theme::ERROR))
                    .clicked()
                {
                    cx.actions.push(Action::ConfirmDelete(dir.clone()));
                    cx.ui.confirm_delete = None;
                }
                if ui.button("Cancelar").clicked() {
                    cx.ui.confirm_delete = None;
                }
            });
        });
}

fn toasts(ctx: &egui::Context, cx: &mut UiCtx) {
    if cx.state.toasts.is_empty() {
        return;
    }
    egui::Area::new(egui::Id::new("studio_toasts"))
        .anchor(egui::Align2::RIGHT_TOP, egui::vec2(-14.0, 54.0))
        .order(egui::Order::Foreground)
        .show(ctx, |ui| {
            for toast in &cx.state.toasts {
                let color = match toast.kind {
                    ToastKind::Info => theme::ACCENT,
                    ToastKind::Ok => theme::OK,
                    ToastKind::Warn => theme::WARN,
                    ToastKind::Error => theme::ERROR,
                };
                egui::Frame::popup(ui.style())
                    .fill(theme::PANEL_HIGH)
                    .stroke(egui::Stroke::new(1.0, color))
                    .show(ui, |ui| {
                        ui.label(egui::RichText::new(&toast.text).color(color));
                    });
            }
        });
}

fn reference_preview(ctx: &egui::Context, cx: &mut UiCtx, rect: egui::Rect) {
    if cx.state.selected != "image" {
        return;
    }
    let Some(project) = cx.state.project.as_ref() else {
        return;
    };
    let path = project.image_path();
    let Some(handle) = texture_for(ctx, &path, cx.textures) else {
        return;
    };
    let available = rect.shrink2(egui::vec2(80.0, 70.0));
    let size = fit_size(&handle, available.size());
    let position = rect.center() - size * 0.5;
    egui::Area::new(egui::Id::new("reference_preview"))
        .fixed_pos(position)
        .order(egui::Order::Foreground)
        .show(ctx, |ui| {
            egui::Frame::popup(ui.style())
                .fill(theme::BG)
                .show(ui, |ui| {
                    ui.add(egui::Image::new(egui::load::SizedTexture::new(
                        handle.id(),
                        size,
                    )));
                });
        });
}

fn viewport_hint(ctx: &egui::Context, cx: &mut UiCtx, rect: egui::Rect) {
    let hint = if let Some(error) = cx.preview.error.as_ref() {
        Some((format!("preview: {error}"), theme::ERROR))
    } else if cx.preview.pending_decode.is_some() || cx.preview.pending_scene.is_some() {
        Some(("a carregar GLB…".to_string(), theme::MUTED))
    } else if let Some(loaded) = cx.preview.loaded.as_ref() {
        if loaded.artifact != cx.state.selected && cx.state.preview_override.is_none() {
            Some((
                format!(
                    "a mostrar {} · {} ainda não existe",
                    loaded.artifact, cx.state.selected
                ),
                theme::MUTED,
            ))
        } else {
            None
        }
    } else if cx.state.artifact_path(&cx.state.selected).is_none() {
        Some((
            format!("{} ainda não existe · corre o pipeline", cx.state.selected),
            theme::MUTED,
        ))
    } else {
        None
    };
    let Some((text, color)) = hint else {
        return;
    };
    let position = egui::pos2(rect.center().x - 170.0, rect.bottom() - 54.0);
    egui::Area::new(egui::Id::new("viewport_hint"))
        .fixed_pos(position)
        .order(egui::Order::Foreground)
        .show(ctx, |ui| {
            egui::Frame::popup(ui.style())
                .fill(theme::PANEL_HIGH)
                .show(ui, |ui| {
                    ui.label(egui::RichText::new(text).color(color).size(11.5));
                });
        });
}

pub fn texture_for(
    ctx: &egui::Context,
    path: &Path,
    cache: &mut TextureCache,
) -> Option<egui::TextureHandle> {
    let mtime = std::fs::metadata(path)
        .and_then(|meta| meta.modified())
        .ok()?;
    if let Some((handle, stamp)) = cache.map.get(path) {
        if *stamp == mtime {
            return Some(handle.clone());
        }
    }
    let image = image::open(path).ok()?.to_rgba8();
    let size = [image.width() as usize, image.height() as usize];
    let color = egui::ColorImage::from_rgba_unmultiplied(size, image.as_raw());
    let handle = ctx.load_texture(path.to_string_lossy(), color, egui::TextureOptions::LINEAR);
    cache
        .map
        .insert(path.to_path_buf(), (handle.clone(), mtime));
    Some(handle)
}

pub fn fit_size(handle: &egui::TextureHandle, max: egui::Vec2) -> egui::Vec2 {
    let size = handle.size_vec2();
    if size.x <= 0.0 || size.y <= 0.0 {
        return max;
    }
    let scale = (max.x / size.x).min(max.y / size.y).min(1.0);
    egui::vec2(size.x * scale, size.y * scale)
}
