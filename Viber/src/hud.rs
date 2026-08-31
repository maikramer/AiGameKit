//! HUD screen elements styled after the original VibeGame simple-rpg DOM
//! interface: rounded dark panels with a heart health bar and level badge,
//! resource chip slots, circular minimap with rotating player arrow and quest
//! dots, a sliding compass strip, world-anchored NPC name tags, interaction
//! prompt, dialogue balloon, help bar and action slots — all `bevy_ui`.
//!
//! The `healthbar`/`xpbar` fills are dynamic: [`hud_health_sync`] /
//! [`hud_xp_sync`] mirror the player's [`Health`] / [`Xp`] (see `vitals`)
//! into the fill width and label text every frame. [`hud_balloon_update`]
//! pops the dialogue balloon on `E` near a `<DialogueNPC>` for a timed 4 s.
//! [`hud_compass_update`], [`hud_minimap_update`] and [`hud_nametags_update`]
//! animate the compass, the minimap and the world-anchored name tags.

use bevy::math::Rot2;
use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::{DialogueNpc, OrbitCamera};
use crate::vitals::{Health, Xp, health_fraction, xp_fraction};

/// Marker for the interaction prompt node (shown near a `<DialogueNPC>`).
#[derive(Component)]
pub struct HudPrompt {
    /// Range in meters at which the prompt appears (authored on the tag).
    pub range: f32,
}

/// Marker for the healthbar fill node (width mirrors the player's `Health`).
#[derive(Component)]
pub struct HudHealthFill;

/// Marker for the healthbar label node ("100/100").
#[derive(Component)]
pub struct HudHealthLabel;

/// Marker for the xpbar fill node (width mirrors the player's `Xp`).
#[derive(Component)]
pub struct HudXpFill;

/// Marker for the xpbar label node ("0/100", dim, right-aligned).
#[derive(Component)]
pub struct HudXpLabel;

/// Dialogue balloon state (`E` near a `<DialogueNPC>` shows it for a while).
#[derive(Component)]
pub struct HudBalloon {
    /// Seconds left before the balloon hides again.
    pub timer: f32,
}

/// How long the balloon stays visible after an interaction (s).
pub const BALLOON_DURATION: f32 = 4.0;
/// Interaction range for the balloon, matching `player::dialogue_interaction`.
pub const BALLOON_RANGE_M: f32 = 3.5;

/// Marker for nodes toggled by a key (e.g. `<TabbedModal key="q">`).
#[derive(Component)]
pub struct HudToggle(pub KeyCode);

/// One direction letter in the sliding compass strip.
#[derive(Component)]
pub struct CompassLetter {
    /// World bearing this letter sits at (0° = north/−Z, 90° = east/+X).
    pub bearing_deg: f32,
}

/// The minimap player arrow (rotation mirrors the camera heading).
#[derive(Component)]
pub struct MinimapArrow;

/// A quest dot on the minimap (positioned at a nearby NPC's world spot).
#[derive(Component)]
pub struct MinimapDot;

/// Radius of the minimap in world meters (authored `range` attribute).
#[derive(Component)]
pub struct MinimapRange(pub f32);

/// A world-anchored NPC name tag pill from the pooled set.
#[derive(Component)]
pub struct NameTag;

/// How many name-tag pills are kept in the pool (reassigned per frame).
pub const NAME_TAG_POOL: usize = 8;
/// Name tags show for NPCs between these distance bounds (meters).
pub const NAME_TAG_MIN_M: f32 = 2.0;
pub const NAME_TAG_MAX_M: f32 = 60.0;

// ------------------------------------------------------------- palette

/// Warm dark translucent panel background shared by every HUD pill.
fn panel_bg() -> BackgroundColor {
    BackgroundColor(Color::srgba(0.09, 0.09, 0.08, 0.85))
}

fn attr<'a>(attrs: &'a [(String, String)], name: &str) -> Option<&'a str> {
    attrs
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

fn label(text: impl Into<String>, size: f32, color: Color) -> impl Bundle {
    (
        Text::new(text.into()),
        TextColor(color),
        TextFont::from_font_size(size),
    )
}

/// An absolutely-positioned child centered on its parent anchor point via a
/// −50 % self translation (compass letters, minimap dots).
fn centered_at(left: Val, top: Val) -> Node {
    Node {
        position_type: PositionType::Absolute,
        left,
        top,
        ..Default::default()
    }
}

