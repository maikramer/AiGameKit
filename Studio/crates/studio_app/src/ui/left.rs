//! Painel esquerdo: fonte, referência e parâmetros por etapa.

use bevy_egui::egui;
use studio_core::config::{PipelineStage, StudioConfig};

use super::{UiCtx, fit_size, texture_for};
use crate::state::{Action, AssetPreset};
use crate::theme;

const CATEGORIES: [&str; 10] = [
    "prop",
    "building",
    "humanoid",
    "creature",
    "vegetation",
    "rock",
    "weapon",
    "armor",
    "item",
    "vehicle",
];

pub fn draw(ui: &mut egui::Ui, cx: &mut UiCtx) {
    let Some(project) = cx.state.project.as_ref() else {
        return;
    };
    let mut config = project.config.clone();
    let image_path = project.image_path();
    let mut changed = false;

    ui.horizontal(|ui| {
        ui.label(theme::heading("Fonte"));
        if cx.state.dirty {
            ui.label(theme::chip("não gravado", theme::WARN));
        }
    });
    ui.add_space(2.0);
    ui.label(egui::RichText::new("Prompt").size(11.0).color(theme::MUTED));
    let response = ui.add(
        egui::TextEdit::multiline(&mut config.asset.idea)
            .desired_rows(5)
            .desired_width(f32::INFINITY)
            .hint_text("descreve o asset: forma, material, pose, detalhes"),
    );
    changed |= response.changed();

    ui.horizontal(|ui| {
        ui.label("ID");
        let id = ui.add(
            egui::TextEdit::singleline(&mut config.asset.id).desired_width(180.0),
        );
        changed |= id.changed();
        ui.label(theme::muted("slug"));
    });

    ui.horizontal(|ui| {
        ui.label("Categoria");
        let mut category = config.asset.category.clone();
        egui::ComboBox::from_id_salt("cat")
            .selected_text(category.clone())
            .width(140.0)
            .show_ui(ui, |ui| {
                for entry in CATEGORIES {
                    ui.selectable_value(&mut category, entry.to_string(), entry);
                }
            });
        if category != config.asset.category {
            config.asset.category = category;
            changed = true;
        }
    });

    ui.horizontal(|ui| {
        ui.label("Preset");
        for preset in AssetPreset::ALL {
            if ui
                .selectable_label(
                    config.asset.kind.as_deref() == kind_for(preset)
                        || (preset == AssetPreset::Prop && config.asset.kind.is_none()),
                    preset.label(),
                )
                .clicked()
            {
                apply_preset(&mut config, preset);
                changed = true;
            }
        }
    });

    ui.horizontal(|ui| {
        ui.label("Seed");
        let mut use_seed = config.asset.seed.is_some();
        let mut seed = config.asset.seed.unwrap_or(7777);
        if ui.checkbox(&mut use_seed, "").changed() {
            changed = true;
        }
        if use_seed && ui.add(egui::DragValue::new(&mut seed).range(0..=i64::MAX)).changed() {
            changed = true;
        }
        if use_seed != config.asset.seed.is_some() {
            config.asset.seed = use_seed.then_some(seed);
        } else if use_seed && config.asset.seed != Some(seed) {
            config.asset.seed = Some(seed);
        }
        ui.label("Qualidade");
        let mut quality = config.asset.quality.clone().unwrap_or_default();
        let label = if quality.is_empty() {
            "herdar".to_string()
        } else {
            quality.clone()
        };
        egui::ComboBox::from_id_salt("quality_asset")
            .selected_text(label)
            .width(90.0)
            .show_ui(ui, |ui| {
                if ui.selectable_value(&mut quality, String::new(), "herdar").clicked() {
                    changed = true;
                }
                for tier in ["fast", "low", "medium", "high", "highest"] {
                    if ui
                        .selectable_value(&mut quality, tier.to_string(), tier)
                        .clicked()
                    {
                        changed = true;
                    }
                }
            });
        let new_quality = (!quality.is_empty()).then_some(quality);
        if new_quality != config.asset.quality {
            config.asset.quality = new_quality;
            changed = true;
        }
    });

    ui.separator();
    ui.label(theme::heading("Referência"));
    if image_path.is_file() {
        if let Some(handle) =
            texture_for(ui.ctx(), &image_path, cx.textures)
        {
            let size = fit_size(&handle, egui::vec2(320.0, 190.0));
            ui.add(egui::Image::new(egui::load::SizedTexture::new(
                handle.id(),
                size,
            )));
        }
        ui.label(theme::muted(format!(
            "{} · {} KB",
            image_path.display(),
            std::fs::metadata(&image_path)
                .map(|meta| meta.len() / 1024)
                .unwrap_or(0)
        )));
        ui.label(theme::muted(
            "nova referência: arrasta um ficheiro para a janela",
        ));
    } else {
        ui.label(theme::muted(
            "sem referência: o text2d gera-a a partir do prompt no próximo run",
        ));
        ui.label(theme::muted("ou arrasta um PNG/JPG para a janela"));
    }

    ui.separator();
    params_sections(ui, &mut config, &mut changed, cx);

    ui.separator();
    if let Some(check) = cx.state.check.as_ref() {
        egui::CollapsingHeader::new("Prompts construídos")
            .default_open(false)
            .show(ui, |ui| {
                let mut text = check.prompt.text2d.clone();
                ui.label(theme::muted("text2d"));
                ui.add(
                    egui::TextEdit::multiline(&mut text)
                        .interactive(false)
                        .desired_rows(3)
                        .desired_width(f32::INFINITY),
                );
                let mut hint = check.prompt.text3d_hint.clone();
                ui.label(theme::muted("text3d"));
                ui.add(
                    egui::TextEdit::multiline(&mut hint)
                        .interactive(false)
                        .desired_rows(3)
                        .desired_width(f32::INFINITY),
                );
            });
    }

    let issues = config.validate();
    if !issues.is_empty() {
        ui.separator();
        for issue in &issues {
            let color = match issue.level {
                studio_core::config::IssueLevel::Error => theme::ERROR,
                studio_core::config::IssueLevel::Warning => theme::WARN,
            };
            ui.label(egui::RichText::new(&issue.message).color(color).size(11.0));
        }
    }

    if changed {
        if let Some(project) = cx.state.project.as_mut() {
            project.config = config;
        }
        cx.state.dirty = true;
    }

    ui.add_space(8.0);
    ui.horizontal(|ui| {
        if ui
            .add_enabled(
                cx.state.dirty,
                egui::Button::new(
                    egui::RichText::new("Aplicar alterações").strong().color(theme::BG),
                )
                .fill(theme::ACCENT),
            )
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
    });
}

