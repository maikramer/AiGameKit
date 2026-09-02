//! HUD element builders: everything spawned from world tags
//! (`HealthBar`, `XpBar`, `Minimap`, `Compass`, `ResourceChip`, …) plus the
//! DOM-parity widgets (help bar, action slots, name-tag pool).

use bevy::prelude::*;
use bevy::ui::widget::ImageNode;

use super::assets::{
    HudAssets, centered_at, gradient_overlay, label, panel_base, panel_edge, panel_shadow,
};
use super::compass::{CompassDistance, CompassLetter, CompassTick};
use super::interact::{BALLOON_DURATION, HudBalloon, HudPrompt, HudToggle};
use super::minimap::{MinimapArrow, MinimapDot, MinimapRange};
use super::nametags::NameTag;
use super::vitals::xp_label_text;
use super::vitals::{HudHealthFill, HudHealthLabel, HudXpFill, HudXpLabel};

fn attr<'a>(attrs: &'a [(String, String)], name: &str) -> Option<&'a str> {
    attrs
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

/// Build every deferred HUD element. `tag` is the lowercased original tag.
pub fn spawn_hud(world: &mut World, tag: &str, attrs: &[(String, String)]) {
    let hud = HudAssets::get(world);
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
            help_bar(world, &hud);
            action_slots(world, &hud);
            name_tag_pool(world, &hud);
        }
        "healthbar" => {
            // Rounded gradient panel: glossy heart icon + green bar with
            // "100/100" inside.
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
                        border: UiRect::all(Val::Px(1.5)),
                        border_radius: BorderRadius::all(Val::Px(16.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_edge(),
                    panel_shadow(),
                    Name::new("hud:health"),
                ))
                .with_children(|p| {
                    p.spawn(gradient_overlay(&hud, 16.0));
                })
                .with_children(|panel| {
                    // Glossy heart icon: back diamond, front diamond, lobes,
                    // glint.
                    panel
                        .spawn(Node {
                            width: Val::Px(30.0),
                            height: Val::Px(26.0),
                            ..Default::default()
                        })
                        .with_children(|heart| {
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(8.0),
                                    top: Val::Px(7.0),
                                    width: Val::Px(14.0),
                                    height: Val::Px(14.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgb(0.52, 0.05, 0.08)),
                            ));
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(8.0),
                                    top: Val::Px(6.0),
                                    width: Val::Px(14.0),
                                    height: Val::Px(14.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgb(0.80, 0.10, 0.13)),
                            ));
                            for x in [2.0_f32, 14.0] {
                                heart.spawn((
                                    Node {
                                        position_type: PositionType::Absolute,
                                        left: Val::Px(x),
                                        top: Val::Px(1.0),
                                        width: Val::Px(14.0),
                                        height: Val::Px(14.0),
                                        border_radius: BorderRadius::all(Val::Px(7.0)),
                                        ..Default::default()
                                    },
                                    BackgroundColor(Color::srgb(0.80, 0.10, 0.13)),
                                ));
                            }
                            heart.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(6.0),
                                    top: Val::Px(4.0),
                                    width: Val::Px(6.0),
                                    height: Val::Px(6.0),
                                    border_radius: BorderRadius::all(Val::Px(3.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(1.0, 0.6, 0.6, 0.9)),
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
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.6)),
                            BorderColor::all(Color::srgba(0.0, 0.0, 0.0, 0.7)),
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
                                    BackgroundColor(Color::srgb(0.30, 0.74, 0.22)),
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
                                        label(&hud, "100/100", 14.0, Color::srgb(0.97, 1.0, 0.96)),
                                        HudHealthLabel,
                                    ));
                                });
                        });
                });
        }
        "xpbar" => {
            // Level badge (gold coin) + slim dark bar with a gold fill.
            world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        top: Val::Px(66.0),
                        left: Val::Px(12.0),
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(10.0),
                        padding: UiRect::axes(Val::Px(8.0), Val::Px(5.0)),
                        border_radius: BorderRadius::all(Val::Px(13.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_shadow(),
                    Name::new("hud:xp"),
                ))
                .with_children(|p| {
                    p.spawn(gradient_overlay(&hud, 13.0));
                })
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
                            BackgroundColor(Color::srgb(0.86, 0.65, 0.12)),
                            BorderColor::all(Color::srgb(0.42, 0.29, 0.04)),
                        ))
                        .with_children(|coin| {
                            coin.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(3.0),
                                    top: Val::Px(3.0),
                                    width: Val::Px(20.0),
                                    height: Val::Px(20.0),
                                    border_radius: BorderRadius::all(Val::Px(10.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(1.0, 0.86, 0.35, 0.55)),
                            ));
                            coin.spawn(label(&hud, "1", 14.0, Color::srgb(0.32, 0.21, 0.02)));
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
                            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.6)),
                            BorderColor::all(Color::srgba(0.0, 0.0, 0.0, 0.7)),
                        ))
                        .with_children(|track| {
                            track.spawn((
                                Node {
                                    width: Val::Percent(0.0),
                                    height: Val::Percent(100.0),
                                    border_radius: BorderRadius::all(Val::Px(4.0)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgb(0.94, 0.71, 0.15)),
                                HudXpFill,
                            ));
                        });
                    // Dim numeric readout parked right of the bar.
                    panel.spawn((
                        label(
                            &hud,
                            xp_label_text(0, 100),
                            10.0,
                            Color::srgba(1.0, 0.95, 0.8, 0.65),
                        ),
                        HudXpLabel,
                    ));
                });
        }
        "bossbar" => {
            let id = bar(
                world,
                &hud,
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
                &hud,
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
                    BackgroundColor(Color::srgba(0.045, 0.07, 0.09, 0.9)),
                    BorderColor::all(Color::srgb(0.82, 0.74, 0.45)),
                    Name::new("hud:minimap"),
                    MinimapRange(range),
                    panel_shadow(),
                ))
                .with_children(|map| {
                    // North indicator.
                    map.spawn((
                        centered_at(Val::Percent(50.0), Val::Px(7.0)),
                        UiTransform::from_translation(Val2::new(Val::Percent(-50.0), Val::ZERO)),
                        label(&hud, "N", 15.0, Color::srgb(0.95, 0.78, 0.25)),
                    ));
                    // Player arrow: a real textured triangle rotated per frame.
                    map.spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            left: Val::Percent(50.0),
                            top: Val::Percent(50.0),
                            margin: UiRect::px(-10.0, 0.0, -10.0, 0.0),
                            width: Val::Px(20.0),
                            height: Val::Px(20.0),
                            ..Default::default()
                        },
                        ImageNode {
                            image: hud.arrow.clone(),
                            ..Default::default()
                        },
                        UiTransform::IDENTITY,
                        MinimapArrow,
                    ));
                    // Reusable numbered quest dots (positioned per frame).
                    for number in 1..=6 {
                        map.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Percent(50.0),
                                top: Val::Percent(50.0),
                                margin: UiRect::px(-5.5, 0.0, -5.5, 0.0),
                                width: Val::Px(11.0),
                                height: Val::Px(11.0),
                                justify_content: JustifyContent::Center,
                                align_items: AlignItems::Center,
                                border_radius: BorderRadius::all(Val::Px(5.5)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.86, 0.65, 0.12)),
                            Outline::new(
                                Val::Px(1.0),
                                Val::ZERO,
                                Color::srgba(0.0, 0.0, 0.0, 0.55),
                            ),
                            Visibility::Hidden,
                            MinimapDot,
                        ))
                        .with_children(|dot| {
                            dot.spawn(label(
                                &hud,
                                number.to_string(),
                                8.0,
                                Color::srgb(0.3, 0.2, 0.02),
                            ));
                        });
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
                            height: Val::Px(34.0),
                            border_radius: BorderRadius::all(Val::Px(17.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.03, 0.03, 0.025, 0.62)),
                        ImageNode {
                            image: hud.panel_gradient.clone(),
                            color: Color::srgba(0.10, 0.095, 0.085, 0.55),
                            ..Default::default()
                        },
                        BorderColor::all(Color::srgba(1.0, 0.96, 0.85, 0.12)),
                        panel_shadow(),
                    ))
                    .with_children(|strip| {
                        // Center caret (static): the heading marker.
                        strip.spawn((
                            centered_at(Val::Percent(50.0), Val::Px(2.0)),
                            Node {
                                width: Val::Px(2.0),
                                height: Val::Px(8.0),
                                border_radius: BorderRadius::all(Val::Px(1.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.95, 0.78, 0.25)),
                        ));
                        for (name, bearing) in DIRECTIONS {
                            let color = if name == "N" {
                                Color::srgb(0.95, 0.78, 0.25)
                            } else {
                                Color::srgb(0.92, 0.92, 0.88)
                            };
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(4.0)),
                                UiTransform::from_translation(Val2::new(
                                    Val::Percent(-50.0),
                                    Val::ZERO,
                                )),
                                label(&hud, name, 13.0, color),
                                CompassLetter {
                                    bearing_deg: bearing,
                                },
                            ));
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(19.0)),
                                UiTransform::from_translation(Val2::new(
                                    Val::Percent(-50.0),
                                    Val::ZERO,
                                )),
                                label(&hud, "", 9.0, Color::srgba(1.0, 0.92, 0.7, 0.85)),
                                CompassDistance {
                                    bearing_deg: bearing,
                                },
                            ));
                        }
                        // Tick marks every 22.5°.
                        for i in 0..16 {
                            let bearing = i as f32 * 22.5;
                            let tall = i % 2 == 0;
                            strip.spawn((
                                centered_at(Val::Px(230.0), Val::Px(0.0)),
                                Node {
                                    width: Val::Px(1.5),
                                    height: Val::Px(if tall { 5.0 } else { 3.0 }),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(
                                    1.0,
                                    1.0,
                                    0.95,
                                    if tall { 0.4 } else { 0.22 },
                                )),
                                CompassTick {
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
                        panel_base(),
                        panel_shadow(),
                        Text::new(format!("[{key}] Interagir")),
                        TextColor(Color::srgb(1.0, 0.95, 0.8)),
                        TextFont {
                            font: hud.font.clone().into(),
                            font_size: 16.0.into(),
                            ..Default::default()
                        },
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
                        BackgroundColor(Color::srgba(0.97, 0.94, 0.86, 0.95)),
                        BorderColor::all(Color::srgba(0.4, 0.3, 0.15, 0.5)),
                        Text::new("…"),
                        TextColor(Color::srgb(0.15, 0.12, 0.08)),
                        TextFont {
                            font: hud.font.clone().into(),
                            font_size: 15.0.into(),
                            ..Default::default()
                        },
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
                        panel_base(),
                        BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
                        panel_shadow(),
                        label(&hud, title, 22.0, Color::srgb(0.95, 0.85, 0.6)),
                    ));
                });
        }
        other => {
            bevy::log::warn!("hud: unhandled element `{other}` — skipped");
        }
    }
}