/// Build every deferred HUD element. `tag` is the lowercased original tag.
pub fn spawn_hud(world: &mut World, tag: &str, attrs: &[(String, String)]) {
    match tag {
        "hudscreenlayer" => {
            // Root layer + the two widgets the original renders from the DOM
            // (help pill and action slots) that have no dedicated tag.
            world.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    top: Val::Px(0.0),
                    left: Val::Px(0.0),
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    ..Default::default()
                },
                Name::new("hud:layer"),
            ));
            help_bar(world);
            action_slots(world);
            name_tag_pool(world);
        }
        "healthbar" => {
            // Rounded panel: heart icon + green bar with "100/100" inside.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(12.0),
                        left: Val::Px(12.0),
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(10.0),
                        padding: UiRect::axes(Val::Px(10.0), Val::Px(8.0)),
                        border: UiRect::all(Val::Px(2.0)),
                        border_radius: BorderRadius::all(Val::Px(14.0)),
                        ..Default::default()
                    },
                    panel_bg(),
                    BorderColor::all(Color::srgba(0.0, 0.0, 0.0, 0.55)),
                    Name::new("hud:health"),
                ))
                .with_children(|panel| {
                    // Heart icon (vector): red rounded disc on a dark ring.
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(26.0),
                                height: Val::Px(26.0),
                                border_radius: BorderRadius::all(Val::Px(9.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.82, 0.12, 0.14)),
                            Outline::new(Val::Px(2.0), Val::ZERO, Color::srgb(0.35, 0.05, 0.05)),
                        ))
                        .with_children(|heart| {
                            // Glint: small lighter square to suggest a shine.
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(5.0),
                                    top: Val::Px(4.0),
                                    width: Val::Px(7.0),
                                    height: Val::Px(7.0),
                                    border_radius: BorderRadius::all(Val::Px(3.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(1.0, 0.55, 0.55, 0.85)),
                            ));
                        });
                    // Bar track with the green fill + centered label.
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(170.0),
                                height: Val::Px(20.0),
                                padding: UiRect::all(Val::Px(2.0)),
                                border_radius: BorderRadius::all(Val::Px(9.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.55)),
                        ))
                        .with_children(|track| {
                            track
                                .spawn((
                                    Node {
                                        width: Val::Percent(100.0),
                                        height: Val::Percent(100.0),
                                        border_radius: BorderRadius::all(Val::Px(7.0)),
                                        ..Default::default()
                                    },
                                    BackgroundColor(Color::srgb(0.28, 0.72, 0.22)),
                                    HudHealthFill,
                                ))
                                .with_children(|fill| {
                                    fill.spawn((
                                        Node {
                                            justify_content: JustifyContent::Center,
                                            align_items: AlignItems::Center,
                                            width: Val::Percent(100.0),
                                            height: Val::Percent(100.0),
                                            ..Default::default()
                                        },
                                        label("100/100", 13.0, Color::srgb(0.96, 1.0, 0.95)),
                                        HudHealthLabel,
                                    ));
                                });
                        });
                });
        }
        "xpbar" => {
            // Level badge (gold disc) + slim dark bar with a gold fill.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(64.0),
                        left: Val::Px(12.0),
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(10.0),
                        padding: UiRect::axes(Val::Px(10.0), Val::Px(6.0)),
                        border_radius: BorderRadius::all(Val::Px(12.0)),
                        ..Default::default()
                    },
                    panel_bg(),
                    Name::new("hud:xp"),
                ))
                .with_children(|panel| {
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(26.0),
                                height: Val::Px(26.0),
                                justify_content: JustifyContent::Center,
                                align_items: AlignItems::Center,
                                border_radius: BorderRadius::all(Val::Px(13.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.93, 0.72, 0.14)),
                            Outline::new(Val::Px(2.0), Val::ZERO, Color::srgb(0.45, 0.32, 0.05)),
                        ))
                        .with_children(|coin| {
                            coin.spawn(label("1", 13.0, Color::srgb(0.35, 0.24, 0.02)));
                        });
                    panel
                        .spawn((
                            Node {
                                width: Val::Px(170.0),
                                height: Val::Px(12.0),
                                padding: UiRect::all(Val::Px(2.0)),
                                border_radius: BorderRadius::all(Val::Px(6.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.55)),
                        ))
                        .with_children(|track| {
                            track.spawn((
                                Node {
                                    width: Val::Percent(0.0),
                                    height: Val::Percent(100.0),
                                    border_radius: BorderRadius::all(Val::Px(4.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgb(0.9, 0.68, 0.16)),
                                HudXpFill,
                            ));
                        });
                    // Dim numeric readout parked right of the bar.
                    panel.spawn((
                        label(
                            xp_label_text(0, 100),
                            10.0,
                            Color::srgba(1.0, 0.95, 0.8, 0.6),
                        ),
                        HudXpLabel,
                    ));
                });
        }
        "bossbar" => {
            let id = bar(
                world,
                BarSpec {
                    label_text: "BOSS",
                    value: 100,
                    max: 100,
                    fill: Color::srgb(0.45, 0.1, 0.55),
                    text_size: 22.0,
                    width: 420.0,
                    left_px: 0.0,
                    bottom_px: 12.0,
                },
            );
            world.entity_mut(id).insert(Visibility::Hidden);
        }
        "targetbar" => {
            let id = bar(
                world,
                BarSpec {
                    label_text: "sem alvo",
                    value: 0,
                    max: 100,
                    fill: Color::srgb(0.6, 0.2, 0.2),
                    text_size: 14.0,
                    width: 240.0,
                    left_px: 170.0,
                    bottom_px: 10.0,
                },
            );
            world.entity_mut(id).insert(Visibility::Hidden);
        }
        "minimap" => {
            let range = attr(attrs, "range")
                .and_then(|v| v.parse::<f32>().ok())
                .unwrap_or(60.0);
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(14.0),
                        right: Val::Px(14.0),
                        width: Val::Px(148.0),
                        height: Val::Px(148.0),
                        border: UiRect::all(Val::Px(3.0)),
                        border_radius: BorderRadius::MAX,
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.05, 0.08, 0.10, 0.88)),
                    BorderColor::all(Color::srgb(0.82, 0.74, 0.45)),
                    Name::new("hud:minimap"),
                    MinimapRange(range),
                ))
                .with_children(|map| {
                    // North indicator.
                    map.spawn((
                        centered_at(Val::Percent(50.0), Val::Px(6.0)),
                        UiTransform::from_translation(Val2::new(Val::Percent(-50.0), Val::ZERO)),
                        label("N", 14.0, Color::srgb(0.95, 0.78, 0.25)),
                    ));
                    // Player arrow: an asymmetric teardrop rotated by heading.
                    map.spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            left: Val::Percent(50.0),
                            top: Val::Percent(50.0),
                            // Center on the anchor point (taffy honours
                            // margins on absolute nodes; UiTransform
                            // translation proved unreliable here).
                            margin: UiRect::px(-5.0, 0.0, -9.0, 0.0),
                            width: Val::Px(10.0),
                            height: Val::Px(18.0),
                            border_radius: BorderRadius::new(
                                Val::Px(5.0),
                                Val::Px(1.0),
                                Val::Px(5.0),
                                Val::Px(1.0),
                            ),
                            ..Default::default()
                        },
                        UiTransform::IDENTITY,
                        // Pointing tip on top, rounded base: reads as an arrow.
                        BackgroundColor(Color::srgb(0.95, 0.97, 1.0)),
                        Outline::new(Val::Px(1.5), Val::ZERO, Color::srgba(0.0, 0.0, 0.0, 0.6)),
                        MinimapArrow,
                    ));
                    // Reusable quest dots (positioned per frame).
                    for _ in 0..6 {
                        map.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Percent(50.0),
                                top: Val::Percent(50.0),
                                margin: UiRect::px(-4.5, 0.0, -4.5, 0.0),
                                width: Val::Px(9.0),
                                height: Val::Px(9.0),
                                border_radius: BorderRadius::all(Val::Px(4.5)),
                                ..Default::default()
                            },
                            UiTransform::IDENTITY,
                            BackgroundColor(Color::srgb(0.93, 0.72, 0.14)),
                            Outline::new(Val::Px(1.0), Val::ZERO, Color::srgba(0.0, 0.0, 0.0, 0.5)),
                            Visibility::Hidden,
                            MinimapDot,
                        ));
                    }
                });
        }
        "compass" => {
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(10.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Name::new("hud:compass"),
                ))
                .with_children(|wrap| {
                    const DIRECTIONS: [(&str, f32); 8] = [
                        ("N", 0.0),
                        ("NE", 45.0),
                        ("E", 90.0),
                        ("SE", 135.0),
                        ("S", 180.0),
                        ("SW", 225.0),
                        ("W", 270.0),
                        ("NW", 315.0),
                    ];
                    // Letters live INSIDE the strip so their absolute left is
                    // relative to it (wrap is full-width, the strip is not).
                    wrap.spawn((
                        Node {
                            width: Val::Px(460.0),
                            height: Val::Px(30.0),
                            border_radius: BorderRadius::all(Val::Px(15.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45)),
                    ))
                    .with_children(|strip| {
                        for (name, bearing) in DIRECTIONS {
                            let color = if name == "N" {
                                Color::srgb(0.95, 0.78, 0.25)
                            } else {
                                Color::srgb(0.9, 0.9, 0.85)
                            };
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(7.0)),
                                label(name, 13.0, color),
                                CompassLetter {
                                    bearing_deg: bearing,
                                },
                            ));
                        }
                    });
                });
        }
        "interactionprompt" => {
            let key = attr(attrs, "key").unwrap_or("E").to_string();
            let range = attr(attrs, "range")
                .and_then(|v| v.parse::<f32>().ok())
                .unwrap_or(3.5);
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(130.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Visibility::Hidden,
                    Name::new("hud:prompt"),
                ))
                .with_children(|wrap| {
                    wrap.spawn((
                        Node {
                            padding: UiRect::axes(Val::Px(14.0), Val::Px(7.0)),
                            border_radius: BorderRadius::all(Val::Px(14.0)),
                            ..Default::default()
                        },
                        panel_bg(),
                        Text::new(format!("[{key}] Interagir")),
                        TextColor(Color::srgb(1.0, 0.95, 0.8)),
                        TextFont::from_font_size(16.0),
                        HudPrompt { range },
                    ));
                });
        }
        "dialogueballoon" => {
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(180.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Visibility::Hidden,
                    HudBalloon {
                        timer: BALLOON_DURATION,
                    },
                    Name::new("hud:balloon"),
                ))
                .with_children(|wrap| {
                    wrap.spawn((
                        Node {
                            max_width: Val::Px(520.0),
                            padding: UiRect::axes(Val::Px(16.0), Val::Px(9.0)),
                            border_radius: BorderRadius::all(Val::Px(12.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.98, 0.96, 0.9, 0.92)),
                        Text::new("…"),
                        TextColor(Color::srgb(0.15, 0.12, 0.08)),
                        TextFont::from_font_size(15.0),
                    ));
                });
        }
        "tabbedmodal" => {
            let key = attr(attrs, "key").unwrap_or("tab").to_string();
            let title = attr(attrs, "title-key").unwrap_or("modal").to_string();
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(0.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        bottom: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        align_items: AlignItems::Center,
                        ..Default::default()
                    },
                    Visibility::Hidden,
                    HudToggle(toggle_key(&key)),
                    Name::new("hud:modal"),
                ))
                .with_children(|modal| {
                    modal.spawn((
                        Node {
                            width: Val::Px(520.0),
                            height: Val::Px(320.0),
                            padding: UiRect::all(Val::Px(18.0)),
                            border: UiRect::all(Val::Px(2.0)),
                            border_radius: BorderRadius::all(Val::Px(16.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.94)),
                        BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
                        Outline::new(Val::Px(2.0), Val::ZERO, Color::srgb(0.85, 0.75, 0.45)),
                        label(title, 22.0, Color::srgb(0.95, 0.85, 0.6)),
                    ));
                });
        }
        other => {
            bevy::log::warn!("hud: unhandled element `{other}` — skipped");
        }
    }
}

