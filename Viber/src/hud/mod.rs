//! HUD screen elements styled after the original VibeGame simple-rpg DOM
//! interface — with the AAA presentation layer: a display font (Cinzel),
//! gradient panels with drop shadows, authored vector icons (heart, coin,
//! log, stone, action glyphs), a real minimap arrow with numbered quest
//! dots, and a ticked compass with per-sector waypoint distances.
//!
//! Modules by domain:
//! * [`assets`] — display font, generated textures, panel palette, text
//!   primitives;
//! * [`elements`] — every world-tag builder (`HealthBar`, `Minimap`, …) and
//!   the DOM-parity widgets;
//! * [`vitals`] — health/xp fill mirroring;
//! * [`interact`] — prompt, dialogue balloon, key-toggled panels;
//! * [`compass`] / [`minimap`] / [`nametags`] — the animated widgets.

pub mod assets;
pub mod compass;
pub mod elements;
pub mod interact;
pub mod minimap;
pub mod nametags;
pub mod vitals;

pub use assets::HudAssets;
pub use compass::{
    CompassDistance, CompassLetter, CompassTick, compass_offset_px, heading_bearing_deg,
    sector_distance, world_bearing_deg,
};
pub use elements::{spawn_hud, spawn_resource_chip};
pub use interact::{
    BALLOON_DURATION, BALLOON_RANGE_M, HudBalloon, HudPrompt, HudToggle, balloon_tick,
    hud_balloon_update, hud_prompt_update, hud_toggle,
};
pub use minimap::{
    MinimapArrow, MinimapDot, MinimapRange, arrow_rotation_rad, hud_minimap_update, minimap_xy,
};
pub use nametags::{NAME_TAG_MAX_M, NAME_TAG_MIN_M, NAME_TAG_POOL, NameTag, hud_nametags_update};
pub use vitals::{
    HudHealthFill, HudHealthLabel, HudXpFill, HudXpLabel, health_label_text, hud_health_sync,
    hud_xp_sync, xp_label_text,
};