fn kind_for(preset: AssetPreset) -> Option<&'static str> {
    match preset {
        AssetPreset::Prop => None,
        AssetPreset::Character => Some("character"),
        AssetPreset::Creature => Some("creature"),
        AssetPreset::Building => Some("building"),
    }
}

fn apply_preset(config: &mut StudioConfig, preset: AssetPreset) {
    config.asset.kind = kind_for(preset).map(str::to_string);
    config.asset.category = preset.category().to_string();
    match preset {
        AssetPreset::Character | AssetPreset::Creature => {
            config.pipeline.stages = PipelineStage::ALL.to_vec();
            config.omni.control_type = Some("pose".into());
            if preset == AssetPreset::Character && config.omni.pose_preset.is_none() {
                config.omni.pose_preset = Some("quaternius-apose".into());
            }
            config.animate.preset = Some(
                if preset == AssetPreset::Creature {
                    "creature"
                } else {
                    "humanoid"
                }
                .into(),
            );
        }
        AssetPreset::Prop => {
            config.pipeline.stages = vec![
                PipelineStage::Paint,
                PipelineStage::Lod,
                PipelineStage::Collision,
            ];
            config.omni.control_type = Some("bbox".into());
            config.omni.pose_preset = None;
        }
        AssetPreset::Building => {
            config.pipeline.stages = vec![
                PipelineStage::Paint,
                PipelineStage::Lod,
                PipelineStage::Collision,
            ];
            config.omni.control_type = Some("bbox".into());
            config.omni.bbox_preset = Some("building".into());
            config.omni.pose_preset = None;
        }
    }
    config.pipeline.stages = config.pipeline.normalized();
}

