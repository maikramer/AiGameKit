//! The editor's look: one palette, one type scale, and the font-chain repair.
//!
//! **Why the repair exists.** feathers propagates its font (`InheritableFont` →
//! `Propagate<TextFont>`) only through entities that carry its `ThemedText` marker — the
//! propagation filter must match EVERY node between the source and the text. Any plain
//! `Node` wrapper (an icon + label row, a tab caption, the status bar) cuts the chain, and
//! the label falls back to Bevy's default 20 px white font. That was the toolbar/tab/status
//! text rendering bigger and brighter than the docks. [`repair_text_fonts`] catches every
//! themed label left without an inherited font and gives it the editor's body font.

use bevy::app::{Inherited, PropagateSet};
use bevy::feathers::constants::fonts;
use bevy::feathers::theme::ThemedText;
use bevy::prelude::*;
use bevy::text::{FontSize, FontWeight, TextFont};

/// Window background (behind the docks).
pub const BG_WINDOW: Color = Color::srgb(0.086, 0.094, 0.110);
/// Dock body background.
pub const BG_PANEL: Color = Color::srgb(0.118, 0.129, 0.149);
/// Dock headers, toolbar, tab bar.
pub const BG_HEADER: Color = Color::srgb(0.141, 0.153, 0.176);
/// Overlay chrome over the viewport (tool strip, mode strip).
pub const BG_OVERLAY: Color = Color::srgba(0.07, 0.078, 0.094, 0.92);
/// Hairlines between areas.
pub const BORDER: Color = Color::srgb(0.204, 0.220, 0.255);
/// Floating popovers (menus, dialogs).
pub const BG_POPOVER: Color = Color::srgb(0.106, 0.114, 0.133);
/// Popover / overlay outline (a step brighter than [`BORDER`]).
pub const BORDER_STRONG: Color = Color::srgb(0.31, 0.34, 0.40);
/// Accent (active tab underline, focus, primary).
pub const ACCENT: Color = Color::srgb(0.27, 0.56, 0.98);
/// Body text.
pub const TEXT: Color = Color::srgb(0.85, 0.87, 0.90);
/// Secondary text (captions, inactive tabs).
pub const TEXT_DIM: Color = Color::srgb(0.58, 0.62, 0.68);

/// The body font size (labels, buttons, tabs).
pub const FONT_BODY: f32 = 13.0;
/// Toolbar height.
pub const TOOLBAR_PX: f32 = 40.0;
/// Status bar height.
pub const STATUS_PX: f32 = 24.0;
/// Tab bar height.
pub const TABBAR_PX: f32 = 30.0;

static MONO: std::sync::OnceLock<Handle<Font>> = std::sync::OnceLock::new();

/// The monospace font (logs, console) at `size` px — loaded by [`ThemePlugin`].
pub fn mono(size: f32) -> TextFont {
    TextFont {
        font: MONO.get().cloned().unwrap_or_default().into(),
        font_size: FontSize::Px(size),
        ..default()
    }
}

/// A dim hint line (empty states).
pub fn hint(text: impl Into<String>) -> impl Bundle {
    (
        Text::new(text),
        TextFont::from_font_size(12.0),
        TextColor(TEXT_DIM),
        FontRepaired,
        Pickable::IGNORE,
    )
}

/// A label that already received the fallback font (so it is not re-written every frame).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct FontRepaired;

/// Themed labels that did not inherit feathers' font (chain cut by a plain wrapper node) get
/// the editor's body font and text color. Runs after feathers' propagation, so a label whose
/// chain is intact keeps the inherited style untouched.
#[allow(clippy::type_complexity)]
pub fn repair_text_fonts(
    mut commands: Commands,
    orphans: Query<
        (Entity, Has<Inherited<TextColor>>),
        (
            With<Text>,
            With<ThemedText>,
            Without<Inherited<TextFont>>,
            Without<FontRepaired>,
        ),
    >,
    server: Res<AssetServer>,
) {
    for (entity, has_color) in &orphans {
        let font: Handle<Font> = server.load(fonts::REGULAR);
        let mut entity = commands.entity(entity);
        entity.try_insert((
            TextFont {
                font: font.into(),
                font_size: FontSize::Px(FONT_BODY),
                weight: FontWeight::NORMAL,
                ..default()
            },
            FontRepaired,
        ));
        if !has_color {
            entity.try_insert(TextColor(TEXT));
        }
    }
}

/// A tab pane: fills the bottom dock's body area (column).
pub fn pane() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            flex_grow: 1.0,
            min_height: px(0),
            min_width: px(0),
            flex_direction: FlexDirection::Column,
        }
    }
}

/// A tab pane's content area: grows into the remaining height, padded, on the panel color.
pub fn pane_body() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            flex_grow: 1.0,
            min_height: px(0),
            min_width: px(0),
            flex_direction: FlexDirection::Column,
            row_gap: px(4),
            padding: px(6),
        }
    }
}

/// Plain `Text` spawned by the editor's own code without a font of its own keeps Bevy's
/// default (embedded mono SUBSET, no accents; 1 rem ≈ 20 px when no size either) — the World Doc summary, log lines… Those
/// under the editor root get the body font. The game's UI (also in this world) is left
/// alone: only descendants of [`crate::ui::EditorRoot`] are touched.
#[allow(clippy::type_complexity)]
pub fn repair_default_fonts(
    mut commands: Commands,
    added: Query<(Entity, &TextFont), (Added<Text>, Without<FontRepaired>)>,
    parents: Query<&ChildOf>,
    roots: Query<(), With<crate::ui::EditorRoot>>,
    server: Res<AssetServer>,
) {
    for (entity, text_font) in &added {
        // A fonte default embutida do Bevy é um SUBSET mono sem acentos (ã, é, —, …
        // saíam como glifo em falta): todo o texto do editor sem fonte própria passa
        // à Fira Sans; o tamanho pedido mantém-se (o default 1 rem vira o corpo).
        let default_font = matches!(&text_font.font, bevy::text::FontSource::Handle(handle) if *handle == Handle::default());
        if !default_font {
            continue;
        }
        let in_editor = parents
            .iter_ancestors(entity)
            .any(|ancestor| roots.contains(ancestor));
        if !in_editor {
            continue;
        }
        let font: Handle<Font> = server.load(fonts::REGULAR);
        let font_size = if text_font.font_size == FontSize::Rem(1.0) {
            FontSize::Px(FONT_BODY)
        } else {
            text_font.font_size
        };
        commands.entity(entity).try_insert((
            TextFont {
                font: font.into(),
                font_size,
                ..text_font.clone()
            },
            FontRepaired,
        ));
    }
}

pub struct ThemePlugin;

impl Plugin for ThemePlugin {
    fn build(&self, app: &mut App) {
        MONO.get_or_init(|| app.world().resource::<AssetServer>().load(fonts::MONO));
        app.add_systems(
            PostUpdate,
            (repair_text_fonts, repair_default_fonts).after(PropagateSet::<TextFont>::default()),
        );
    }
}