/// Resource chip slot styled like the original: rounded dark slot with a
/// vector icon and a count. `index` is 1-based (row position).
pub fn spawn_resource_chip(world: &mut World, index: usize, resource: &str) {
    // (color, width, height) — gold disc, wood log, stone pebble.
    let (icon_color, icon_w, icon_h) = match resource {
        "gold" => (Color::srgb(0.93, 0.72, 0.14), 13.0_f32, 13.0_f32),
        "wood" => (Color::srgb(0.55, 0.35, 0.15), 16.0, 10.0),
        _ => (Color::srgb(0.6, 0.62, 0.66), 13.0, 11.0),
    };
    let radius = if resource == "wood" {
        BorderRadius::all(Val::Px(4.0))
    } else {
        BorderRadius::all(Val::Px(icon_w / 2.0))
    };
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(106.0),
                left: Val::Px(12.0 + 62.0 * (index - 1) as f32),
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: Val::Px(7.0),
                padding: UiRect::axes(Val::Px(8.0), Val::Px(6.0)),
                border_radius: BorderRadius::all(Val::Px(11.0)),
                ..Default::default()
            },
            panel_bg(),
            Name::new(format!("chip:{resource}")),
        ))
        .with_children(|slot| {
            slot.spawn((
                Node {
                    width: Val::Px(icon_w),
                    height: Val::Px(icon_h),
                    border_radius: radius,
                    ..Default::default()
                },
                BackgroundColor(icon_color),
                Outline::new(Val::Px(1.5), Val::ZERO, Color::srgba(0.0, 0.0, 0.0, 0.5)),
            ));
            slot.spawn(label("0", 15.0, Color::srgb(0.95, 0.93, 0.85)));
        });
}

