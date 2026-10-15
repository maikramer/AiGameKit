//! Font Awesome 6 (solid) icons drawn as text glyphs from an embedded font.
//!
//! The font lives at `assets/fonts/fa-solid-900.ttf` (Font Awesome Free — icons CC BY 4.0,
//! fonts SIL OFL 1.1; see `fa-solid-900.LICENSE.txt`). Icons are plain `Text` entities
//! WITHOUT feathers' `ThemedText` marker: `TextFont`/`TextColor` propagation is filtered on
//! `ThemedText`, so the glyph keeps the icon font while sibling labels keep the theme font.

use bevy::asset::AssetServer;
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::text::{FontSource, FontSourceTemplate, TextFont};
use std::sync::OnceLock;

/// Embedded path of the icon font.
pub const FONT_PATH: &str = "embedded://vibereditor/assets/fonts/fa-solid-900.ttf";

/// Default icon color: matches feathers' light UI text.
pub const ICON: Color = Color::srgb(0.82, 0.85, 0.9);
/// Dimmed icon color (secondary chrome).
pub const ICON_DIM: Color = Color::srgb(0.6, 0.64, 0.7);

static FONT: OnceLock<Handle<Font>> = OnceLock::new();

/// Loads the embedded icon font once; called from [`crate::ui::EditorUiPlugin`].
pub fn load_font(server: &AssetServer) -> Handle<Font> {
    FONT.get_or_init(|| server.load(FONT_PATH)).clone()
}

/// The icon font as a `FontSource`; the default handle (renders nothing) before [`load_font`].
pub fn font() -> FontSource {
    FontSource::Handle(FONT.get().cloned().unwrap_or_default())
}

/// Font Awesome 6 Free solid codepoints, validated against the shipped font's cmap.
///
/// Kept as a catalog: not every glyph is used yet, but each entry is verified against
/// `fa-solid-900.ttf`'s cmap, so new UI can reach for them without re-checking codepoints.
#[allow(dead_code)]
pub mod fa {
    pub const PLAY: char = '\u{f04b}';
    pub const PAUSE: char = '\u{f04c}';
    pub const STOP: char = '\u{f04d}';
    pub const FLOPPY_DISK: char = '\u{f0c7}';

    // Gizmo tools.
    pub const ARROW_POINTER: char = '\u{f245}';
    pub const ARROWS_UP_DOWN_LEFT_RIGHT: char = '\u{f047}';
    pub const UP_RIGHT_AND_DOWN_LEFT_FROM_CENTER: char = '\u{f424}';
    pub const ROTATE_RIGHT: char = '\u{f2f9}';
    pub const ROTATE_LEFT: char = '\u{f2ea}';
    pub const CROSSHAIRS: char = '\u{f05b}';
    pub const GLOBE: char = '\u{f0ac}';
    pub const CUBE: char = '\u{f1b2}';
    pub const CUBES: char = '\u{f1b3}';
    pub const MAGNIFYING_GLASS: char = '\u{f002}';

    // Docks / panels.
    pub const SITEMAP: char = '\u{f0e8}';
    pub const SLIDERS: char = '\u{f1de}';
    pub const TERMINAL: char = '\u{f120}';
    pub const FILE_LINES: char = '\u{f15c}';
    pub const MAP: char = '\u{f279}';

    // Tree / inspector chrome.
    pub const CHEVRON_RIGHT: char = '\u{f054}';
    pub const CHEVRON_DOWN: char = '\u{f078}';
    pub const PLUS: char = '\u{2b}';
    pub const WAND_MAGIC_SPARKLES: char = '\u{e2ca}';
    pub const ANGLE_RIGHT: char = '\u{f105}';
    pub const TAGS: char = '\u{f02c}';