/// Resource chip slot styled like the original: rounded dark slot with an
/// authored mini icon (coin / log / stone) and a count. `index` is 1-based.
pub fn spawn_resource_chip(world: &mut World, index: usize, resource: &str) {
    let hud = HudAssets::get(world);
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(110.0),
                left: Val::Px(12.0 + 64.0 * (index - 1) as f32),
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: Val::Px(7.0),
                padding: UiRect::axes(Val::Px(9.0), Val::Px(6.0)),
                border_radius: BorderRadius::all(Val::Px(12.0)),
                ..Default::default()
            },
            panel_base(),
            panel_edge(),
            panel_shadow(),
            Name::new(format!("chip:{resource}")),
        ))
        .with_children(|slot| {
            match resource {
                // Gold coin: dark ring + gold disc + glint.
                "gold" => {
                    slot.spawn((
                        Node {
                            width: Val::Px(15.0),
                            height: Val::Px(15.0),
                            border_radius: BorderRadius::all(Val::Px(7.5)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.42, 0.29, 0.04)),
                    ))
                    .with_children(|coin| {
                        coin.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(2.0),
                                top: Val::Px(2.0),
                                width: Val::Px(11.0),
                                height: Val::Px(11.0),
                                border_radius: BorderRadius::all(Val::Px(5.5)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.95, 0.73, 0.16)),
                        ));
                        coin.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(3.0),
                                top: Val::Px(3.0),
                                width: Val::Px(4.0),
                                height: Val::Px(4.0),
                                border_radius: BorderRadius::all(Val::Px(2.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgba(1.0, 0.95, 0.75, 0.9)),
                        ));
                    });
                }
                // Wood log: rounded plank + end-grain disc + core dot.
                "wood" => {
                    slot.spawn((
                        Node {
                            width: Val::Px(17.0),
                            height: Val::Px(11.0),
                            border_radius: BorderRadius::px(5.5, 1.5, 1.5, 5.5),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.45, 0.28, 0.12)),
                    ))
                    .with_children(|log| {
                        log.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(0.0),
                                top: Val::Px(0.0),
                                width: Val::Px(9.0),
                                height: Val::Px(11.0),
                                border_radius: BorderRadius::all(Val::Px(5.5)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.66, 0.45, 0.22)),
                        ));
                        log.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(2.5),
                                top: Val::Px(2.5),
                                width: Val::Px(4.0),
                                height: Val::Px(6.0),
                                border_radius: BorderRadius::all(Val::Px(2.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.45, 0.28, 0.12)),
                        ));
                    });
                }
                // Stone: pebble disc + lighter top face.
                _ => {
                    slot.spawn((
                        Node {
                            width: Val::Px(15.0),
                            height: Val::Px(12.0),
                            border_radius: BorderRadius::all(Val::Px(6.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgb(0.38, 0.40, 0.44)),
                    ))
                    .with_children(|stone| {
                        stone.spawn((
                            Node {
                                position_type: PositionType::Absolute,
                                left: Val::Px(1.5),
                                top: Val::Px(0.5),
                                width: Val::Px(12.0),
                                height: Val::Px(8.0),
                                border_radius: BorderRadius::all(Val::Px(5.0)),
                                ..Default::default()
                            },
                            BackgroundColor(Color::srgb(0.63, 0.66, 0.71)),
                        ));
                    });
                }
            }
            slot.spawn(label(&hud, "0", 15.0, Color::srgb(0.96, 0.94, 0.86)));
        });
}

