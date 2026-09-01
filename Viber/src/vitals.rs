//! Player vitals: `Health` and `Xp` components plus the debug key driver
//! (`H` −10 HP, `N` full heal, `K` +10 XP) that feeds the dynamic HUD bars.
//!
//! `J` was retired to the hero's melee attack (`combat`); `H`/`K` are free in
//! the native build (the Luau `interacted()` keys are e/j/f/q/r/space only).
//!
//! There is no real combat yet — this is the dynamic-UI pipeline: the keys
//! mutate the vitals, [`crate::hud::hud_health_sync`] /
//! [`crate::hud::hud_xp_sync`] mirror them into the `healthbar`/`xpbar`
//! fills and labels.
//!
//! The player spawn recipe (`recipes::spawn`) does not attach vitals, so
//! [`debug_damage`] *inserts* `Health`/`Xp` on the hero on the first relevant
//! key press (query with `Option` + `Commands::insert`). Until then the HUD
//! sync systems treat a missing component as the default 100/100 / 0/100.
//!
//! WIRED-BY-ORCHESTRATOR: `vitals::debug_damage` must be registered in
//! `src/main.rs` in the `Update` schedule (add `vitals::debug_damage,` to the
//! existing `app.add_systems(bevy::app::Update, (…))` tuple). This module
//! intentionally does not touch `main.rs`.

use bevy::prelude::*;

use crate::player::Player;

/// Default HP pool (also the HUD fallback when no `Health` exists yet).
pub const DEFAULT_HEALTH: f32 = 100.0;
/// Damage per `H` press (debug driver).
pub const DEBUG_DAMAGE: f32 = 10.0;
/// XP needed for the first level (HUD fallback uses the same).
pub const DEFAULT_XP_NEXT: u32 = 100;
/// XP gain per `K` press (debug driver).
pub const DEBUG_XP_GAIN: u32 = 10;

/// Player HP pool, clamped to `0..=max` by [`apply_damage`] / [`heal_full`].
#[derive(Debug, Clone, Copy, PartialEq, Component)]
pub struct Health {
    pub current: f32,
    pub max: f32,
}

impl Default for Health {
    fn default() -> Self {
        Self {
            current: DEFAULT_HEALTH,
            max: DEFAULT_HEALTH,
        }
    }
}

/// XP progress toward the next tier; on level-up the remainder carries over
/// and `next` grows by [`xp_ramp`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Component)]
pub struct Xp {
    pub current: u32,
    pub next: u32,
}

impl Default for Xp {
    fn default() -> Self {
        Self {
            current: 0,
            next: DEFAULT_XP_NEXT,
        }
    }
}

/// Health bar fraction in `0..=1` for the UI fill (`0.0` when `max <= 0`).
pub fn health_fraction(current: f32, max: f32) -> f32 {
    if max <= 0.0 {
        return 0.0;
    }
    (current / max).clamp(0.0, 1.0)
}

/// XP bar fraction in `0..=1` for the UI fill (`0.0` when `next == 0`).
pub fn xp_fraction(current: u32, next: u32) -> f32 {
    if next == 0 {
        return 0.0;
    }
    (current as f32 / next as f32).clamp(0.0, 1.0)
}

/// Applies damage clamped to `0..=max`; negative `amount` (a heal) clamps at
/// `max` as well, so the pool never leaves its range.
pub fn apply_damage(health: &mut Health, amount: f32) {
    health.current = (health.current - amount).clamp(0.0, health.max);
}

/// Full restore (debug `N`).
pub fn heal_full(health: &mut Health) {
    health.current = health.max;
}

/// Next-tier XP requirement after levelling: +50 % rounded up, never 0 —
/// the "ramp" that makes each level cost more than the last.
pub fn xp_ramp(next: u32) -> u32 {
    next.saturating_mul(3).div_ceil(2).max(1)
}

/// Adds XP, overflowing into a ramped next tier while at/over `next`
/// (carrying the remainder, like classic level systems).
pub fn gain_xp(xp: &mut Xp, gain: u32) {
    xp.current = xp.current.saturating_add(gain);
    while xp.next > 0 && xp.current >= xp.next {
        xp.current -= xp.next;
        xp.next = xp_ramp(xp.next);
    }
}

