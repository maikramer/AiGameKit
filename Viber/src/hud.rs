//! HUD screen elements: bars, minimap, compass, prompt, modal — built with
//! `bevy_ui` on top of the raw attributes kept in the IR ([`HudElement`]).
//!
//! Values are static placeholders (health 100/100, XP 0) until the combat
//! and economy systems land; layout and toggle behaviour are real.

use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::DialogueNpc;

/// Marker for the interaction prompt node (shown near a `<DialogueNPC>`).
#[derive(Component)]
pub struct HudPrompt;

/// Marker for nodes toggled by a key (e.g. `<TabbedModal key="q">`).
#[derive(Component)]
pub struct HudToggle(pub KeyCode);

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

/// Build every deferred HUD element. `tag` is the lowercased original tag.
pub fn spawn_hud(world: &mut World, tag: &str, attrs: &[(String, String)]) {
    match tag {
        "hudscreenlayer" => {
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
        }
        "healthbar" => {
            bar(
                world,
                "HP",
                100,
                100,
                Color::srgb(0.75, 0.15, 0.15),
                (18.0, 900.0),
            );
        }
        "xpbar" => {
            bar(
                world,
                "XP",
                0,
                100,
                Color::srgb(0.2, 0.45, 0.8),
                (10.0, 900.0),
            );
        }
        "bossbar" => {
            let id = bar(
                world,
                "BOSS",
                100,
                100,
                Color::srgb(0.45, 0.1, 0.55),
                (22.0, 420.0),
            );
            world.entity_mut(id).insert(Visibility::Hidden);
        }
        "targetbar" => {
            let id = bar(
                world,
                "sem alvo",
                0,
                100,
                Color::srgb(0.6, 0.2, 0.2),
                (14.0, 240.0),
            );
            world.entity_mut(id).insert(Visibility::Hidden);
        }
        "minimap" => {
            let range = attr(attrs, "range").and_then(|v| v.parse::<f32>().ok());
            let mut node = world.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    top: Val::Px(10.0),
                    right: Val::Px(10.0),
                    width: Val::Px(140.0),
                    height: Val::Px(140.0),
                    border: UiRect::all(Val::Px(2.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.05, 0.08, 0.12, 0.72)),
                BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
                Name::new("hud:minimap"),
            ));
            node.with_children(|map| {
                map.spawn((
                    Node {
                        margin: UiRect::all(Val::Auto),
                        ..Default::default()
                    },
                    label(
                        format!("MAP {}m", range.map(|r| r.to_string()).unwrap_or_default()),
                        14.0,
                        Color::srgb(0.85, 0.85, 0.75),
                    ),
                ));
            });
        }
        "compass" => {
            world.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    top: Val::Px(8.0),
                    left: Val::Px(0.0),
                    right: Val::Px(0.0),
                    justify_content: JustifyContent::Center,
                    ..Default::default()
                },
                label("N ↑", 22.0, Color::srgb(0.95, 0.9, 0.75)),
                Name::new("hud:compass"),
            ));
        }
        "interactionprompt" => {
            let key = attr(attrs, "key").unwrap_or("E").to_string();
            let id = world
                .spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        bottom: Val::Px(120.0),
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        justify_content: JustifyContent::Center,
                        ..Default::default()
                    },
                    Text::new(format!("[{key}] Interagir")),
                    TextColor(Color::srgb(1.0, 0.95, 0.8)),
                    TextFont::from_font_size(20.0),
                    Visibility::Hidden,
                    HudPrompt,
                    Name::new("hud:prompt"),
                ))
                .id();
            world.entity_mut(id);
        }
        "dialogueballoon" => {
            world.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    bottom: Val::Px(170.0),
                    left: Val::Px(0.0),
                    right: Val::Px(0.0),
                    justify_content: JustifyContent::Center,
                    ..Default::default()
                },
                Text::new("…"),
                TextColor(Color::srgb(0.95, 0.95, 0.9)),
                TextFont::from_font_size(18.0),
                Visibility::Hidden,
                Name::new("hud:balloon"),
            ));
        }
        "tabbedmodal" => {
            let key = attr(attrs, "key").unwrap_or("tab").to_string();
            let title = attr(attrs, "title-key").unwrap_or("modal").to_string();
            let id = world
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
                            ..Default::default()
                        },
                        BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.94)),
                        BorderColor::all(Color::srgb(0.85, 0.75, 0.45)),
                        Outline::new(Val::Px(2.0), Val::ZERO, Color::srgb(0.85, 0.75, 0.45)),
                        label(title, 22.0, Color::srgb(0.95, 0.85, 0.6)),
                    ));
                })
                .id();
            world.entity_mut(id);
        }
        other => {
            bevy::log::warn!("hud: unhandled element `{other}` — skipped");
        }
    }
}

/// Health/XP/boss/target bar: background + fill + numeric label stacked in
/// the requested corner. Returns the root entity id.
fn bar(
    world: &mut World,
    label_text: &str,
    value: u32,
    max: u32,
    fill: Color,
    (text_size, width): (f32, f32),
) -> Entity {
    let fraction = if max > 0 {
        value as f32 / max as f32
    } else {
        0.0
    };
    let id = world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(10.0),
                width: Val::Px(width),
                height: Val::Px(text_size + 8.0),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.02, 0.02, 0.02, 0.8)),
            Name::new(format!("hud:bar:{label_text}")),
        ))
        .with_children(|bar_node| {
            bar_node.spawn((
                Node {
                    width: Val::Percent(fraction * 100.0),
                    height: Val::Percent(100.0),
                    ..Default::default()
                },
                BackgroundColor(fill),
            ));
        })
        .id();
    world.entity_mut(id).with_children(|bar_node| {
        bar_node.spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(6.0),
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

/// Show the interaction prompt when the player stands near a `<DialogueNPC>`.
pub fn hud_prompt_update(
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<&GlobalTransform, With<DialogueNpc>>,
    mut prompts: Query<&mut Visibility, With<HudPrompt>>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let near = npcs
        .iter()
        .any(|npc| npc.translation().distance(player.translation()) < 3.5);
    for mut visibility in &mut prompts {
        *visibility = if near {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
}