fn params_sections(
    ui: &mut egui::Ui,
    config: &mut StudioConfig,
    changed: &mut bool,
    cx: &mut UiCtx,
) {
    egui::CollapsingHeader::new("Mesh e Hi3D")
        .default_open(true)
        .show(ui, |ui| {
            *changed |= ui
                .checkbox(&mut config.refine.enabled, "Hi3D refine")
                .changed();
            ui.horizontal(|ui| {
                ui.label("views");
                *changed |= ui
                    .add(egui::DragValue::new(&mut config.refine.views).range(1..=6))
                    .changed();
                if config.refine.views > 1 {
                    ui.label(theme::chip("multiview copia a referência", theme::WARN));
                }
            });
            ui.horizontal(|ui| {
                ui.label("octree");
                *changed |= opt_u32(ui, &mut config.text3d.octree_resolution, 64..=1024);
                ui.label("mc_level");
                *changed |= opt_i32(ui, &mut config.text3d.mc_level, -2..=4);
            });
            ui.horizontal(|ui| {
                ui.label("morph_close");
                *changed |= opt_f32(ui, &mut config.text3d.morph_close, 0.0..=0.2, 0.001);
            });
            *changed |= tri_bool(ui, "separar árvores por altura", &mut config.text3d.split_at_height);
            ui.horizontal(|ui| {
                ui.label("preset");
                *changed |= opt_text(ui, &mut config.text3d.preset, "hq");
            });
        });

    egui::CollapsingHeader::new("Omni / controlo de forma")
        .default_open(false)
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.label("control");
                let mut control = config.omni.control_type.clone().unwrap_or_default();
                egui::ComboBox::from_id_salt("control_type")
                    .selected_text(if control.is_empty() {
                        "auto".to_string()
                    } else {
                        control.clone()
                    })
                    .show_ui(ui, |ui| {
                        if ui.selectable_value(&mut control, String::new(), "auto").clicked() {
                            *changed = true;
                        }
                        for option in ["bbox", "pose", "point-cloud", "voxel-mesh"] {
                            if ui
                                .selectable_value(&mut control, option.to_string(), option)
                                .clicked()
                            {
                                *changed = true;
                            }
                        }
                    });
                let next = (!control.is_empty()).then_some(control);
                if next != config.omni.control_type {
                    config.omni.control_type = next;
                    *changed = true;
                }
            });
            ui.horizontal(|ui| {
                ui.label("bbox_preset");
                *changed |= opt_text(ui, &mut config.omni.bbox_preset, "cube");
                ui.label("pose_preset");
                *changed |= opt_text(ui, &mut config.omni.pose_preset, "quaternius-apose");
            });
            ui.horizontal(|ui| {
                ui.label("size_m");
                let mut size = config.omni.size_m.unwrap_or([1.0, 1.0, 1.0]);
                let mut has = config.omni.size_m.is_some();
                if ui.checkbox(&mut has, "").changed() {
                    config.omni.size_m = has.then_some(size);
                    *changed = true;
                }
                if has {
                    let mut size_changed = false;
                    for value in &mut size {
                        if ui
                            .add(egui::DragValue::new(value).speed(0.05).range(0.01..=100.0))
                            .changed()
                        {
                            size_changed = true;
                        }
                    }
                    if size_changed {
                        config.omni.size_m = Some(size);
                        *changed = true;
                    }
                }
            });
        });

    egui::CollapsingHeader::new("Pintura")
        .default_open(false)
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.label("texture_size");
                let mut enabled = config.paint.texture_size.is_some();
                let mut size = config.paint.texture_size.unwrap_or(2048);
                if ui.checkbox(&mut enabled, "").changed() {
                    config.paint.texture_size = enabled.then_some(size);
                    *changed = true;
                }
                if enabled {
                    egui::ComboBox::from_id_salt("tex_size")
                        .selected_text(size.to_string())
                        .show_ui(ui, |ui| {
                            for option in [512u32, 1024, 2048, 4096] {
                                if ui
                                    .selectable_value(&mut size, option, option.to_string())
                                    .clicked()
                                {
                                    config.paint.texture_size = Some(size);
                                    *changed = true;
                                }
                            }
                        });
                }
            });
            ui.horizontal(|ui| {
                ui.label("quality");
                *changed |= opt_text(ui, &mut config.paint.quality, "medium");
            });
            *changed |= tri_bool(ui, "pbr_enrich", &mut config.paint.pbr_enrich);
            *changed |= tri_bool(ui, "preserve_origin", &mut config.paint.preserve_origin);
        });

    egui::CollapsingHeader::new("Rig e animação")
        .default_open(config.pipeline.has(PipelineStage::Rig))
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.label("rig quality");
                *changed |= opt_text(ui, &mut config.rig.quality, "medium");
            });
            ui.horizontal(|ui| {
                ui.label("animate preset");
                *changed |= opt_text(ui, &mut config.animate.preset, "humanoid");
            });
            ui.horizontal(|ui| {
                ui.label("clips");
                *changed |= opt_text(ui, &mut config.animate.clips, "idle,walk,run");
            });
            ui.horizontal(|ui| {
                ui.label("anim_pack");
                *changed |= opt_text(ui, &mut config.animate.anim_pack, "all");
            });
            *changed |= tri_bool(ui, "procedural", &mut config.animate.procedural);
            *changed |= tri_bool(ui, "force_preset", &mut config.animate.force_preset);
            ui.label(theme::muted(format!(
                "clips motion3d: {} (painel Movimento)",
                cx.state.clips.len()
            )));
        });

    egui::CollapsingHeader::new("LOD, colisão e run")
        .default_open(false)
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.label("rig_max_level");
                let mut level = config.lod.rig_max_level.unwrap_or(2);
                if ui
                    .add(egui::DragValue::new(&mut level).range(0..=2))
                    .changed()
                {
                    config.lod.rig_max_level = Some(level);
                    *changed = true;
                }
            });
            ui.horizontal(|ui| {
                ui.label("collision");
                let mut mode = config.collision.mode.clone().unwrap_or_default();
                let text = if mode.is_empty() {
                    "auto".to_string()
                } else {
                    mode.clone()
                };
                egui::ComboBox::from_id_salt("collision_mode")
                    .selected_text(text)
                    .show_ui(ui, |ui| {
                        if ui.selectable_value(&mut mode, String::new(), "auto").clicked() {
                            *changed = true;
                        }
                        for option in ["hull", "envelope", "mesh"] {
                            if ui
                                .selectable_value(&mut mode, option.to_string(), option)
                                .clicked()
                            {
                                *changed = true;
                            }
                        }
                    });
                let next = (!mode.is_empty()).then_some(mode);
                if next != config.collision.mode {
                    config.collision.mode = next;
                    *changed = true;
                }
            });
            ui.horizontal(|ui| {
                ui.label("max_faces");
                *changed |= opt_u32(ui, &mut config.collision.max_faces, 32..=4096);
                ui.label("inflate");
                *changed |= opt_f32(ui, &mut config.collision.inflate, 0.0..=0.5, 0.005);
            });
            *changed |= ui
                .checkbox(&mut config.run.fail_fast, "parar no primeiro erro")
                .changed();
        });
}

