//! Tema da app (escuro, acento âmbar — linguagem do Vael/ArtCraft).

use bevy_egui::egui::{self, Color32};

pub const ACCENT: Color32 = Color32::from_rgb(224, 168, 78);
pub const ACCENT_DIM: Color32 = Color32::from_rgb(130, 98, 46);
pub const OK: Color32 = Color32::from_rgb(120, 190, 120);
pub const WARN: Color32 = Color32::from_rgb(226, 178, 84);
pub const ERROR: Color32 = Color32::from_rgb(226, 106, 96);
pub const MUTED: Color32 = Color32::from_rgb(150, 156, 166);
pub const PANEL: Color32 = Color32::from_rgb(22, 24, 28);
pub const PANEL_HIGH: Color32 = Color32::from_rgb(30, 33, 38);
pub const BG: Color32 = Color32::from_rgb(14, 15, 18);

pub fn apply(ctx: &egui::Context) {
    ctx.set_theme(egui::ThemePreference::Dark);
    let mut style = (*ctx.style_of(egui::Theme::Dark)).clone();
    let mut visuals = egui::Visuals::dark();
    visuals.panel_fill = PANEL;
    visuals.window_fill = PANEL_HIGH;
    visuals.faint_bg_color = PANEL_HIGH;
    visuals.extreme_bg_color = Color32::from_rgb(10, 11, 13);
    visuals.selection.bg_fill = ACCENT_DIM;
    visuals.selection.stroke.color = ACCENT;
    visuals.widgets.noninteractive.bg_fill = PANEL_HIGH;
    visuals.widgets.inactive.bg_fill = Color32::from_rgb(38, 41, 48);
    visuals.widgets.inactive.weak_bg_fill = Color32::from_rgb(32, 35, 41);
    visuals.widgets.hovered.bg_fill = Color32::from_rgb(52, 56, 64);
    visuals.widgets.active.bg_fill = ACCENT_DIM;
    visuals.widgets.active.bg_stroke.color = ACCENT;
    visuals.widgets.hovered.bg_stroke.color = ACCENT_DIM;
    visuals.hyperlink_color = ACCENT;
    style.visuals = visuals;
    style.spacing.item_spacing = egui::vec2(8.0, 6.0);
    style.spacing.button_padding = egui::vec2(10.0, 4.0);
    style.spacing.interact_size.y = 24.0;
    style
        .text_styles
        .insert(egui::TextStyle::Heading, egui::FontId::proportional(17.0));
    style
        .text_styles
        .insert(egui::TextStyle::Body, egui::FontId::proportional(13.0));
    style
        .text_styles
        .insert(egui::TextStyle::Button, egui::FontId::proportional(13.0));
    style
        .text_styles
        .insert(egui::TextStyle::Small, egui::FontId::proportional(11.0));
    style
        .text_styles
        .insert(egui::TextStyle::Monospace, egui::FontId::monospace(11.5));
    ctx.set_style_of(egui::Theme::Dark, style);
}

pub fn heading(text: impl Into<String>) -> egui::RichText {
    egui::RichText::new(text).strong().size(13.0)
}

pub fn muted(text: impl Into<String>) -> egui::RichText {
    egui::RichText::new(text).color(MUTED).size(11.0)
}

pub fn chip(text: impl Into<String>, color: Color32) -> egui::RichText {
    egui::RichText::new(text)
        .color(color)
        .size(11.0)
        .monospace()
}