/// Debug vitals driver for the hero: `H` deals [`DEBUG_DAMAGE`], `N` fully
/// heals, `K` gains [`DEBUG_XP_GAIN`]. Inserts missing `Health`/`Xp` on the
/// first relevant press (spawn recipes are intentionally left untouched).
/// `J` belongs to the melee attack (`combat::player_melee_attack`).
///
/// O dano de `H` segue o path único do feedback (`PlayerHurt`: i-frames,
/// vinheta, número flutuante, morte/respawn) — por isso escreve a mensagem
/// em vez de aplicar directo. Necessita `Health` já presente (o melee e os
/// scripts inserem via `ensure_player_vitals`).
///
/// WIRED-BY-ORCHESTRATOR: registered in `src/main.rs` (`vitals::debug_damage`
/// in the `Update` schedule).
#[allow(clippy::type_complexity)]
pub fn debug_damage(
    keys: Res<ButtonInput<KeyCode>>,
    mut commands: Commands,
    mut players: Query<(Entity, Option<&mut Health>, Option<&mut Xp>), With<Player>>,
    mut hurts: bevy::ecs::message::MessageWriter<crate::feedback::PlayerHurt>,
) {
    let Ok((entity, mut health, mut xp)) = players.single_mut() else {
        return;
    };

    if keys.just_pressed(KeyCode::KeyH) {
        if health.is_none() {
            commands.entity(entity).insert(Health::default());
        }
        hurts.write(crate::feedback::PlayerHurt {
            amount: DEBUG_DAMAGE,
            status: false,
        });
    }
    if keys.just_pressed(KeyCode::KeyN) {
        match health.as_mut() {
            Some(hp) => heal_full(hp),
            None => {
                commands.entity(entity).insert(Health::default());
            }
        }
    }
    if keys.just_pressed(KeyCode::KeyK) {
        match xp.as_mut() {
            Some(x) => gain_xp(x, DEBUG_XP_GAIN),
            None => {
                let mut fresh = Xp::default();
                gain_xp(&mut fresh, DEBUG_XP_GAIN);
                commands.entity(entity).insert(fresh);
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
    fn test_health_fraction_basic() {
        assert!(approx(health_fraction(75.0, 100.0), 0.75));
        assert!(approx(health_fraction(0.0, 100.0), 0.0));
        assert!(approx(health_fraction(100.0, 100.0), 1.0));
    }

    #[test]
    fn test_health_fraction_guards() {
        // max <= 0 must not produce NaN/inf (bar would vanish or explode)
        assert!(approx(health_fraction(50.0, 0.0), 0.0));
        assert!(approx(health_fraction(50.0, -10.0), 0.0));
        // current outside the pool clamps into 0..=1
        assert!(approx(health_fraction(120.0, 100.0), 1.0));
        assert!(approx(health_fraction(-5.0, 100.0), 0.0));
    }

    #[test]
    fn test_apply_damage_clamps_at_zero() {
        let mut hp = Health {
            current: 25.0,
            max: 100.0,
        };
        for _ in 0..4 {
            apply_damage(&mut hp, DEBUG_DAMAGE);
        }
        assert!(approx(hp.current, 0.0), "clamps at 0, got {}", hp.current);
        // extra hits stay at 0 — never negative
        apply_damage(&mut hp, DEBUG_DAMAGE);
        assert!(approx(hp.current, 0.0));
    }

    #[test]
    fn test_heal_full_and_overheal_clamp_at_max() {
        let mut hp = Health {
            current: 30.0,
            max: 100.0,
        };
        heal_full(&mut hp);
        assert!(approx(hp.current, 100.0));
        // negative damage = heal, also clamped at max
        apply_damage(&mut hp, -50.0);
        assert!(approx(hp.current, 100.0));
    }

    #[test]
    fn test_gain_xp_accumulates() {
        let mut xp = Xp::default();
        for _ in 0..3 {
            gain_xp(&mut xp, DEBUG_XP_GAIN);
        }
        assert_eq!(
            xp,
            Xp {
                current: 30,
                next: 100
            }
        );
    }

    #[test]
    fn test_gain_xp_ramps_next_tier() {
        // 95/100 + 10 → level up: remainder 5 carries, next ramps +50 % → 150
        let mut xp = Xp {
            current: 95,
            next: 100,
        };
        gain_xp(&mut xp, 10);
        assert_eq!(
            xp,
            Xp {
                current: 5,
                next: 150
            }
        );
        // keep going to the second ramp: 5 + 145 → current 0 at tier 150,
        // next ramps again to 225
        gain_xp(&mut xp, 145);
        assert_eq!(
            xp,
            Xp {
                current: 0,
                next: 225
            }
        );
    }

    #[test]
    fn test_xp_fraction_guards() {
        assert!(approx(xp_fraction(30, 100), 0.3));
        // division guard: next == 0 → 0.0, never NaN/inf
        assert!(approx(xp_fraction(10, 0), 0.0));
        // clamped into 0..=1
        assert!(approx(xp_fraction(200, 100), 1.0));
    }

    #[test]
    fn test_xp_ramp_never_zero() {
        assert_eq!(xp_ramp(100), 150);
        assert_eq!(xp_ramp(150), 225);
        assert_eq!(xp_ramp(1), 2); // +50 % rounds up, min 1
    }
}