/// Bottom-center help pill with key-hint styling: keys in gold, actions in
/// warm light, dot separators.
fn help_bar(world: &mut World, hud: &HudAssets) {
    const SEGMENTS: [(&str, bool); 9] = [
        ("WASD", true),
        (" mover", false),
        ("ESPAÇO", true),
        (" pular", false),
        ("SHIFT", true),
        (" correr", false),
        ("E", true),
        (" interagir", false),
        ("Q menu", true),
    ];
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
                    padding: UiRect::axes(Val::Px(16.0), Val::Px(7.0)),
                    border_radius: BorderRadius::all(Val::Px(14.0)),
                    ..Default::default()
                },
                panel_base(),
                panel_shadow(),
                Text::new(""),
            ))
            .with_children(|pill| {
                for (i, (text, is_key)) in SEGMENTS.iter().enumerate() {
                    let color = if *is_key {
                        Color::srgb(0.95, 0.78, 0.28)
                    } else {
                        Color::srgb(0.9, 0.88, 0.8)
                    };
                    pill.spawn((
                        bevy::text::TextSpan::new((*text).to_string()),
                        TextColor(color),
                        TextFont {
                            font: hud.font.clone().into(),
                            font_size: 13.0.into(),
                            ..Default::default()
                        },
                    ));
                    if i + 1 < SEGMENTS.len() {
                        pill.spawn((
                            bevy::text::TextSpan::new("  ·  "),
                            TextColor(Color::srgba(0.8, 0.78, 0.7, 0.5)),
                            TextFont {
                                font: hud.font.clone().into(),
                                font_size: 13.0.into(),
                                ..Default::default()
                            },
                        ));
                    }
                }
            });
        });
}