/// Bottom-center help pill matching the original's key legend.
fn help_bar(world: &mut World) {
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                bottom: Val::Px(10.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                ..Default::default()
            },
            Name::new("hud:help"),
        ))
        .with_children(|wrap| {
            wrap.spawn((
                Node {
                    padding: UiRect::axes(Val::Px(14.0), Val::Px(6.0)),
                    border_radius: BorderRadius::all(Val::Px(13.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.5)),
                label(
                    "WASD mover - Espaco pular - Shift correr - E interagir - Q menu",
                    13.0,
                    Color::srgb(0.92, 0.9, 0.82),
                ),
            ));
        });
}

/// Bottom-left action slots (C/E/R) styled after the original buttons.
fn action_slots(world: &mut World) {
    const SLOTS: [(&str, Color); 3] = [
        ("C", Color::srgb(0.15, 0.5, 0.9)),
        ("E", Color::srgb(0.2, 0.7, 0.3)),
        ("R", Color::srgb(0.9, 0.45, 0.1)),
    ];
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                bottom: Val::Px(12.0),
                left: Val::Px(12.0),
                column_gap: Val::Px(10.0),
                ..Default::default()
            },
            Name::new("hud:slots"),
        ))
        .with_children(|row| {
            for (key, color) in SLOTS {
                row.spawn((
                    Node {
                        width: Val::Px(44.0),
                        height: Val::Px(44.0),
                        justify_content: JustifyContent::Center,
                        align_items: AlignItems::FlexEnd,
                        border_radius: BorderRadius::all(Val::Px(10.0)),
                        ..Default::default()
                    },
                    BackgroundColor(color),
                    Outline::new(Val::Px(2.0), Val::ZERO, Color::srgba(0.0, 0.0, 0.0, 0.45)),
                ))
                .with_children(|slot| {
                    slot.spawn(label(key, 15.0, Color::srgba(1.0, 1.0, 1.0, 0.95)));
                });
            }
        });
}

