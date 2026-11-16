//! Painel direito: pipeline, artefactos, gates e re-roll.

use bevy_egui::egui;
use studio_core::{
    check::{Artifact, GlbSummary},
    config::PipelineStage,
    reroll::Stage,
};

use super::UiCtx;
use crate::state::Action;
use crate::theme;

pub fn draw(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let Some(project) = cx.state.project.as_ref() else {
        return;
    };
    let mut config = project.config.clone();
    let mut changed = false;

    ui.horizontal(|ui| {
        ui.label(theme::heading("Pipeline"));
        if let Some(check) = cx.state.check.as_ref() {
            ui.label(theme::muted(format!(
                "{} · {} etapa(s)",
                check.state,
                check.asset.pipeline.len()
            )));
        }
    });

    ui.horizontal_wrapped(|ui| {
        for stage in PipelineStage::ALL {
            let mut enabled = config.pipeline.has(stage);
            if ui.checkbox(&mut enabled, stage.label()).changed() {
                config.pipeline.set(stage, enabled);
                if !enabled {
                    cx.ui.invalidate_after_save = Some(stage_for(stage));
                }
                changed = true;
            }
        }
    });
    if changed {
        if let Some(project) = cx.state.project.as_mut() {
            project.config = config;
        }
        cx.state.dirty = true;
    }

    if let Some(lock) = cx.state.lock_live().cloned() {
        let color = theme::ERROR;
        egui::Frame::group(ui.style())
            .stroke(egui::Stroke::new(1.0, color))
            .show(ui, |ui| {
                ui.label(
                    egui::RichText::new(format!(
                        "lock ativo: pid {:?} · {}",
                        lock.pid,
                        lock.command.clone().unwrap_or_default()
                    ))
                    .color(color)
                    .size(11.0),
                );
            });
    }

    ui.add_space(4.0);
    let check = cx.state.check.clone();
    for stage in Stage::DAG {
        stage_row(ui, cx, stage, check.as_ref());
    }

    ui.separator();
    ui.label(theme::heading("Deliverables"));
    if let Some(check) = check.as_ref() {
        for (key, label) in [
            ("lod0", "LOD0"),
            ("lod1", "LOD1"),
            ("lod2", "LOD2"),
            ("collision", "Colisão"),
            ("precompute", "Precompute"),
        ] {
            let Some(artifact) = check.deliverables.get(key) else {
                continue;
            };
            let color = if artifact.exists {
                theme::OK
            } else {
                theme::MUTED
            };
            ui.horizontal(|ui| {
                ui.label(theme::chip(if artifact.exists { "●" } else { "○" }, color));
                ui.label(egui::RichText::new(label).size(11.5));
                if let Some(size) = artifact.size {
                    ui.label(theme::muted(format!("{} KB", size / 1024)));
                }
                if artifact.exists {
                    ui.label(theme::muted(
                        artifact
                            .path
                            .rsplit('/')
                            .next()
                            .unwrap_or(&artifact.path)
                            .to_string(),
                    ));
                }
            });
        }
        if check.promotion.lod0 != "none" {
            ui.label(theme::chip(
                format!("promoção: {}", check.promotion.lod0),
                theme::OK,
            ));
        }
    } else {
        ui.label(theme::muted("sem estado do gameassets"));
    }

    ui.separator();
    ui.horizontal_wrapped(|ui| {
        if ui
            .add_enabled(
                cx.state.can_run(),
                egui::Button::new(
                    egui::RichText::new("Correr pendentes")
                        .strong()
                        .color(theme::BG),
                )
                .fill(theme::ACCENT),
            )
            .clicked()
        {
            cx.actions.push(Action::Run);
        }
        if ui.button("Atualizar estado").clicked() {
            cx.actions.push(Action::RefreshCheck);
        }
        if ui.button("Doctor").clicked() {
            cx.actions.push(Action::Doctor);
        }
        if ui
            .button(egui::RichText::new("Reset total").color(theme::ERROR))
            .clicked()
        {
            cx.actions.push(Action::Reset);
        }
        if ui.button("Fechar projeto").clicked() {
            cx.actions.push(Action::CloseProject);
        }
    });
    ui.label(theme::muted(
        "Re-roll guarda o artefacto em .studio/history antes de apagar.",
    ));
}

fn stage_for(stage: PipelineStage) -> Stage {
    match stage {
        PipelineStage::Paint => Stage::Paint,
        PipelineStage::Rig => Stage::Rig,
        PipelineStage::Animate => Stage::Animate,
        PipelineStage::Lod => Stage::Lod,
        PipelineStage::Collision => Stage::Collision,
    }
}