/// Bottom-left action slots (C/E/R): dark slots with colored glyphs and
/// keycap letters, styled after the original buttons.
fn action_slots(world: &mut World, hud: &HudAssets) {
    const SLOTS: [(&str, Color); 3] = [
        ("C", Color::srgb(0.16, 0.52, 0.92)),
        ("E", Color::srgb(0.16, 0.66, 0.28)),
        ("R", Color::srgb(0.92, 0.45, 0.1)),
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
                        width: Val::Px(46.0),
                        height: Val::Px(46.0),
                        justify_content: JustifyContent::Center,
                        align_items: AlignItems::Center,
                        border_radius: BorderRadius::all(Val::Px(11.0)),
                        ..Default::default()
                    },
                    panel_base(),
                    panel_shadow(),
                    BorderColor::all(Color::srgba(1.0, 0.96, 0.85, 0.12)),
                ))
                .with_children(|slot| {
                    // Colored icon square with a dark inner glyph.
                    slot.spawn((
                        Node {
                            width: Val::Px(26.0),
                            height: Val::Px(26.0),
                            justify_content: JustifyContent::Center,
                            align_items: AlignItems::Center,
                            border_radius: BorderRadius::all(Val::Px(7.0)),
                            ..Default::default()
                        },
                        BackgroundColor(color),
                        Outline::new(Val::Px(1.5), Val::ZERO, Color::srgba(0.0, 0.0, 0.0, 0.45)),
                    ))
                    .with_children(|icon| match key {
                        // C: compass ring.
                        "C" => {
                            icon.spawn((
                                Node {
                                    width: Val::Px(14.0),
                                    height: Val::Px(14.0),
                                    border_radius: BorderRadius::all(Val::Px(7.0)),
                                    border: UiRect::all(Val::Px(3.0)),
                                    ..Default::default()
                                },
                                BorderColor::all(Color::srgba(0.02, 0.1, 0.22, 0.9)),
                            ));
                        }
                        // E: sword blade + guard.
                        "E" => {
                            icon.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(11.0),
                                    top: Val::Px(3.0),
                                    width: Val::Px(4.0),
                                    height: Val::Px(16.0),
                                    border_radius: BorderRadius::px(2.0, 2.0, 0.0, 0.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgba(0.03, 0.14, 0.05, 0.92)),
                            ));
                            icon.spawn((
                                Node {
                                    position_type: PositionType::Absolute,
                                    left: Val::Px(6.0),
                                    top: Val::Px(15.0),
                                    width: Val::Px(14.0),
                                    height: Val::Px(3.0),
                                    border_radius: BorderRadius::all(Val::Px(1.5)),
                                    ..Default::default()
                                },
                                BackgroundColor(Color::srgba(0.03, 0.14, 0.05, 0.92)),
                            ));
                        }
                        // R: burst diamond.
                        _ => {
                            icon.spawn((
                                Node {
                                    width: Val::Px(13.0),
                                    height: Val::Px(13.0),
                                    ..Default::default()
                                },
                                UiTransform::from_rotation(Rot2::radians(
                                    std::f32::consts::FRAC_PI_4,
                                )),
                                BackgroundColor(Color::srgba(0.2, 0.08, 0.01, 0.92)),
                            ));
                        }
                    });
                    // Keycap letter, bottom-right.
                    slot.spawn((
                        Node {
                            position_type: PositionType::Absolute,
                            right: Val::Px(2.0),
                            bottom: Val::Px(1.0),
                            padding: UiRect::axes(Val::Px(4.0), Val::Px(0.0)),
                            border_radius: BorderRadius::all(Val::Px(5.0)),
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.04, 0.04, 0.04, 0.75)),
                        label(hud, key, 10.0, Color::srgba(1.0, 1.0, 1.0, 0.95)),
                    ));
                });
            }
        });
}

/// Pooled world-anchored NPC name tags: reassigned every frame by the
/// nametags module.
fn name_tag_pool(world: &mut World, hud: &HudAssets) {
    for _ in 0..super::nametags::NAME_TAG_POOL {
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
                    BackgroundColor(Color::srgba(0.02, 0.02, 0.02, 0.78)),
                    BorderColor::all(Color::srgba(1.0, 0.96, 0.85, 0.14)),
                    label(hud, "", 13.0, Color::srgb(0.96, 0.96, 0.92)),
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

fn bar(world: &mut World, hud: &HudAssets, spec: BarSpec) -> Entity {
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
            panel_base(),
            panel_shadow(),
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
                hud,
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