/// Pooled world-anchored NPC name tags: reassigned every frame by
/// [`hud_nametags_update`].
fn name_tag_pool(world: &mut World) {
    for _ in 0..NAME_TAG_POOL {
        world
            .spawn((
                Node {
                    position_type: PositionType::Absolute,
                    left: Val::Px(0.0),
                    top: Val::Px(0.0),
                    ..Default::default()
                },
                Visibility::Hidden,
                NameTag,
                Name::new("hud:nametag"),
            ))
            .with_children(|tag| {
                tag.spawn((
                    Node {
                        padding: UiRect::axes(Val::Px(10.0), Val::Px(5.0)),
                        border_radius: BorderRadius::all(Val::Px(11.0)),
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.55)),
                    label("", 13.0, Color::srgb(0.95, 0.95, 0.9)),
                ));
            });
    }
}

/// Static bar (boss/target): background + fill + label, rounded.
struct BarSpec {
    label_text: &'static str,
    value: u32,
    max: u32,
    fill: Color,
    text_size: f32,
    width: f32,
    left_px: f32,
    bottom_px: f32,
}

fn bar(world: &mut World, spec: BarSpec) -> Entity {
    let BarSpec {
        label_text,
        value,
        max,
        fill,
        text_size,
        width,
        left_px,
        bottom_px,
    } = spec;
    let fraction = if max > 0 {
        value as f32 / max as f32
    } else {
        0.0
    };
    let id = world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(left_px),
                bottom: Val::Px(bottom_px),
                width: Val::Px(width),
                height: Val::Px(text_size + 10.0),
                padding: UiRect::all(Val::Px(2.0)),
                border_radius: BorderRadius::all(Val::Px(8.0)),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.02, 0.02, 0.02, 0.8)),
            Name::new(format!("hud:bar:{label_text}")),
        ))
        .id();
    world.entity_mut(id).with_children(|bar_node| {
        bar_node.spawn((
            Node {
                width: Val::Percent(fraction * 100.0),
                height: Val::Percent(100.0),
                border_radius: BorderRadius::all(Val::Px(6.0)),
                ..Default::default()
            },
            BackgroundColor(fill),
        ));
        bar_node.spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(8.0),
                top: Val::Px(2.0),
                ..Default::default()
            },
            label(
                format!("{label_text} {value}/{max}"),
                text_size,
                Color::srgb(0.95, 0.92, 0.85),
            ),
        ));
    });
    id
}

/// Map a toggle key name (as authored) to a [`KeyCode`].
fn toggle_key(name: &str) -> KeyCode {
    match name.to_ascii_lowercase().as_str() {
        "q" => KeyCode::KeyQ,
        "e" => KeyCode::KeyE,
        "tab" => KeyCode::Tab,
        "m" => KeyCode::KeyM,
        "i" => KeyCode::KeyI,
        "space" => KeyCode::Space,
        _ => KeyCode::Tab,
    }
}

/// Toggle HUD elements with their authored key (e.g. TabbedModal on Q).
pub fn hud_toggle(
    keys: Res<ButtonInput<KeyCode>>,
    mut toggles: Query<(&mut Visibility, &HudToggle)>,
) {
    for (mut visibility, toggle) in &mut toggles {
        if keys.just_pressed(toggle.0) {
            *visibility = match *visibility {
                Visibility::Hidden => Visibility::Visible,
                _ => Visibility::Hidden,
            };
        }
    }
}