fn opt_text(ui: &mut egui::Ui, value: &mut Option<String>, hint: &str) -> bool {
    let mut text = value.clone().unwrap_or_default();
    let response = ui.add(
        egui::TextEdit::singleline(&mut text)
            .desired_width(150.0)
            .hint_text(hint),
    );
    if response.changed() {
        *value = (!text.trim().is_empty()).then(|| text.trim().to_string());
        true
    } else {
        false
    }
}

fn opt_u32(ui: &mut egui::Ui, value: &mut Option<u32>, range: std::ops::RangeInclusive<u32>) -> bool {
    let mut has = value.is_some();
    let mut current = value.unwrap_or(*range.start());
    let mut changed = false;
    if ui.checkbox(&mut has, "").changed() {
        *value = has.then_some(current);
        changed = true;
    }
    if has && ui.add(egui::DragValue::new(&mut current).range(range)).changed() {
        *value = Some(current);
        changed = true;
    }
    changed
}

fn opt_i32(ui: &mut egui::Ui, value: &mut Option<i32>, range: std::ops::RangeInclusive<i32>) -> bool {
    let mut has = value.is_some();
    let mut current = value.unwrap_or(0);
    let mut changed = false;
    if ui.checkbox(&mut has, "").changed() {
        *value = has.then_some(current);
        changed = true;
    }
    if has && ui.add(egui::DragValue::new(&mut current).range(range)).changed() {
        *value = Some(current);
        changed = true;
    }
    changed
}

fn opt_f32(
    ui: &mut egui::Ui,
    value: &mut Option<f32>,
    range: std::ops::RangeInclusive<f32>,
    speed: f32,
) -> bool {
    let mut has = value.is_some();
    let mut current = value.unwrap_or(*range.start());
    let mut changed = false;
    if ui.checkbox(&mut has, "").changed() {
        *value = has.then_some(current);
        changed = true;
    }
    if has
        && ui
            .add(
                egui::DragValue::new(&mut current)
                    .speed(speed)
                    .range(range),
            )
            .changed()
    {
        *value = Some(current);
        changed = true;
    }
    changed
}

fn tri_bool(ui: &mut egui::Ui, label: &str, value: &mut Option<bool>) -> bool {
    ui.horizontal(|ui| {
        ui.label(label);
        let mut current = value.unwrap_or(false);
        let mut has = value.is_some();
        if ui.checkbox(&mut has, "definir").changed() {
            *value = has.then_some(current);
            return true;
        }
        if has && ui.checkbox(&mut current, "sim").changed() {
            *value = Some(current);
            return true;
        }
        false
    })
    .inner
}
