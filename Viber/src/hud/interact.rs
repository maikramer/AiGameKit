//! Interaction widgets: the `[key] prompt` near a `<DialogueNPC>`, the timed
//! dialogue balloon, and key-toggled panels (TabbedModal).

use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::DialogueNpc;

/// Marker for the interaction prompt node (shown near a `<DialogueNPC>`).
#[derive(Component)]
pub struct HudPrompt {
    /// Range in meters at which the prompt appears (authored on the tag).
    pub range: f32,
}

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

/// Dialogue balloon timer: while visible it counts down; when it runs out
/// the balloon hides and its text resets. The TEXT is set on interaction by
/// the dialogue flow.
pub fn hud_balloon_update(
    time: Res<Time>,
    mut balloons: Query<(&mut Visibility, &mut HudBalloon, &Children)>,
    mut texts: Query<&mut Text>,
) {
    let dt = time.delta_secs();
    for (mut visibility, mut balloon, children) in &mut balloons {
        let visible = *visibility == Visibility::Visible;
        if !balloon_tick(&mut balloon.timer, visible, dt) {
            *visibility = Visibility::Hidden;
            if let Some(child) = children.first() {
                if let Ok(mut text) = texts.get_mut(*child) {
                    text.0 = "…".into();
                }
            }
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