/// Show the interaction prompt when the player stands near a `<DialogueNPC>`
/// within the authored range.
pub fn hud_prompt_update(
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<&GlobalTransform, With<DialogueNpc>>,
    mut prompts: Query<(&mut Visibility, &HudPrompt)>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    for (mut visibility, prompt) in &mut prompts {
        let near = npcs
            .iter()
            .any(|npc| npc.translation().distance(player.translation()) < prompt.range);
        *visibility = if near {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
}

// ------------------------------------------------------- compass / minimap

/// Camera heading as a compass bearing in degrees (0° = north/−Z, 90° = east).
pub fn heading_bearing_deg(yaw_deg: f32) -> f32 {
    (-yaw_deg).rem_euclid(360.0)
}

/// Horizontal offset (px from the strip center) for a compass letter whose
/// bearing differs from the heading by `delta_deg`. `None` when out of the
/// visible span.
pub fn compass_offset_px(delta_deg: f32, half_width: f32, span_deg: f32) -> Option<f32> {
    if delta_deg.abs() >= span_deg {
        None
    } else {
        Some(delta_deg / span_deg * half_width)
    }
}

/// Minimap position (UI px offsets from the map center) for a world delta
/// `(dx, dz)` from the player, north-up: camera yaw rotates the plot so the
/// character's facing feels stable. Clamped to the map radius.
pub fn minimap_xy(dx: f32, dz: f32, yaw_deg: f32, range_m: f32, radius_px: f32) -> (f32, f32) {
    let yaw = yaw_deg.to_radians();
    let (sin, cos) = yaw.sin_cos();
    // Camera forward (fx, fz), right (rx, rz) — matches player::process_input.
    let (fx, fz) = (-sin, -cos);
    let (rx, rz) = (cos, -sin);
    let scale = radius_px / range_m.max(1.0);
    let x = (dx * rx + dz * rz) * scale;
    let y_up = (dx * fx + dz * fz) * scale;
    let len = (x * x + y_up * y_up).sqrt();
    let clamp = radius_px * 0.92;
    let (x, y_up) = if len > clamp {
        (x / len * clamp, y_up / len * clamp)
    } else {
        (x, y_up)
    };
    // UI y grows downward.
    (x, -y_up)
}

/// Minimap arrow rotation (radians, clockwise) for a camera yaw — the arrow
/// points where the camera faces, on a north-up map.
pub fn arrow_rotation_rad(yaw_deg: f32) -> f32 {
    -yaw_deg.to_radians()
}

/// Animate the compass strip: letters slide with the camera heading.
pub fn hud_compass_update(
    cameras: Query<&OrbitCamera>,
    mut letters: Query<(&mut Node, &CompassLetter)>,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let heading = heading_bearing_deg(cam.yaw_deg);
    for (mut node, letter) in &mut letters {
        let delta = crate::camera::shortest_angle_delta_deg(heading, letter.bearing_deg);
        node.left = match compass_offset_px(delta, 230.0, 55.0) {
            Some(offset) => Val::Px(230.0 + offset),
            None => Val::Px(-100.0), // park off-strip
        };
    }
}

/// Minimap-node query (type alias for clippy's type-complexity limit).
/// Quest-dot query tuple (kept as a type alias for clippy's type-complexity).
/// Animate the minimap: arrow rotation + quest dots at nearby NPC spots.
#[allow(clippy::type_complexity)]
pub fn hud_minimap_update(
    cameras: Query<&OrbitCamera>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<&GlobalTransform, With<DialogueNpc>>,
    maps: Query<(&Node, &MinimapRange), (Without<MinimapArrow>, Without<MinimapDot>)>,
    mut arrow: Query<&mut UiTransform, (With<MinimapArrow>, Without<MinimapDot>)>,
    mut dots: Query<(&mut Node, &mut UiTransform, &mut Visibility), With<MinimapDot>>,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let Some(mapper) = players.iter().next() else {
        return;
    };
    let player_pos = mapper.translation();
    let mut arrow_transform = match arrow.single_mut() {
        Ok(transform) => Some(transform),
        Err(error) => {
            bevy::log::warn!("minimap arrow query failed: {error}");
            None
        }
    };
    if let Some(transform) = arrow_transform.as_mut() {
        // Keep the −half-shape centering translation; only the rotation is
        // driven per frame (from_rotation alone would zero it out).
        **transform = UiTransform {
            translation: Val2::new(Val::Px(-5.0), Val::Px(-9.0)),
            rotation: Rot2::radians(arrow_rotation_rad(cam.yaw_deg)),
            ..Default::default()
        };
    }
    let Ok((map_node, range)) = maps.single() else {
        return;
    };
    // Node height is authored px (148) — derive the inner radius from it.
    let radius = match map_node.height {
        Val::Px(h) => h / 2.0 - 6.0,
        _ => 68.0,
    };
    let range_m = range.0;
    let mut npcs: Vec<_> = npcs
        .iter()
        .map(|t| t.translation())
        .map(|p| (p.x - player_pos.x, p.z - player_pos.z))
        .collect();
    npcs.sort_by(|a, b| {
        (a.0 * a.0 + a.1 * a.1)
            .partial_cmp(&(b.0 * b.0 + b.1 * b.1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    for (index, (mut node, mut transform, mut visibility)) in dots.iter_mut().enumerate() {
        if let Some(&(dx, dz)) = npcs.get(index % npcs.len().max(1)) {
            let (x, y) = minimap_xy(dx, dz, cam.yaw_deg, range_m, radius);
            node.left = Val::Percent(50.0);
            node.top = Val::Percent(50.0);
            *transform = UiTransform::from_translation(Val2::new(Val::Px(x), Val::Px(y)));
            *visibility = Visibility::Visible;
        } else {
            *visibility = Visibility::Hidden;
        }
    }
}

// ------------------------------------------------------------- name tags

/// Screen position (UI px, top-left origin) for a world point, if the camera
/// sees it (in front and within the viewport). `world_to_viewport` already
/// returns top-left-origin coordinates (bevy flips Y internally), so the
/// result plugs straight into UI `left`/`top`.
fn world_to_ui(
    camera: &Camera,
    camera_global: &GlobalTransform,
    world_pos: Vec3,
) -> Option<(f32, f32)> {
    // In front of the camera?
    let to_point = world_pos - camera_global.translation();
    if to_point.dot(camera_global.forward().as_vec3()) <= 0.0 {
        return None;
    }
    camera
        .world_to_viewport(camera_global, world_pos)
        .ok()
        .map(|vp| (vp.x, vp.y))
}

/// Reassign the pooled name-tag pills to the nearest NPCs: "<name> · <d> m".
pub fn hud_nametags_update(
    cameras: Query<(&Camera, &GlobalTransform), Without<Player>>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc, Option<&Name>)>,
    mut tags: Query<(&mut Node, &mut Visibility, &Children), With<NameTag>>,
    mut texts: Query<&mut Text>,
) {
    let Ok((camera, camera_global)) = cameras.single() else {
        return;
    };
    let Ok(player) = players.single() else {
        return;
    };
    let player_pos = player.translation();
    let mut candidates: Vec<(f32, Vec3, String)> = npcs
        .iter()
        .filter_map(|(global, npc, name)| {
            let pos = global.translation();
            let dist = pos.distance(player_pos);
            (NAME_TAG_MIN_M..NAME_TAG_MAX_M).contains(&dist).then(|| {
                let label = name
                    .map(|n| n.to_string())
                    .unwrap_or_else(|| npc.dialogue_id.clone());
                (dist, pos, label)
            })
        })
        .collect();
    candidates.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));

    for (index, (mut node, mut visibility, children)) in tags.iter_mut().enumerate() {
        match candidates.get(index) {
            Some(&(dist, pos, ref label_text)) => {
                // Anchor ~2 m above the NPC's feet (head height).
                if let Some((x, y)) = world_to_ui(camera, camera_global, pos + Vec3::Y * 2.1) {
                    node.left = Val::Px(x);
                    node.top = Val::Px(y);
                    *visibility = Visibility::Visible;
                    if let Some(child) = children.first() {
                        if let Ok(mut text) = texts.get_mut(*child) {
                            let next = format!("{label_text} {} m", dist.round() as i32);
                            if text.0 != next {
                                text.0 = next;
                            }
                        }
                    }
                } else {
                    *visibility = Visibility::Hidden;
                }
            }
            None => *visibility = Visibility::Hidden,
        }
    }
}

// ---------------------------------------------------------- dynamic vitals

/// Label text for the healthbar ("{cur}/{max}", hp rounded for display).
pub fn health_label_text(current: f32, max: f32) -> String {
    format!("{}/{}", current.round() as i32, max.round() as i32)
}

/// Label text for the xpbar ("{cur}/{next}").
pub fn xp_label_text(current: u32, next: u32) -> String {
    format!("{current}/{next}")
}

/// Mirror the hero's [`Health`] into the `healthbar` fill width and label.
/// Without a `Health` on the player it shows the default 100/100.
///
/// WIRED-BY-ORCHESTRATOR: register in `src/main.rs` `Update` (module docs).
pub fn hud_health_sync(
    players: Query<&Health, With<Player>>,
    mut fills: Query<&mut Node, With<HudHealthFill>>,
    mut labels: Query<&mut Text, With<HudHealthLabel>>,
) {
    let (current, max) = players
        .iter()
        .next()
        .map(|h| (h.current, h.max))
        .unwrap_or((100.0, 100.0));
    let percent = health_fraction(current, max) * 100.0;
    for mut node in &mut fills {
        node.width = Val::Percent(percent);
    }
    let text = health_label_text(current, max);
    for mut label in &mut labels {
        if label.0 != text {
            label.0 = text.clone();
        }
    }
}

/// Mirror the hero's [`Xp`] into the `xpbar` fill width and label.
/// Without an `Xp` on the player it shows the default 0/100.
///
/// WIRED-BY-ORCHESTRATOR: register in `src/main.rs` `Update` (module docs).
pub fn hud_xp_sync(
    players: Query<&Xp, With<Player>>,
    mut fills: Query<&mut Node, With<HudXpFill>>,
    mut labels: Query<&mut Text, With<HudXpLabel>>,
) {
    let (current, next) = players
        .iter()
        .next()
        .map(|x| (x.current, x.next))
        .unwrap_or((0, 100));
    let percent = xp_fraction(current, next) * 100.0;
    for mut node in &mut fills {
        node.width = Val::Percent(percent);
    }
    let text = xp_label_text(current, next);
    for mut label in &mut labels {
        if label.0 != text {
            label.0 = text.clone();
        }
    }
}

/// Countdown for a visible balloon: advances `timer` by `dt` and returns
/// whether it should stay visible (clamping the timer at 0). Invisible
/// balloons just stay hidden without burning the timer.
pub fn balloon_tick(timer: &mut f32, visible: bool, dt: f32) -> bool {
    if !visible {
        return false;
    }
    *timer -= dt;
    if *timer <= 0.0 {
        *timer = 0.0;
        false
    } else {
        true
    }
}

/// Dialogue balloon: pressing `E` with a `<DialogueNPC>` within
/// [`BALLOON_RANGE_M`] shows it with that NPC's `dialogue-id` for
/// [`BALLOON_DURATION`] seconds, then hides it again.
///
/// The balloon's TEXT lives one level below the tracked root (the root only
/// carries visibility + the timer), hence the `Children` hop.
///
/// WIRED-BY-ORCHESTRATOR: register in `src/main.rs` `Update` (module docs).
pub fn hud_balloon_update(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc)>,
    mut balloons: Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    mut texts: Query<&mut Text>,
) {
    let dialogue: Option<String> = if keys.just_pressed(KeyCode::KeyE) {
        players.iter().next().and_then(|player| {
            npcs.iter()
                .find(|(t, _)| t.translation().distance(player.translation()) < BALLOON_RANGE_M)
                .map(|(_, npc)| npc.dialogue_id.clone())
        })
    } else {
        None
    };

    let dt = time.delta_secs();
    for (mut visibility, mut balloon, children) in &mut balloons {
        if let Some(id) = &dialogue {
            balloon.timer = BALLOON_DURATION;
            *visibility = Visibility::Visible;
            if let Some(child) = children.first() {
                if let Ok(mut text) = texts.get_mut(*child) {
                    text.0 = id.clone();
                }
            }
        }
        let visible = *visibility == Visibility::Visible;
        if !balloon_tick(&mut balloon.timer, visible, dt) {
            *visibility = Visibility::Hidden;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_health_label_text() {
        assert_eq!(health_label_text(100.0, 100.0), "100/100");
        assert_eq!(health_label_text(87.5, 100.0), "88/100"); // rounds
        assert_eq!(health_label_text(0.0, 100.0), "0/100");
    }

    #[test]
    fn test_xp_label_text() {
        assert_eq!(xp_label_text(0, 100), "0/100");
        assert_eq!(xp_label_text(30, 150), "30/150");
    }

    #[test]
    fn test_balloon_tick_hides_after_duration() {
        let mut timer = BALLOON_DURATION;
        let mut visible = true;
        let mut steps = 0;
        while visible && steps < 100 {
            visible = balloon_tick(&mut timer, visible, 0.1);
            steps += 1;
        }
        assert!(!visible, "hides at the end of the duration");
        // ~40 steps (4 s at 0.1 s); exact count drifts ±1 with f32 0.1 accumulation
        assert!(
            (38..=42).contains(&steps),
            "hides after ~4 s, took {steps} steps"
        );
        assert!(approx(timer, 0.0), "timer clamps at 0: {timer}");
        // and it stays hidden afterwards
        assert!(!balloon_tick(&mut timer, true, 0.5));
    }

    #[test]
    fn test_balloon_tick_stays_hidden_without_burning_timer() {
        let mut timer = BALLOON_DURATION;
        assert!(!balloon_tick(&mut timer, false, 0.016));
        assert!(approx(timer, BALLOON_DURATION), "hidden → timer untouched");
    }

    #[test]
    fn test_balloon_retrigger_resets_timer() {
        let mut timer = BALLOON_DURATION;
        for _ in 0..30 {
            assert!(balloon_tick(&mut timer, true, 0.1));
        }
        assert!(approx(timer, 1.0), "3 s elapsed: {timer}");
        // HUD sets the timer back to the full duration on a new interaction…
        timer = BALLOON_DURATION;
        // …and it keeps the balloon visible for another full cycle.
        for _ in 0..39 {
            assert!(balloon_tick(&mut timer, true, 0.1));
        }
        assert!(balloon_tick(&mut timer, true, 0.1));
        assert!(!balloon_tick(&mut timer, true, 0.1));
    }

    #[test]
    fn test_heading_bearing_matches_compass() {
        // yaw 0 → camera looks −Z (north) → bearing 0; yaw 90 swings the
        // camera to the +X side so it faces −X (west) → bearing 270.
        assert!(approx(heading_bearing_deg(0.0), 0.0));
        assert!(approx(heading_bearing_deg(90.0), 270.0));
        assert!(approx(heading_bearing_deg(-90.0), 90.0));
        // yaw wraps: −450° ≡ −90° → facing east.
        assert!(approx(heading_bearing_deg(-450.0), 90.0));
    }

    #[test]
    fn test_compass_offset_visibility_window() {
        // Centered letter sits at zero offset; ±span hides.
        assert_eq!(compass_offset_px(0.0, 230.0, 55.0), Some(0.0));
        let half = compass_offset_px(27.5, 230.0, 55.0).unwrap();
        assert!(approx(half, 115.0));
        assert_eq!(compass_offset_px(55.0, 230.0, 55.0), None);
        assert_eq!(compass_offset_px(-80.0, 230.0, 55.0), None);
    }

    #[test]
    fn test_minimap_xy_north_up() {
        let radius = 68.0;
        // NPC directly north (−Z) of the player, camera facing north:
        // dot sits straight up on the map (UI y negative).
        let (x, y) = minimap_xy(0.0, -30.0, 0.0, 60.0, radius);
        assert!(approx(x, 0.0) && y < 0.0 && approx(y, -34.0), "({x},{y})");
        // NPC to the east: dot to the right regardless of camera yaw.
        let (x, _) = minimap_xy(30.0, 0.0, 0.0, 60.0, radius);
        assert!(x > 0.0);
        // Camera swung 180°: the same north NPC now plots below center.
        let (_, y) = minimap_xy(0.0, -30.0, 180.0, 60.0, radius);
        assert!(y > 0.0);
        // Beyond range clamps inside the circle.
        let (x, y) = minimap_xy(0.0, -500.0, 0.0, 60.0, radius);
        assert!((x * x + y * y).sqrt() <= radius * 0.93);
    }

    #[test]
    fn test_arrow_rotation_points_facing() {
        // Facing north → arrow up; facing east (yaw −90) → rotated +90°.
        assert!(approx(arrow_rotation_rad(0.0), 0.0));
        assert!(approx(
            arrow_rotation_rad(-90.0),
            std::f32::consts::FRAC_PI_2
        ));
    }
}
