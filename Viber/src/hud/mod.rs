//! HUD screen elements styled after the original simple-rpg DOM
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
//! * [`compass`] / [`minimap`] — the animated widgets.

pub mod assets;
pub mod compass;
pub mod elements;
pub mod interact;
pub mod map;
pub mod menu;
pub mod minimap;
pub mod vitals;
pub mod waypoint;
pub mod widgets;

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
pub use map::{
    MAP_PANEL_PX, MapSpot, MapView, PIN_PICK_RADIUS_PX, RegionalMapPlugin, RegionalMapState,
    VILLAGE_CLEAR_M, pick_pin, region_hidden, reveal_key, zoom_half_m, zoom_label,
};
pub use minimap::{
    MinimapAnchor, MinimapArrow, MinimapDot, MinimapLandmark, MinimapRange, MinimapTower,
    arrow_rotation_rad, hud_minimap_landmarks_update, hud_minimap_update, minimap_xy,
};
pub use vitals::{
    HudHealthFill, HudHealthLabel, HudXpFill, HudXpLabel, health_label_text, hud_health_sync,
    hud_xp_sync, xp_label_text,
};

/// Painéis do HUD que só fazem sentido no MUNDO aberto (bússola, minimapa):
/// dentro de uma sala da bolsa de interior mostravam distâncias de 2,6 km e
/// um mapa vazio.
#[derive(bevy::prelude::Component, Debug, Clone, Copy, Default)]
pub struct HideInInterior;

/// Esconde/repõe os [`HideInInterior`] quando o herói entra/sai de um interior.
pub fn hide_world_hud_in_interior(
    interior: Option<bevy::prelude::Res<crate::worldsys::InteriorLighting>>,
    cinematic: Option<bevy::prelude::Res<crate::cinematic::CinematicPlayback>>,
    mut panels: bevy::prelude::Query<
        &mut bevy::prelude::Visibility,
        bevy::prelude::With<HideInInterior>,
    >,
) {
    use bevy::prelude::{DetectChangesMut, Visibility};
    // Também durante uma cinemática (este sistema repunha-os por cima do
    // esconder-HUD da cena).
    let inside = interior.as_deref().is_some_and(|state| state.active)
        || cinematic.as_deref().is_some_and(|c| c.playing().is_some());
    let wanted = if inside {
        Visibility::Hidden
    } else {
        Visibility::Inherited
    };
    for mut visibility in &mut panels {
        visibility.set_if_neq(wanted);
    }
}
