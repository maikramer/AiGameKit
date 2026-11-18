//! Dock inferior: tarefas, consola e movimento por texto.

use bevy_egui::egui;

use super::UiCtx;
use crate::state::{Action, BottomTab, LogKind};
use crate::theme;

pub fn draw(ui: &mut egui::Ui, cx: &mut UiCtx) {
    ui.horizontal(|ui| {
        if ui
            .selectable_label(cx.state.bottom == BottomTab::Tasks, "Tarefas")
            .clicked()
        {
            cx.state.bottom = BottomTab::Tasks;
        }
        if ui
            .selectable_label(cx.state.bottom == BottomTab::Console, "Consola")
            .clicked()
        {
            cx.state.bottom = BottomTab::Console;
        }
        if ui
            .selectable_label(cx.state.bottom == BottomTab::Motion, "Movimento")
            .clicked()
        {
            cx.state.bottom = BottomTab::Motion;
        }
        ui.separator();
        if cx.state.run.is_busy() {
            ui.spinner();
            ui.label(theme::chip(
                cx.state
                    .run
                    .active_stage
                    .clone()
                    .unwrap_or_else(|| "a correr".into()),
                theme::ACCENT,
            ));
            ui.label(theme::muted(format!("{:.0}s", cx.state.run.elapsed_secs())));
        } else {
            ui.label(theme::muted("idle"));
        }
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if cx.job_running {
                ui.spinner();
                ui.label(theme::muted("job"));
            }
            if cx.check_in_flight {
                ui.label(theme::muted("check…"));
            }
            if cx.state.dirty {
                ui.label(theme::chip("não gravado", theme::WARN));
            }
        });
    });
    ui.separator();

    match cx.state.bottom {
        BottomTab::Tasks => tasks(ui, cx),
        BottomTab::Console => console(ui, cx),
        BottomTab::Motion => motion(ui, cx),
    }
}

fn tasks(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let percent = cx.state.run.percent.unwrap_or(0.0) / 100.0;
    let bar = egui::ProgressBar::new(percent)
        .animate(cx.state.run.is_busy())
        .show_percentage()
        .text(if cx.state.run.is_busy() {
            format!(
                "{} · {}",
                cx.state.run.active_stage.clone().unwrap_or_default(),
                cx.state.run.phase.clone().unwrap_or_default()
            )
        } else {
            cx.state
                .check
                .as_ref()
                .map(|check| check.state.clone())
                .unwrap_or_else(|| "sem estado".into())
        });
    ui.add(bar);

    ui.horizontal_wrapped(|ui| {
        if let Some(loaded) = cx.preview.loaded.as_ref() {
            ui.label(theme::chip(
                format!("preview: {}", loaded.artifact),
                theme::MUTED,
            ));
        }
        if let Some(check) = cx.state.check.as_ref() {
            ui.label(theme::chip(
                format!(
                    "{} artefacto(s)",
                    check.artifacts.values().filter(|a| a.exists).count()
                ),
                theme::MUTED,
            ));
            if let Some(target) = check.asset.target_faces {
                ui.label(theme::chip(format!("alvo {target} tris"), theme::MUTED));
            }
            if let Some(size) = check.asset.paint_texture_size {
                ui.label(theme::chip(format!("tex {size}"), theme::MUTED));
            }
            let tools_ok = check.tools.values().filter(|value| value.is_some()).count();
            let tools_total = check.tools.len();
            ui.label(theme::chip(
                format!("tools {tools_ok}/{tools_total}"),
                if tools_ok == tools_total {
                    theme::OK
                } else {
                    theme::WARN
                },
            ));
        }
        if cx.state.vramd_running + cx.state.vramd_queued > 0 {
            ui.label(theme::chip(
                format!(
                    "vramd {}▶ {}…",
                    cx.state.vramd_running, cx.state.vramd_queued
                ),
                theme::WARN,
            ));
            if let Some(job) = &cx.state.vramd_job {
                ui.label(theme::muted(job.clone()));
            }
        }
    });

    if !cx.state.stage_errors.is_empty() {
        ui.separator();
        for (stage, error) in &cx.state.stage_errors {
            ui.label(
                egui::RichText::new(format!("{stage}: {error}"))
                    .color(theme::ERROR)
                    .size(11.0),
            );
        }
    }
    if let Some(lock) = cx.state.lock_live() {
        ui.label(
            egui::RichText::new(format!(
                "lock: pid {:?} {}",
                lock.pid,
                lock.command.clone().unwrap_or_default()
            ))
            .color(theme::ERROR)
            .size(11.0),
        );
    }
    if let Some(error) = &cx.state.check_error {
        ui.label(
            egui::RichText::new(format!("check: {error}"))
                .color(theme::ERROR)
                .size(11.0),
        );
    }
    if let Some(error) = &cx.preview.error {
        ui.label(
            egui::RichText::new(format!("preview: {error}"))
                .color(theme::ERROR)
                .size(11.0),
        );
    }

    ui.horizontal(|ui| {
        if ui
            .add_enabled(
                cx.state.can_run(),
                egui::Button::new(egui::RichText::new("Correr").strong().color(theme::BG))
                    .fill(theme::ACCENT),
            )
            .clicked()
        {
            cx.actions.push(Action::Run);
        }
        if ui
            .add_enabled(cx.state.run.is_busy(), egui::Button::new("Cancelar"))
            .clicked()
        {
            cx.actions.push(Action::Cancel);
        }
        if let Some(path) = cx.state.run.log_path.clone() {
            ui.label(theme::muted(path.display().to_string()));
        }
    });
}