    // World tags.
    pub const FOLDER: char = '\u{f07b}';
    pub const FILE_IMPORT: char = '\u{f56f}';
    pub const LINK: char = '\u{f0c1}';
    pub const LAYER_GROUP: char = '\u{f5fd}';
    pub const CLONE: char = '\u{f24d}';
    pub const CIRCLE: char = '\u{f111}';
    pub const DATABASE: char = '\u{f1c0}';
    pub const SQUARE: char = '\u{f0c8}';
    pub const PILLS: char = '\u{f484}';
    pub const LIGHTBULB: char = '\u{f0eb}';
    pub const SUN: char = '\u{f185}';
    pub const CLOUD_SUN: char = '\u{f6c4}';
    pub const VIDEO: char = '\u{f03d}';
    pub const CLOUD: char = '\u{f0c2}';
    pub const MOUNTAIN_SUN: char = '\u{e52f}';
    pub const MOUNTAIN: char = '\u{f6fc}';
    pub const MOUNTAIN_CITY: char = '\u{e52e}';
    pub const TABLE_CELLS: char = '\u{f00a}';
    pub const WATER: char = '\u{f773}';
    pub const WATER_LADDER: char = '\u{f5c5}';
    pub const ROAD: char = '\u{f018}';
    pub const NETWORK_WIRED: char = '\u{f6ff}';
    pub const ARCHWAY: char = '\u{f557}';
    pub const DUNGEON: char = '\u{f6d9}';
    pub const BAN: char = '\u{f05e}';
    pub const LOCATION_DOT: char = '\u{f3c5}';
    pub const SEEDLING: char = '\u{f4d8}';
    pub const DISPLAY: char = '\u{e163}';
    pub const PAINTBRUSH: char = '\u{f1fc}';
    pub const GEARS: char = '\u{f085}';
    pub const CIRCLE_NODES: char = '\u{e4e2}';
    pub const DOOR_OPEN: char = '\u{f52b}';
    pub const BOLT: char = '\u{f0e7}';
    pub const EYE: char = '\u{f06e}';
    pub const MUSIC: char = '\u{f001}';
    pub const PERSON: char = '\u{f183}';
    pub const TREE: char = '\u{f1bb}';
    pub const STORE: char = '\u{f54e}';
    pub const DRAGON: char = '\u{f6d5}';
    pub const SKULL: char = '\u{f54c}';
    pub const GHOST: char = '\u{f6e2}';
    pub const COMPASS: char = '\u{f14e}';
    pub const FIRE: char = '\u{f06d}';
    pub const PAW: char = '\u{f1b0}';
    pub const SPIDER: char = '\u{f717}';
    pub const BAG_SHOPPING: char = '\u{f290}';
    pub const TOWER_OBSERVATION: char = '\u{e586}';
    pub const GEM: char = '\u{f3a5}';

    // Editor chrome.
    pub const STAMP: char = '\u{f5bf}';

    pub const CLOCK: char = '\u{f017}';
    pub const CLOUD_RAIN: char = '\u{f73d}';

    pub const GAUGE_HIGH: char = '\u{f625}';
    pub const LOCATION_CROSSHAIRS: char = '\u{f601}';
    pub const OBJECT_GROUP: char = '\u{f247}';

    // Status.
    pub const CIRCLE_CHECK: char = '\u{f058}';
    pub const CIRCLE_XMARK: char = '\u{f057}';
    pub const TRIANGLE_EXCLAMATION: char = '\u{f071}';
    pub const INFO: char = '\u{f129}';
    pub const CIRCLE_QUESTION: char = '\u{f059}';
}

/// Embedded text2d-generated icons (see `assets/icons/`; produced with
/// `text2d generate --category icon --transparent`).
pub const LOGO_ICON: &str = "embedded://vibereditor/assets/icons/logo.png";
pub const PICK_ICON: &str = "embedded://vibereditor/assets/icons/pick.png";
pub const WORLD_ICON: &str = "embedded://vibereditor/assets/icons/world.png";

/// A raster icon (text2d-generated) shown at `side` px, for `bsn!` children lists.
pub fn image_icon(path: &'static str, side: f32) -> impl Scene {
    bsn! {
        Node { width: {px(side)}, height: {px(side)} }
        ImageNode { image: path }
    }
}

/// A bare icon glyph, for `bsn!` children lists (`@icon(fa::PLAY, 14.0, icons::ICON)`).
pub fn icon(code: char, size: f32, color: Color) -> impl Scene {
    let glyph = code.to_string();
    bsn! {
        Text(glyph)
        TextFont { font: FontSourceTemplate::Handle(FONT_PATH), font_size: {FontSize::Px(size)} }
        TextColor(color)
    }
}

