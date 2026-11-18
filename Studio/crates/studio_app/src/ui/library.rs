//! Biblioteca de projetos do workspace.

use std::time::Instant;

use bevy_egui::egui;

use super::{UiCtx, fit_size, texture_for};
use crate::state::Action;
use crate::theme;

pub fn draw(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let due = cx
        .library
        .last_refresh
        .map(|last| last.elapsed().as_secs() > 5)
        .unwrap_or(true);
    if due {
        cx.library.last_refresh = Some(Instant::now());
        cx.actions.push(Action::RefreshLibrary);
    }

    ui.horizontal(|ui| {
        ui.label(theme::heading("Biblioteca"));
        ui.add(
            egui::TextEdit::singleline(&mut cx.library.search)
                .desired_width(280.0)
                .hint_text("filtrar por id, prompt ou categoria"),
        );
        if ui
            .button(
                egui::RichText::new("Novo projeto")
                    .strong()
                    .color(theme::BG),
            )
            .clicked()
        {
            cx.new_form.reset();
            cx.ui.show_new_project = true;
        }
        if ui.button("Atualizar").clicked() {
            cx.actions.push(Action::RefreshLibrary);
        }
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(theme::muted(cx.state.workspace.display().to_string()));
        });
    });
    ui.label(theme::muted(
        "cada projeto tem game.yaml + manifest.yaml e um único asset (contrato do studio check)",
    ));
    ui.separator();

    let search = cx.library.search.trim().to_lowercase();
    let projects: Vec<_> = cx
        .state
        .library
        .iter()
        .filter(|project| {
            search.is_empty()
                || project.id.to_lowercase().contains(&search)
                || project.idea.to_lowercase().contains(&search)
                || project.category.to_lowercase().contains(&search)
        })
        .cloned()
        .collect();

    if projects.is_empty() {
        ui.add_space(30.0);
        ui.vertical_centered(|ui| {
            ui.label(theme::muted("sem projetos neste workspace"));
            if ui.button("Criar o primeiro projeto").clicked() {
                cx.new_form.reset();
                cx.ui.show_new_project = true;
            }
        });
        return;
    }

    egui::ScrollArea::vertical()
        .auto_shrink([false, false])
        .show(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                for project in &projects {
                    card(ui, cx, project);
                }
            });
        });
}

fn card(ui: &mut egui::Ui, cx: &mut UiCtx, project: &studio_core::project::ProjectSummary) {
    egui::Frame::group(ui.style())
        .fill(theme::PANEL)
        .inner_margin(egui::Margin::same(8))
        .show(ui, |ui| {
            ui.vertical(|ui| {
                ui.set_width(230.0);
                let cover = project.dir.join(".studio").join("thumbs").join("cover.png");
                let size = egui::vec2(214.0, 128.0);
                if let Some(handle) = texture_for(ui.ctx(), &cover, cx.textures) {
                    let fit = fit_size(&handle, size);
                    ui.add(egui::Image::new(egui::load::SizedTexture::new(
                        handle.id(),
                        fit,
                    )));
                } else {
                    let (rect, _) = ui.allocate_exact_size(size, egui::Sense::hover());
                    ui.painter().rect_filled(rect, 4.0, theme::PANEL_HIGH);
                    ui.painter().text(
                        rect.center(),
                        egui::Align2::CENTER_CENTER,
                        "sem thumbnail",
                        egui::FontId::proportional(12.0),
                        theme::MUTED,
                    );
                }
                ui.add_space(4.0);
                ui.label(egui::RichText::new(&project.id).strong().size(13.0));
                ui.label(theme::chip(project.category.clone(), theme::MUTED));
                let idea = if project.idea.chars().count() > 90 {
                    format!("{}…", project.idea.chars().take(90).collect::<String>())
                } else {
                    project.idea.clone()
                };
                ui.label(theme::muted(idea));
                if let Some(modified) = project.modified {
                    if let Ok(elapsed) = modified.elapsed() {
                        let secs = elapsed.as_secs();
                        let label = if secs < 90 {
                            format!("{secs}s")
                        } else if secs < 5400 {
                            format!("{}min", secs / 60)
                        } else if secs < 172800 {
                            format!("{}h", secs / 3600)
                        } else {
                            format!("{}d", secs / 86400)
                        };
                        ui.label(theme::muted(format!("há {label}")));
                    }
                }
                ui.horizontal(|ui| {
                    if ui.button("Abrir").clicked() {
                        cx.actions.push(Action::OpenProject(project.dir.clone()));
                    }
                    if ui
                        .button(egui::RichText::new("Apagar").color(theme::ERROR))
                        .clicked()
                    {
                        cx.actions.push(Action::DeleteProject(project.dir.clone()));
                    }
                });
            });
        });
}