fn console(ui: &mut egui::Ui, cx: &mut UiCtx) {
    ui.horizontal(|ui| {
        ui.checkbox(&mut cx.ui.console_errors_only, "só erros/avisos");
        ui.checkbox(&mut cx.ui.console_autoscroll, "auto-scroll");
        if ui.button("Limpar").clicked() {
            cx.state.log.clear();
        }
        if ui.button("Copiar").clicked() {
            let text = cx
                .state
                .log
                .iter()
                .map(|line| format!("[{}] {}", line.at, line.text))
                .collect::<Vec<_>>()
                .join("\n");
            ui.ctx().copy_text(text);
        }
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(theme::muted(format!("{} linhas", cx.state.log.len())));
        });
    });

    let mut scroll = egui::ScrollArea::vertical().auto_shrink([false, false]);
    if cx.ui.console_autoscroll {
        scroll = scroll.stick_to_bottom(true);
    }
    scroll.show(ui, |ui| {
        let start = cx.state.log.len().saturating_sub(800);
        for line in cx.state.log.iter().skip(start) {
            if cx.ui.console_errors_only && !matches!(line.kind, LogKind::Warn | LogKind::Error) {
                continue;
            }
            let color = match line.kind {
                LogKind::Info => theme::MUTED,
                LogKind::Ok => theme::OK,
                LogKind::Warn => theme::WARN,
                LogKind::Error => theme::ERROR,
                LogKind::Tool => egui::Color32::from_rgb(180, 190, 205),
                LogKind::Progress => theme::ACCENT,
            };
            ui.label(
                egui::RichText::new(format!("[{}] {}", line.at, line.text))
                    .monospace()
                    .size(11.0)
                    .color(color),
            );
        }
    });
}

