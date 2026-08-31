//! HUD screen elements: bars, minimap, compass, prompt, modal — built with
//! `bevy_ui` on top of the raw attributes kept in the IR ([`HudElement`]).
//!
//! The `healthbar`/`xpbar` fills are dynamic: [`hud_health_sync`] /
//! [`hud_xp_sync`] mirror the player's [`Health`] / [`Xp`] (see `vitals`)
//! into the fill width and label text every frame, defaulting to 100/100 and
//! 0/100 while the player has no vitals. [`hud_balloon_update`] pops the
//! dialogue balloon on `E` near a `<DialogueNPC>` for a timed 4 s. Layout,
//! boss/target bars and toggle behaviour remain as before.
//!
//! WIRED-BY-ORCHESTRATOR: `hud_health_sync`, `hud_xp_sync` and
//! `hud_balloon_update` must be registered in `src/main.rs` in the `Update`
//! schedule (add them to the existing
//! `app.add_systems(bevy::app::Update, (…))` tuple). This file intentionally
//! does not touch `main.rs`.

use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::DialogueNpc;
use crate::vitals::{Health, Xp, health_fraction, xp_fraction};

/// Marker for the interaction prompt node (shown near a `<DialogueNPC>`).
#[derive(Component)]
pub struct HudPrompt;

/// Marker for the healthbar fill node (width mirrors the player's `Health`).
#[derive(Component)]
pub struct HudHealthFill;

/// Marker for the healthbar label node ("HP {cur}/{max}").
#[derive(Component)]
pub struct HudHealthLabel;

/// Marker for the xpbar fill node (width mirrors the player's `Xp`).
#[derive(Component)]
pub struct HudXpFill;

/// Marker for the xpbar label node ("XP {cur}/{next}").
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

/// Which vitals a bar mirrors, if any (`bossbar`/`targetbar` stay static).
#[derive(Clone, Copy, PartialEq, Eq)]
enum BarVitals {
    Plain,
    Health,
    Xp,
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
                BarSpec {
                    label_text: "HP",
                    value: 100,
                    max: 100,
                    fill: Color::srgb(0.75, 0.15, 0.15),
                    text_size: 18.0,
                    width: 300.0,
                    left_px: 10.0,
                    bottom_px: 10.0,
                    vitals: BarVitals::Health,
                },
            );
        }
        "xpbar" => {
            bar(
                world,
                BarSpec {
                    label_text: "XP",
                    value: 0,
                    max: 100,
                    fill: Color::srgb(0.2, 0.45, 0.8),
                    text_size: 10.0,
                    width: 300.0,
                    left_px: 10.0,
                    bottom_px: 46.0,
                    vitals: BarVitals::Xp,
                },
            );
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
                    vitals: BarVitals::Plain,
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
                    vitals: BarVitals::Plain,
                },
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
                HudBalloon {
                    timer: BALLOON_DURATION,
                },
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
/// the requested corner. Returns the root entity id. `vitals` marks the fill
/// and label nodes so [`hud_health_sync`] / [`hud_xp_sync`] can drive them
/// (plain bars stay static).
/// Layout + conteúdo de uma barra de HUD.
struct BarSpec {
    label_text: &'static str,
    value: u32,
    max: u32,
    fill: Color,
    text_size: f32,
    width: f32,
    left_px: f32,
    bottom_px: f32,
    vitals: BarVitals,
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
        vitals,
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
                height: Val::Px(text_size + 8.0),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.02, 0.02, 0.02, 0.8)),
            Name::new(format!("hud:bar:{label_text}")),
        ))
        .id();
    let mut fill_id = None;
    world.entity_mut(id).with_children(|bar_node| {
        fill_id = Some(
            bar_node
                .spawn((
                    Node {
                        width: Val::Percent(fraction * 100.0),
                        height: Val::Percent(100.0),
                        ..Default::default()
                    },
                    BackgroundColor(fill),
                ))
                .id(),
        );
    });
    let mut label_id = None;
    world.entity_mut(id).with_children(|bar_node| {
        label_id = Some(
            bar_node
                .spawn((
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
                ))
                .id(),
        );
    });
    match vitals {
        BarVitals::Plain => {}
        BarVitals::Health => {
            world
                .entity_mut(fill_id.expect("fill spawned above"))
                .insert(HudHealthFill);
            world
                .entity_mut(label_id.expect("label spawned above"))
                .insert(HudHealthLabel);
        }
        BarVitals::Xp => {
            world
                .entity_mut(fill_id.expect("fill spawned above"))
                .insert(HudXpFill);
            world
                .entity_mut(label_id.expect("label spawned above"))
                .insert(HudXpLabel);
        }
    }
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

// ---------------------------------------------------------- dynamic vitals

/// Label text for the healthbar ("HP {cur}/{max}", hp rounded for display).
pub fn health_label_text(current: f32, max: f32) -> String {
    format!("HP {}/{}", current.round() as i32, max.round() as i32)
}

/// Label text for the xpbar ("XP {cur}/{next}").
pub fn xp_label_text(current: u32, next: u32) -> String {
    format!("XP {current}/{next}")
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
/// WIRED-BY-ORCHESTRATOR: register in `src/main.rs` `Update` (module docs).
pub fn hud_balloon_update(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc)>,
    mut balloons: Query<(&mut Visibility, &mut HudBalloon, &mut Text)>,
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
    for (mut visibility, mut balloon, mut text) in &mut balloons {
        if let Some(id) = &dialogue {
            text.0 = id.clone();
            balloon.timer = BALLOON_DURATION;
            *visibility = Visibility::Visible;
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
        assert_eq!(health_label_text(100.0, 100.0), "HP 100/100");
        assert_eq!(health_label_text(87.5, 100.0), "HP 88/100"); // rounds
        assert_eq!(health_label_text(0.0, 100.0), "HP 0/100");
    }

    #[test]
    fn test_xp_label_text() {
        assert_eq!(xp_label_text(0, 100), "XP 0/100");
        assert_eq!(xp_label_text(30, 150), "XP 30/150");
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
}