fn stage_row(
    ui: &mut egui::Ui,
    cx: &mut UiCtx,
    stage: Stage,
    check: Option<&studio_core::check::CheckPayload>,
) {
    let exists = check.is_some_and(|check| {
        stage
            .artifact_keys()
            .iter()
            .all(|key| check.has_artifact(key))
    });
    let any_exists = check.is_some_and(|check| {
        stage
            .artifact_keys()
            .iter()
            .any(|key| check.has_artifact(key))
    });
    let stale = cx.state.stage_stale(stage);
    let error = cx.state.stage_errors.get(stage.key()).cloned();
    let running = active_for(stage, cx.state);
    let timing = cx.state.timings.get(stage.key()).copied();
    let artifact = check
        .and_then(|check| stage.artifact_keys().iter().find_map(|key| check.artifact(key)));

    let color = if error.is_some() {
        theme::ERROR
    } else if running {
        theme::ACCENT
    } else if stale {
        theme::WARN
    } else if exists {
        theme::OK
    } else if any_exists {
        theme::WARN
    } else {
        theme::MUTED
    };
    let dot = if error.is_some() {
        "✕"
    } else if running {
        "◌"
    } else if exists {
        "●"
    } else {
        "○"
    };

    egui::Frame::group(ui.style())
        .fill(theme::PANEL_HIGH)
        .stroke(egui::Stroke::new(
            if running { 1.0 } else { 0.5 },
            if running { theme::ACCENT } else { theme::ACCENT_DIM },
        ))
        .inner_margin(egui::Margin::symmetric(8, 6))
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new(dot).color(color).size(13.0));
                ui.label(egui::RichText::new(stage.label()).strong().size(12.5));
                if running {
                    ui.spinner();
                    if let Some(percent) = cx.state.run.percent {
                        ui.label(theme::chip(format!("{percent:.0}%"), theme::ACCENT));
                    }
                }
                if let Some(timing) = timing {
                    ui.label(theme::muted(format!("{timing:.1}s")));
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui
                        .add_enabled(
                            exists && !cx.state.run.is_busy(),
                            egui::Button::new(egui::RichText::new("Re-roll").size(11.0)),
                        )
                        .clicked()
                    {
                        cx.actions.push(Action::Reroll(stage));
                    }
                    if stage != Stage::Image
                        && ui
                            .add_enabled(
                                any_exists && !cx.state.run.is_busy(),
                                egui::Button::new(
                                    egui::RichText::new("Invalidar").size(11.0),
                                ),
                            )
                            .clicked()
                    {
                        cx.actions.push(Action::InvalidateFrom(stage));
                    }
                });
            });

            if let Some(error) = error {
                ui.label(
                    egui::RichText::new(error)
                        .color(theme::ERROR)
                        .size(10.5),
                );
            }
            if stale {
                ui.label(theme::chip(
                    "stale: os parâmetros mudaram",
                    theme::WARN,
                ));
            }
            if let Some(artifact) = artifact {
                artifact_line(ui, artifact, stage);
            }
        });
    ui.add_space(3.0);
}

fn artifact_line(ui: &mut egui::Ui, artifact: &Artifact, stage: Stage) {
    if !artifact.exists {
        ui.label(theme::muted("ainda não existe"));
        return;
    }
    if let Some(glb) = artifact.glb.as_ref() {
        chips(ui, glb);
        if let Some(gates) = artifact.gates.as_ref() {
            ui.horizontal_wrapped(|ui| {
                for (label, ok) in [
                    ("skins", gates.skins),
                    ("anims", gates.animations),
                    ("mats", gates.materials),
                    ("clips", !gates.duplicate_clips),
                ] {
                    if stage == Stage::Lod || stage == Stage::Animate || stage == Stage::Rig {
                        ui.label(theme::chip(
                            format!("{label}:{}", if ok { "✓" } else { "✗" }),
                            if ok { theme::OK } else { theme::MUTED },
                        ));
                    }
                }
            });
        }
    } else if let Some(size) = artifact.size {
        ui.label(theme::muted(format!("{} KB", size / 1024)));
    }
}

fn chips(ui: &mut egui::Ui, glb: &GlbSummary) {
    ui.horizontal_wrapped(|ui| {
        ui.label(theme::chip(
            format!("{} tris", glb.triangle_count_total),
            theme::MUTED,
        ));
        ui.label(theme::chip(
            format!("{} verts", glb.vertex_count_total),
            theme::MUTED,
        ));
        if let Some(ratio) = glb.v_per_tri {
            ui.label(theme::chip(format!("v/t {ratio:.2}"), theme::MUTED));
        }
        if glb.materials > 0 {
            ui.label(theme::chip(
                format!("{} mat", glb.materials),
                theme::MUTED,
            ));
        }
        if glb.skins > 0 {
            ui.label(theme::chip("skin", theme::OK));
        }
        if glb.animations > 0 {
            ui.label(theme::chip(
                format!("{} anim", glb.animations),
                theme::OK,
            ));
        }
        if glb.base_color_texture {
            ui.label(theme::chip("tex", theme::OK));
        }
        if glb.has_tangents {
            ui.label(theme::chip("tang", theme::OK));
        }
    });
    if !glb.animation_names.is_empty() {
        let names = glb.animation_names.join(", ");
        ui.label(theme::muted(names));
    }
}

fn active_for(stage: Stage, state: &crate::state::StudioState) -> bool {
    let tool = state.run.active_stage.as_deref().unwrap_or_default();
    let phase = state.run.phase.as_deref().unwrap_or_default();
    match stage {
        Stage::Image => phase.contains("Imagem") || phase.contains("text2d"),
        Stage::Shape => tool == "shape" || phase == "Shape",
        Stage::Clean => tool == "topology_fix" || tool == "topology-fix",
        Stage::Refine => tool == "refine",
        Stage::ToPaint => tool == "to_paint" || tool == "to-paint",
        Stage::Paint => tool == "paint" || phase == "Paint",
        Stage::Rig => tool == "rig" || phase == "Rigging",
        Stage::Animate => tool == "animate" || phase == "Animation",
        Stage::Lod => {
            matches!(tool, "lod" | "validate_lod0" | "validate-lod0" | "finish")
        }
        Stage::Collision => tool == "collision",
    }
}