fn motion(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let Some(project) = cx.state.project.as_ref() else {
        ui.label(theme::muted("sem projeto"));
        return;
    };
    let seed: Option<i64> = cx.motion_form.seed.trim().parse().ok();

    ui.horizontal_wrapped(|ui| {
        ui.label(theme::heading("Movimento por texto"));
        ui.label(theme::chip("motion3d · CPU", theme::MUTED));
        ui.label(theme::muted(
            "retarget no _rigged.glb; empacotar substitui o anim-pack",
        ));
    });
    ui.horizontal(|ui| {
        ui.label("Prompt");
        ui.add(
            egui::TextEdit::singleline(&mut cx.motion_form.prompt)
                .desired_width(360.0)
                .hint_text("a tired traveler walks forward"),
        );
    });
    ui.horizontal_wrapped(|ui| {
        ui.label("duração");
        ui.add(
            egui::DragValue::new(&mut cx.motion_form.duration_s)
                .speed(0.1)
                .range(0.5..=10.0)
                .suffix(" s"),
        );
        ui.checkbox(&mut cx.motion_form.looped, "loop");
        ui.checkbox(&mut cx.motion_form.foot_lock, "pés plantados");
        ui.label("qualidade");
        egui::ComboBox::from_id_salt("motion_quality")
            .selected_text(cx.motion_form.quality.clone())
            .show_ui(ui, |ui| {
                for tier in ["fast", "low", "medium", "high", "highest"] {
                    ui.selectable_value(&mut cx.motion_form.quality, tier.to_string(), tier);
                }
            });
        ui.label("attempts");
        ui.add(egui::DragValue::new(&mut cx.motion_form.attempts).range(1..=8));
        ui.label("seed");
        ui.add(
            egui::TextEdit::singleline(&mut cx.motion_form.seed)
                .desired_width(80.0)
                .hint_text("auto"),
        );
    });

    ui.horizontal(|ui| {
        let generate = ui.add_enabled(
            !cx.motion_form.prompt.trim().is_empty() && !cx.job_running,
            egui::Button::new(
                egui::RichText::new("Gerar movimento")
                    .strong()
                    .color(theme::BG),
            )
            .fill(theme::ACCENT),
        );
        if generate.clicked() {
            let name = studio_core::motion::unique_name(project, &cx.motion_form.prompt);
            let request = studio_core::motion::MotionRequest {
                name,
                prompt: cx.motion_form.prompt.clone(),
                duration_s: cx.motion_form.duration_s,
                looped: cx.motion_form.looped,
                foot_lock: cx.motion_form.foot_lock,
                quality: cx.motion_form.quality.clone(),
                attempts: cx.motion_form.attempts,
                seed,
            };
            cx.motion_form.selected.clear();
            cx.actions.push(Action::GenerateMotion(request));
        }
        if ui
            .add_enabled(
                !cx.motion_form.selected.is_empty() && !cx.job_running,
                egui::Button::new("Empacotar selecionados"),
            )
            .clicked()
        {
            cx.actions
                .push(Action::PackMotions(cx.motion_form.selected.clone()));
        }
        if cx.job_running {
            ui.spinner();
        }
    });
    if let Some(error) = &cx.motion_form.error {
        ui.colored_label(theme::ERROR, error);
    }
    let duplicate_clips = cx
        .state
        .check
        .as_ref()
        .and_then(|check| check.artifacts.get("animated"))
        .and_then(|artifact| artifact.gates.as_ref())
        .is_some_and(|gates| gates.duplicate_clips);
    if duplicate_clips {
        ui.label(theme::chip(
            "clips duplicados no animado: empacota sobre o _rigged sem game-pack",
            theme::WARN,
        ));
    }

    ui.separator();
    if cx.state.clips.is_empty() {
        ui.label(theme::muted("sem clips; gera um movimento"));
        return;
    }
    let clips = cx.state.clips.clone();
    egui::ScrollArea::vertical()
        .max_height(ui.available_height())
        .auto_shrink([false, false])
        .show(ui, |ui| {
            for clip in &clips {
                ui.horizontal(|ui| {
                    let mut selected = cx.motion_form.selected.contains(&clip.name);
                    if ui.checkbox(&mut selected, "").changed() {
                        if selected {
                            cx.motion_form.selected.push(clip.name.clone());
                        } else {
                            cx.motion_form.selected.retain(|name| name != &clip.name);
                        }
                    }
                    ui.label(egui::RichText::new(&clip.name).strong());
                    ui.label(theme::muted(format!("{:.1}s", clip.duration_s)));
                    if let Some(score) = clip.score {
                        ui.label(theme::chip(format!("score {score:.3}"), theme::MUTED));
                    }
                    if let Some(slide) = clip.foot_slide {
                        ui.label(theme::chip(format!("slide {slide:.3}"), theme::MUTED));
                    }
                    if ui.button("Preview").clicked() {
                        cx.actions.push(Action::PreviewMotion(clip.name.clone()));
                    }
                    if ui
                        .button(egui::RichText::new("Apagar").color(theme::ERROR))
                        .clicked()
                    {
                        cx.actions.push(Action::DeleteMotion(clip.name.clone()));
                    }
                });
                ui.label(theme::muted(clip.prompt.clone()));
            }
        });
}