/// An icon next to a themed label, for button captions.
pub fn icon_caption(code: char, label: &str, color: Color) -> impl Scene {
    bsn! {
        Node {
            column_gap: px(6),
            align_items: AlignItems::Center,
        }
        Children [
            @icon(code, 13.0, color)
            --
            @caption(label)
        ]
    }
}

/// An icon glyph as a spawnable bundle, for `commands.spawn` UI code.
pub fn icon_text(code: char, size: f32, color: Color) -> (Text, TextFont, TextColor) {
    (
        Text::new(code.to_string()),
        TextFont {
            font: font(),
            font_size: FontSize::Px(size),
            ..default()
        },
        TextColor(color),
    )
}

/// The icon glyph for a world XML tag (case-insensitive); unknown tags get the generic marker.
pub fn tag_icon(tag: &str) -> char {
    use fa::*;
    match tag.to_ascii_lowercase().as_str() {
        "entity" => CUBE,
        "group" => FOLDER,
        "include" => FILE_IMPORT,
        "composition" => LAYER_GROUP,
        "prototype" => CLONE,
        "use" => LINK,
        "cuboid" | "box" => CUBE,
        "sphere" => CIRCLE,
        "cylinder" => DATABASE,
        "plane" => SQUARE,
        "capsule" => PILLS,
        "pointlight" => LIGHTBULB,
        "directionallight" => SUN,
        "ambientlight" => CLOUD_SUN,
        "orbitcamera" | "camera" => VIDEO,
        "sky" => CLOUD,
        "terrain" => MOUNTAIN_SUN,
        "terrainpad" | "pad" => TABLE_CELLS,
        "lake" => WATER,
        "river" => WATER_LADDER,
        "road" => ROAD,
        "roadnetwork" => NETWORK_WIRED,
        "cliff" => MOUNTAIN,
        "arch" => ARCHWAY,
        "cave" => DUNGEON,
        "exclusion" => BAN,
        "spawner" => LOCATION_DOT,
        "scatter" => SEEDLING,
        "uiroot" => DISPLAY,
        "uistyle" => PAINTBRUSH,
        "engineconfig" => GEARS,
        "navmesh" => CIRCLE_NODES,
        "spawngate" => DOOR_OPEN,
        "projectiletemplate" => BOLT,
        "adaptivequality" => EYE,
        "postfx" | "postfxdebugtoggle" => EYE,
        "music" | "audio" => MUSIC,
        "player" => PERSON,
        _ => TAGS,
    }
}

/// A subtle category color for a tag's icon, so the tree scans by kind at a glance.
pub fn tag_color(tag: &str) -> Color {
    const GRAY: Color = Color::srgb(0.72, 0.76, 0.82);
    const AMBER: Color = Color::srgb(0.95, 0.78, 0.42);
    const BLUE: Color = Color::srgb(0.48, 0.7, 0.95);
    const GREEN: Color = Color::srgb(0.55, 0.8, 0.55);
    const RED: Color = Color::srgb(0.92, 0.5, 0.48);
    const PURPLE: Color = Color::srgb(0.75, 0.6, 0.95);
    match tag.to_ascii_lowercase().as_str() {
        "pointlight" | "directionallight" | "ambientlight" => AMBER,
        "lake" | "river" | "sky" | "cloud" => BLUE,
        "terrain" | "terrainpad" | "scatter" | "spawner" | "tree" => GREEN,
        "cliff" | "cave" | "arch" => RED,
        "uiroot" | "uistyle" | "prototype" | "use" | "composition" => PURPLE,
        _ => GRAY,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_tags_map_to_their_icons() {
        assert_eq!(tag_icon("Entity"), fa::CUBE);
        assert_eq!(tag_icon("TERRAINPAD"), fa::TABLE_CELLS);
        assert_eq!(tag_icon("OrbitCamera"), fa::VIDEO);
        assert_eq!(tag_icon("PointLight"), fa::LIGHTBULB);
    }

    #[test]
    fn unknown_tags_fall_back_to_the_generic_marker() {
        assert_eq!(tag_icon("SomethingNew"), fa::TAGS);
    }

    #[test]
    fn tag_colors_are_distinct_per_category() {
        let light = tag_color("PointLight");
        let water = tag_color("Lake");
        let ui = tag_color("UiRoot");
        assert_ne!(light, water);
        assert_ne!(water, ui);
    }
}
